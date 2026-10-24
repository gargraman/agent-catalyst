# 02 (LLD) — Provider Gateway (hard core #1)

> **Parent:** [`../02-provider-gateway.md`](../02-provider-gateway.md) (IR, adapter Protocol, profiles,
> `compute_cost`, the retry/fallback/breaker *sketch*).
> The parent fully specifies the IR dataclasses and `compute_cost`; it leaves `resolve_profile` as `...`,
> the `CircuitBreaker`/`retry_policy`/`attach_cost`/`req_fallback` helpers named-only, the error
> classification as prose ("429/5xx/timeout"), and the substitution rules as one example. This doc makes
> those buildable. A **working reference implementation** of the adapter contract already exists in
> `mvp/providers/{base,openai_adapter,ollama_adapter}.py` (the viability MVP) — §6 points at it.

## What this adds over parent `02`

| Gap in `02` | Filled here |
|---|---|
| `resolve_profile(...) -> CapabilityProfile: ...` (load+cache unspecified) | §2 — file-naming key, in-process cache, hot-reload + invalidation, the snapshot-vs-live rule |
| `RetryableProviderError`/`NonRetryableError` named; classification is prose | §3 — the exception hierarchy + a status/exception → category table |
| `retry_policy(max=3, base=0.5, jitter=True)` + `attempt.last` used, undefined | §4 — the generator, backoff math, `Retry-After` handling |
| `breaker` used; "opens after N … half-opens after a cooldown … State in Redis" — N, cooldown, keys, atomicity all unspecified | §5 — the `CircuitBreaker` class, N/cooldown/half-open, Redis key schema, atomic failure count |
| `supports()` substitution logic shown once | §6 — the full feature→action rule set + the adapter `stream()` obligations (+ mvp reference) |
| `compute_cost` returns `float`; `runs.cost_usd` is `numeric(12,6)` (01) — quantization undefined | §7 — the Decimal quantization point + rounding mode |
| `§6` fallback calls `resolve_profile(fb…)` **live**, contradicting "snapshot so a profile change can't alter a published agent" | §8 — the decision (snapshot the fallback profile too) |

Python 3.12, `from __future__ import annotations`. Snippets compile (§9). Types `NormalizedRequest`,
`NormalizedUsage`, `Substitution`, `CapabilityProfile`, `compute_cost`, the adapter Protocol: **see `02` — not
restated.** `ModelConfig` / `VersionSnapshot`: see [`01-data-model.lld.md`](01-data-model.lld.md).

---

## 1. `build_normalized_request` — snapshot → IR

`03 §4` `model_call` calls `build_normalized_request(snapshot, messages)`; `02 §2` defines the target
`NormalizedRequest`. This is the mapping (ties the `01` LLD `ModelConfig` to the `02` IR):

```python
from __future__ import annotations
from runtime.providers.ir import NormalizedRequest   # 02 §2


def build_normalized_request(snapshot, messages: list[dict]) -> NormalizedRequest:
    mc = snapshot.model_config            # 01 LLD ModelConfig
    io = snapshot.io_contract
    return NormalizedRequest(
        provider=mc.provider, model=mc.model, api_version=mc.api_version,
        messages=messages, system=snapshot.system_prompt or None,
        params=_params_for_profile(mc.params, snapshot.profile),      # §1.1 validate/clamp vs profile
        tools=[b.schema_snapshot for b in snapshot.tool_bindings],   # frozen JSON-schema tool defs
        tool_choice=None,                                            # model decides; forced choice is a binding concern
        response_format=_response_format(io, snapshot.profile),      # only if profile.feature("structured_output")
        reasoning=_reasoning(mc.params, snapshot.profile),           # only if profile.feature("reasoning_control")
        cache_hints=_cache_hints(snapshot),                          # 07 — shape owned there
        stream=True,
    )
```

### 1.1 Params validation vs profile (the "validated vs profile" in `02 §2`)

Clamp, don't silently pass. The profile's `limits` bound the request:

```python
def _params_for_profile(params, profile) -> dict:
    out = dict(temperature=params.temperature, max_tokens=params.max_tokens)
    if params.top_p is not None:
        out["top_p"] = params.top_p
    cap = profile.limits.get("max_output_tokens")
    if cap and out["max_tokens"] > cap:
        out["max_tokens"] = cap          # clamp to the model's real ceiling (02 profile limits)
    return out                           # temperature stays as-authored; a model that rejects it → §6 runtime substitution
```

> Rule: **clamp** values that have a hard ceiling (`max_tokens` > profile cap); **pass through** values the
> model may reject at call time (`temperature` on a reasoning model) and let the adapter record a
> `Substitution` when the provider 400s (§6, the mvp OpenAI adapter does exactly this). Never silently drop.

---

## 2. `resolve_profile` — load + cache (fills `02 §4`)

```python
from __future__ import annotations
import json, threading
from pathlib import Path
from runtime.providers.profiles import CapabilityProfile      # 02 §4

_PROFILE_DIR = Path(__file__).parent / "profiles"
_CACHE: dict[str, CapabilityProfile] = {}
_LOCK = threading.Lock()


def _profile_key(provider: str, model: str, api_version: str | None) -> str:
    # file convention (02 §4): provider__model__apiversion.json; apiversion omitted ⇒ provider__model.json
    base = f"{provider}__{model}"
    return f"{base}__{api_version}" if api_version else base


def resolve_profile(provider: str, model: str, api_version: str | None = None) -> CapabilityProfile:
    key = _profile_key(provider, model, api_version)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    with _LOCK:                                   # double-checked: one loader wins under concurrent first-touch
        if key in _CACHE:
            return _CACHE[key]
        path = _PROFILE_DIR / f"{key}.json"
        if not path.exists():
            # CI staleness gate (02 §4.2) should prevent this in prod; at runtime it's a hard config error,
            # NOT a silent default — an unknown model must not be billed at a guessed rate.
            raise ProfileNotFound(provider, model, api_version)
        prof = _load(path)
        _CACHE[key] = prof
        return prof


def invalidate_profile_cache() -> None:
    """Called by the hot-reload signal (SIGHUP / admin endpoint) after a profile PR merges (02 §4.2)."""
    with _LOCK:
        _CACHE.clear()
```

- **Cache:** process-local dict; profiles are small and change on a PR cadence (`02 §4.2`), so a full clear on
  reload is fine — no per-key TTL needed.
- **Hot-reload:** `invalidate_profile_cache()` on a signal; the next `resolve_profile` reloads from disk.
- **The snapshot rule (load-bearing):** `resolve_profile` returns the **live** file. A published version does
  **not** re-resolve — it carries `agent_versions.profile_snapshot` frozen at freeze time (`02 §4`), so a
  pricing/feature edit becomes a *drift trigger*, not a silent change. `resolve_profile` is used (a) at
  **freeze** to populate the snapshot, (b) for the **eval** candidate when re-evaluating under a new profile,
  and (c) the fallback path — see §8 for the one place the parent gets this wrong.

---

## 3. Error classification

```python
class ProviderError(Exception):
    """Base for provider-call failures (NOT an AppError — caught inside the gateway, 00-conventions §1 cat. B)."""

class RetryableProviderError(ProviderError):
    """429 / 5xx / timeout — the retry loop (§4) may try again; honors Retry-After if present."""
    def __init__(self, msg: str, *, retry_after_s: float | None = None):
        super().__init__(msg); self.retry_after_s = retry_after_s

class NonRetryableError(ProviderError):
    """4xx that won't change on retry: auth, bad request, content filter. Fail straight through."""

class ProfileNotFound(NonRetryableError):
    def __init__(self, provider, model, api_version):
        super().__init__(f"no capability profile for {provider}/{model}/{api_version}")

# ProviderUnavailable (provider + all fallbacks exhausted) is the ONE that escapes to HTTP (502) — it is an
# AppError, defined in 00-conventions.lld.md §1.2, raised by gateway.stream() (02 §6), not here.
```

**Classification table** — each adapter maps its transport's failure to one category. By HTTP status
(LiteLLM and native SDKs all expose it):

| Signal | Category | Why |
|---|---|---|
| HTTP 429 | `RetryableProviderError(retry_after_s=…)` | rate limit; back off + honor `Retry-After` |
| HTTP 500–599 | `RetryableProviderError` | transient server-side |
| connect/read timeout, conn reset | `RetryableProviderError` | transient network |
| HTTP 408 | `RetryableProviderError` | request timeout |
| HTTP 401 / 403 | `NonRetryableError` | bad/expired key — retry won't fix; surfaces as 502 after fallback |
| HTTP 400 / 422 | `NonRetryableError` | malformed request (unless it's the **recoverable param-reject** case → §6, adapter drops the param + records a `Substitution` and retries *once*, not via `retry_policy`) |
| HTTP 400 content-filter / safety block | `NonRetryableError` | provider refused; a run-level outcome, not a retry |
| 404 model-not-found | `NonRetryableError` | config/profile error |

The adapter owns this mapping because only it knows its transport's exception shape (`anthropic.*`,
`openai.*`, LiteLLM's unified error). The **recoverable param-reject** (e.g. a reasoning model rejecting
`temperature`) is deliberately *not* a `retry_policy` retry — it's a one-shot in-adapter re-request with the
offending param dropped and a `Substitution` recorded (the mvp OpenAI adapter, `§6`).

---

## 4. `retry_policy` — backoff generator

```python
from __future__ import annotations
import random, time
from dataclasses import dataclass


@dataclass
class Attempt:
    n: int           # 1-based
    last: bool       # True on the final allowed attempt → caller re-raises instead of looping


def retry_policy(*, max: int = 3, base: float = 0.5, jitter: bool = True):
    """Yields Attempt objects; sleep happens on the *consumer* side only after a RetryableProviderError,
    so a success returns immediately. Exponential: base * 2**(n-1), full jitter, capped."""
    for n in range(1, max + 1):
        yield Attempt(n=n, last=(n == max))


def backoff_delay(attempt: Attempt, *, base: float, jitter: bool, retry_after_s: float | None) -> float:
    if retry_after_s is not None:
        return retry_after_s                      # 429 Retry-After always wins (02 §6)
    raw = min(base * (2 ** (attempt.n - 1)), 8.0) # cap at 8s so 3 tries never blow ctx.deadline_s (03 §5)
    return random.uniform(0, raw) if jitter else raw
```

Usage reconciles with `02 §6`'s loop (`for attempt in retry_policy(...)` / `if attempt.last: raise`): on a
`RetryableProviderError`, the gateway `await asyncio.sleep(backoff_delay(attempt, …, retry_after_s=e.retry_after_s))`
before the next iteration; on `attempt.last`, it re-raises to fall through to the breaker/fallback path. The
8s cap × 3 attempts is bounded so retries can't by themselves breach the 30s run deadline.

---

## 5. `CircuitBreaker` (fills `02 §6`'s "State in Redis")

Per `(provider, model)`: open after **N=5** consecutive failures; stay open for a **cooldown=30s**; then
**half-open** (allow one trial) — a success closes it, a failure re-opens for another cooldown. State in
Redis so `api` + `worker` replicas share one view.

```python
from __future__ import annotations
import time

N_FAILURES, COOLDOWN_S = 5, 30.0


class CircuitBreaker:
    def __init__(self, redis): self.redis = redis

    def _k(self, provider: str, model: str) -> str:
        return f"breaker:{provider}:{model}"      # hash: {fails, opened_at}

    async def is_open(self, provider: str, model: str) -> bool:
        st = await self.redis.hgetall(self._k(provider, model))
        opened_at = float(st.get("opened_at", 0) or 0)
        if not opened_at:
            return False
        if time.time() - opened_at >= COOLDOWN_S:
            return False                          # cooldown elapsed → half-open (one trial allowed through §6)
        return True                               # still open → skip straight to fallback

    async def record_failure(self, provider: str, model: str) -> None:
        k = self._k(provider, model)
        # atomic: INCR fails; open when it crosses N. HINCRBY is atomic across replicas.
        fails = await self.redis.hincrby(k, "fails", 1)
        if fails >= N_FAILURES:
            await self.redis.hset(k, "opened_at", time.time())
        await self.redis.expire(k, int(COOLDOWN_S * 4))   # self-heal: stale state can't wedge a provider forever

    async def record_success(self, provider: str, model: str) -> None:
        await self.redis.delete(self._k(provider, model))  # close + reset the counter
```

- **Atomicity:** `HINCRBY` is atomic, so concurrent replicas can't lose a failure increment. The `opened_at`
  set after crossing N is idempotent (re-setting the same timestamp is harmless).
- **Half-open:** modeled by `is_open` returning `False` once `cooldown` elapsed — the next call is the trial;
  its `record_success`/`record_failure` closes or re-opens. (A stricter single-flight half-open — only *one*
  trial in flight — is a P2 refinement; at current scale the cooldown + N bound the hammering adequately.)
- **Self-heal:** the `expire` means an abandoned breaker key clears itself, so a one-off blip never
  permanently routes a `(provider, model)` to fallback.

---

## 6. Adapter contract + the substitution rule set

The parent gives the `ProviderAdapter` Protocol (`supports()` + `stream()`) and one substitution example.
Here are the full obligations. **Reference implementation: `mvp/providers/openai_adapter.py`** implements this
exact contract against a real OpenAI endpoint (preflight subs, SSE parse, runtime param-reject substitution,
USAGE event) — treat it as the worked example; `ollama_adapter.py` is the second provider that proves the
abstraction holds.

**`supports(profile, req) -> list[Substitution]`** — preflight: compare the request's feature use against
`profile.features`; return a `Substitution` for each downgrade. The full rule set:

| Request uses | Needs `profile.feature(...)` | If absent → `Substitution(feature, action, …)` |
|---|---|---|
| `req.response_format` (structured output) | `structured_output` | `("structured_output", "emulated_via_prompt", "JSON enforced via instruction, not API")` |
| `req.tool_choice` is a `dict` (forced tool) | `forced_tool_choice` | `("forced_tool_choice", "emulated_via_prompt", "tool forced via prompt, not tool_choice")` |
| `req.reasoning` (`{effort\|budget}`) | `reasoning_control` | `("reasoning_control", "dropped", "effort/budget not controllable on this model")` |
| `req.cache_hints` | `prompt_caching` | `("prompt_caching", "dropped", "prompt-cache breakpoints unsupported")` |

**`stream(req, *, api_key, profile)`** obligations:
1. Translate `req` → provider wire body (the one place a provider SDK type appears).
2. Yield `NormalizedStreamEvent`s as they arrive: `TEXT_DELTA`, `REASONING_DELTA`, `TOOL_CALL_*`, then a
   **final `USAGE` event** carrying `NormalizedUsage` with **raw** token counts — cost is attached by the
   gateway, never the adapter (`02 §1`).
3. **Runtime substitutions:** a downgrade discovered *at call time* (not preflight) — the param-reject case —
   is dropped + recorded and attached to the final `USAGE` event's substitutions. The adapter retries **once**
   in-band (not through `retry_policy`); see the mvp OpenAI adapter's `temperature` handling.
4. Classify any transport failure into §3's categories before it leaves `stream()`.

`action` vocabulary (from `02 §5` `Substitution.action`): `dropped` | `emulated_via_prompt` | `downgraded`.
The gateway emits these as span attrs + attaches them to the Run; eval treats a substitution as a quality
risk (`02 §5`, and `capability_substitution` is a `DRIFT_TRIGGER`, `05 §5`).

---

## 7. Cost quantization (float → `numeric(12,6)`)

`compute_cost` (`02 §4.1`) returns a Python `float`; `runs.cost_usd` / `spans.cost_usd` are `numeric(12,6)`
(`01 §8`, "never float for money"). The boundary needs one explicit quantization, or rounding drifts between
the computed value, the DB value, and the hand-calc test (`02 §8`).

```python
from __future__ import annotations
from decimal import Decimal, ROUND_HALF_EVEN

_CENTS6 = Decimal("0.000001")                 # numeric(12,6) scale


def quantize_usd(value: float | Decimal) -> Decimal:
    """Single quantization point. compute_cost (02 §4.1) returns float; convert ONCE here, at the moment the
    cost is attached to a span/run — never re-quantize downstream (summing already-quantized values is exact)."""
    return Decimal(str(value)).quantize(_CENTS6, rounding=ROUND_HALF_EVEN)


def attach_cost(usage_event, profile) -> "NormalizedStreamEvent":
    """02 §6 names attach_cost(ev, profile). It calls compute_cost then quantizes onto the USAGE event."""
    raw = compute_cost(profile, usage_event.data["usage"])        # 02 §4.1 (float)
    usage_event.data["usage"] = replace_cost(usage_event.data["usage"], quantize_usd(raw))
    return usage_event
```

Rules: **`Decimal(str(float))`** (not `Decimal(float)` — the latter carries binary noise), **`ROUND_HALF_EVEN`**
(banker's rounding, the Postgres `numeric` default), quantize **once** at attach time. `NormalizedUsage.cost_usd`
stays a float in the IR (`02 §2`) for the hot path; the `Decimal` is what's persisted. The `02 §8` cost test
asserts `quantize_usd(compute_cost(...))` equals the hand-calc at 6dp.

---

## 8. Fallback profile resolution (a parent inconsistency, resolved)

`02 §6` fallback does `resolve_profile(fb.provider, fb.model)` — a **live** resolve. But the whole point of
`profile_snapshot` is that a published version's cost/behavior can't shift under a live profile edit
(`02 §4`). The fallback model's profile is **not** in the version snapshot today (only the primary's is), so a
fallback run would be costed against a *live* profile — a silent inconsistency with the immutability guarantee.

> ⚠️ **Amendment to `01 §4.2` + `02 §4/§6`.** When a version declares `model_config.fallback`, freeze the
> **fallback model's resolved profile** into the snapshot too — e.g. `agent_versions.profile_snapshot` becomes
> `{"primary": {...}, "fallback": {...}}` (or a sibling `fallback_profile_snapshot`). Then the §6 fallback path
> reads `snapshot.fallback_profile` instead of `resolve_profile(fb…)` live, and a fallback run is costed and
> capability-checked under the same immutability rule as the primary. A fallback-model profile edit then
> becomes a drift trigger like any other. Flagged for the `01`/`02` reconciliation pass, not edited here.
> (`VersionSnapshot` in `01-data-model.lld.md §1` should gain `fallback_profile: CapabilityProfile | None`.)

Until adopted, document the current behavior honestly: **fallback runs use the live fallback profile**, so a
fallback-model price change is reflected immediately (not frozen) — the one place the snapshot guarantee has a
hole.

---

## 9. Verification performed

1. **Snippet compile** — every `python` block via `compile()` (same harness as `01`/`00` LLD).
2. **Cross-ref** — against `02` read in full: IR (`§2`), profile JSON + `CapabilityProfile` (`§4`),
   `compute_cost` (`§4.1`), the `§6` loop (`retry_policy`/`breaker`/`attach_cost`/`req_fallback` names),
   `Substitution` (`§5`); `01 §8` money type; `03 §4/§5` deadline bound; `05 §5` `capability_substitution`
   trigger; `00-conventions.lld.md §1` for `ProviderUnavailable`/category-B classification.
3. **Reference cross-check** — the §6 adapter contract matches the working `mvp/providers/openai_adapter.py`
   (`supports()` preflight subs, SSE `stream()`, runtime temperature-reject `Substitution`, final USAGE event)
   and `ollama_adapter.py` — verified live in the earlier `/verify` run (H2 produced correct substitutions +
   cost).
4. **One flagged amendment** — the fallback-profile snapshot hole (§8), recorded as a callout + a note that
   `VersionSnapshot` gains `fallback_profile`, not a silent parent edit.

→ Next LLD: `05-eval-drift.lld.md` (subtask 4, the second defensible core).
