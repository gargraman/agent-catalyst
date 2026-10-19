# 02 — Provider Gateway (hard core #1)

> Implements `spec.md §B2`. The gateway is the one place that knows a provider exists. Above it,
> everything speaks one normalized dialect; below it, each provider's quirks are contained. This is where
> model-agnosticism either holds or quietly leaks — and where cost accuracy is won or lost.

## 1. What the gateway owns (and what it refuses to trust)

Owns: the normalized request/response IR, per-provider adapters, **our own cost computation**, retry/
fallback/circuit-breaking, and recording capability substitutions.

Refuses to trust: **LiteLLM's cost numbers and its feature flattening.** LiteLLM is excellent transport
(one call shape across 100+ providers) and gives us raw token usage — but its pricing table lags, and
its unified wrapper hides provider-specific things (prompt caching tokens, reasoning tokens, forced tool
choice rules) that drift (05) and COGS (07) depend on.

> **Delta from spec (00 §7.1):** spec says "LiteLLM (unified call + cost)". This design uses LiteLLM for
> **transport + raw usage**, and computes **$ ourselves from versioned capability profiles**. For
> Anthropic and OpenAI — where cached/reasoning-token accounting materially changes cost — the adapter
> may call the **native SDK directly** behind the same `ProviderAdapter` Protocol. The framework is
> never the source of truth for cost or capability.

## 2. The IR (intermediate representation)

Every caller builds a `NormalizedRequest`; every provider streams `NormalizedStreamEvent`s. Nothing
above the gateway imports a provider SDK type.

```python
# services/runtime/providers/ir.py
from dataclasses import dataclass, field
from enum import Enum

@dataclass(frozen=True)
class NormalizedRequest:
    provider: str
    model: str
    api_version: str | None
    messages: list[dict]                 # role/content, normalized
    system: str | None
    params: dict                         # temperature, max_tokens, top_p, …(validated vs profile)
    tools: list[dict] = field(default_factory=list)      # JSON-schema tool defs
    tool_choice: str | dict | None = None
    response_format: dict | None = None  # structured-output JSON schema, if the profile supports it
    reasoning: dict | None = None        # {effort|budget} — only if profile.reasoning_control
    cache_hints: list[dict] = field(default_factory=list)  # prompt-cache breakpoints (07)
    stream: bool = True

class EventType(str, Enum):
    TEXT_DELTA = "text_delta"
    REASONING_DELTA = "reasoning_delta"
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_DELTA = "tool_call_delta"
    TOOL_CALL_END = "tool_call_end"
    USAGE = "usage"
    STOP = "stop"
    ERROR = "error"

@dataclass(frozen=True)
class NormalizedStreamEvent:
    type: EventType
    data: dict                           # payload by type: TEXT_DELTA→{"text"}, TOOL_CALL_*→{"id","name",
                                         # "args"|"args_delta"}, USAGE→{"usage": NormalizedUsage},
                                         # ERROR→{"error"}. Consumers (03, 10) read these keys.

@dataclass(frozen=True)
class NormalizedUsage:
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0         # cache READS — priced at the discounted rate (07)
    cache_write_tokens: int = 0          # cache SEEDING — priced at the cache_write premium (07)
    reasoning_tokens: int = 0
    cost_usd: float = 0.0                # WE compute this (§4), not the provider/LiteLLM
```

## 3. The adapter Protocol

One interface, N implementations. Adapters translate IR ⇄ provider wire format and normalize usage.

```python
# services/runtime/providers/adapters/base.py
from typing import Protocol, AsyncIterator

class ProviderAdapter(Protocol):
    provider: str
    def supports(self, profile: "CapabilityProfile", req: NormalizedRequest) -> list["Substitution"]:
        """Return the downgrades/rewrites needed to make req runnable on this model (may be empty)."""
    async def stream(self, req: NormalizedRequest, *, api_key: str,
                     profile: "CapabilityProfile") -> AsyncIterator[NormalizedStreamEvent]:
        """Call the provider (via LiteLLM or native SDK) and yield normalized events incl. a final
        USAGE event with raw token counts. Cost is attached by the gateway, not here."""
```

Implementations: `AnthropicAdapter` (native SDK — precise cache/reasoning usage), `OpenAIAdapter`
(native SDK), `LiteLLMAdapter` (catch-all for Google/Mistral/self-host/Bedrock/Azure). Registry:

```python
# services/runtime/providers/adapters/__init__.py
ADAPTERS: dict[str, ProviderAdapter] = {
    "anthropic": AnthropicAdapter(),
    "openai":    OpenAIAdapter(),
    # everything else routes through LiteLLM behind the same Protocol:
    "_default":  LiteLLMAdapter(),
}
def adapter_for(provider: str) -> ProviderAdapter:
    return ADAPTERS.get(provider, ADAPTERS["_default"])
```

## 4. Capability profiles — versioned data, not code

A profile per `(provider, model, api_version)`: pricing + feature flags. These are **data files**
(`providers/profiles/*.json`), hot-reloadable, and a resolved copy is **snapshotted into each
AgentVersion** (`agent_versions.profile_snapshot`, 01) so a profile change can't silently alter a
published agent — it becomes a drift trigger (05).

```jsonc
// services/runtime/providers/profiles/anthropic__claude-opus-5-5.json
{
  "provider": "anthropic",
  "model": "claude-opus-5-5",
  "api_version": "2026-01",
  "availability": "active",            // active | deprecated | retired — the model-deprecation detector (16 §3)
  "sunset_at": null,                   //   diffs this against every published version's profile_snapshot.model
                                       //   and notifies the owner. Only as current as the profile itself
                                       //   (the profile-currency maintenance gap gates it).
  "pricing_usd_per_mtok": {            // matches frontend MODELS (data.ts)
    "input": 4.0, "output": 20.0,
    "cached_input": 0.4,              // 90% off — the cache-savings lever (07)
    "cache_write": 5.0,              // one-time write premium when seeding the cache (07)
    "reasoning": 20.0,
    "batch_multiplier": 0.5          // Batch API ~50% off — applied by compute_cost (§4.1) for eval/ingest
  },
  "limits": { "context_tokens": 1000000, "max_output_tokens": 64000 },
  "features": {
    "structured_output": true,
    "forced_tool_choice": true,
    "reasoning_control": true,         // {effort|budget}
    "prompt_caching": true,
    "tool_call_format": "anthropic",
    "zdr_eligible": true               // feeds retention/compliance (09 §11)
  }
}
```

```python
# services/runtime/providers/profiles/__init__.py
@dataclass(frozen=True)
class CapabilityProfile:
    provider: str; model: str; api_version: str | None
    pricing: dict; limits: dict; features: dict
    availability: str = "active"               # active | deprecated | retired (16 §3)
    sunset_at: str | None = None               # ISO date the provider retires the model; None = none announced
    def feature(self, k: str) -> bool: return bool(self.features.get(k))

def resolve_profile(provider, model, api_version=None) -> CapabilityProfile: ...  # load + cache
```

### 4.1 Cost computation (we do it, every call)

```python
# services/runtime/providers/cost.py
def compute_cost(profile: CapabilityProfile, u: NormalizedUsage, *, batch: bool = False) -> float:
    """The single authority for cost. `batch=True` (eval/ingest via Batch API, 05/07) applies the
    profile's batch_multiplier; cache writes/reads are priced at their own rates. Interactive paths
    (playground, published invoke) always pass batch=False."""
    p = profile.pricing  # USD per 1M tokens
    billable_input = u.input_tokens - u.cached_input_tokens - u.cache_write_tokens
    mult = p.get("batch_multiplier", 1.0) if batch else 1.0
    return mult * (
        billable_input        * p["input"]        / 1e6
      + u.cached_input_tokens * p.get("cached_input", p["input"]) / 1e6
      + u.cache_write_tokens  * p.get("cache_write",  p["input"]) / 1e6
      + u.output_tokens       * p["output"]       / 1e6
      + u.reasoning_tokens    * p.get("reasoning", p["output"]) / 1e6
    )
```

This single function is the authority for `runs.cost_usd`, `spans.cost_usd`, eval cost deltas, and the
COGS dashboards (07, 10). It is unit-tested against hand-calculated fixtures per profile
(`spec.md §D`), and periodically **reconciled against provider billing** (drift in our pricing table is
itself an alert, 10).

### 4.2 Profile currency — the maintenance process (not just the files)

Profiles are hand-authored JSON, so cost accuracy is only as good as how current they are kept. The
"accurate cost + safe downgrade" promise depends on this process existing, not on the files alone:

- **Owner + source of truth.** The profiles directory has a **named owning team** (the platform team) and
  a `CODEOWNERS` entry; every profile carries `source_url` + `verified_at` pointing at the provider's
  official pricing/model page. A change is a reviewed PR, never an ad-hoc edit.
- **Add/change cadence.** Providers ship models and reprice on their own schedule, so currency is
  **event-driven, not calendar-driven**: a watch on provider pricing/model-release pages (manual
  subscription in P1; a scraper/API feed in P2) opens a profile PR. Target SLA: a new model or price in
  use by any agent has a verified profile within **one business week**.
- **CI staleness gate.** CI fails if (a) any profile referenced by a live `agent_versions.profile_snapshot`
  has no file, (b) a `verified_at` is older than **90 days** (stale → warn), or (c) a model id appears in
  usage logs with no profile. This stops silent cost drift from an un-updated table.
- **Reconciliation loop.** A scheduled job compares our computed `runs.cost_usd` (summed per provider)
  against the provider's actual invoice; a variance beyond tolerance opens a profile-review alert (10).
  Reconciliation **detects** drift; this process is what **fixes** it.
- **Deprecation feeds P0-3.** The `availability`/`sunset_at` fields (§4) are part of the same PR workflow;
  marking a model `deprecated`/`retired` here is what the model-deprecation detector reads (16 §3). So
  profile currency gates both cost accuracy *and* deprecation notifications.

> Honest limit: this is a **manual-first** process. It reduces, not eliminates, the staleness risk —
> the CI gate makes staleness loud rather than silent, but a provider price change still isn't reflected
> until someone merges the PR. Automating the provider feed is P2.

## 5. Capability substitution (the leak, made visible)

When a request uses a feature a target model lacks, the adapter **downgrades and records it** rather
than silently dropping it. `supports()` returns the substitutions; the gateway emits them as a span
attr and attaches them to the Run — surfaced to the user and to eval (which treats a substitution as a
quality risk).

```python
@dataclass(frozen=True)
class Substitution:
    feature: str          # "structured_output" | "forced_tool_choice" | "reasoning_control" | …
    action: str           # "dropped" | "emulated_via_prompt" | "downgraded"
    detail: str

# Example: a version pinned to structured output is swapped to a model without it →
#   Substitution("structured_output", "emulated_via_prompt", "JSON enforced via instruction, not API")
```

> **Version portability rule (spec B2):** if a version opts into a provider-specific feature, switching
> its provider marks it **non-portable for that feature** → the UI forces a new version + re-eval. A
> substitution on a *published* run raises a warning; a substitution discovered at *eval* time counts
> against the verdict.

## 6. Retry, fallback, circuit breaker (resilience)

```python
# services/runtime/providers/gateway.py
async def stream(req, *, api_key, profile) -> AsyncIterator[NormalizedStreamEvent]:
    adapter = adapter_for(req.provider)
    subs = adapter.supports(profile, req)
    req = apply_substitutions(req, subs)              # §5
    for attempt in retry_policy(max=3, base=0.5, jitter=True):   # 429/5xx/timeout only
        if breaker.is_open(req.provider, req.model):
            break                                    # skip straight to fallback
        try:
            async for ev in adapter.stream(req, api_key=api_key, profile=profile):
                if ev.type == EventType.USAGE:
                    ev = attach_cost(ev, profile)    # §4.1
                yield ev
            breaker.record_success(req.provider, req.model)
            return
        except RetryableProviderError as e:
            breaker.record_failure(req.provider, req.model)
            if attempt.last: raise
        except NonRetryableError:   # auth, bad request, content filter
            raise
    # exhausted or breaker open → versioned fallback model (spec A0 #2, #12)
    if fb := req_fallback(req):                       # from model_config.fallback (01)
        yield FallbackStarted(fb)                     # recorded on the Run
        async for ev in stream(fb, api_key=api_key, profile=resolve_profile(fb.provider, fb.model)):
            yield ev
    else:
        raise ProviderUnavailable(req.provider)
```

- **Retry**: exponential backoff + jitter, only on 429/5xx/timeouts; cap 3; respects `Retry-After`.
- **Circuit breaker**: per `(provider, model)`, opens after N consecutive failures, half-opens after a
  cooldown — stops hammering a down provider and fails fast to fallback. State in Redis (shared across
  `api`/`worker` replicas).
- **Fallback**: the version's pinned fallback model; the switch is recorded as a substitution-like
  event on the Run so cost/quality attribution stays honest.
- **Idempotency**: published invoke dedupes on `idempotency_key` (01 `runs`) so a client retry of a
  partially-streamed call doesn't double-bill.

## 7. Streaming contract (to the graph and SSE)

The graph (03) consumes `NormalizedStreamEvent`s and re-emits them; the HTTP layer (08) encodes them as
SSE frames 1:1. Because text/reasoning/tool-call/usage are all normalized here, the Playground renders
the same event types regardless of provider — the model-agnostic promise, kept at the stream level, not
just the request level.

## 8. Test strategy (the contract that proves abstraction holds)

Per `spec.md §D`, the headline Phase-1 test lives here:
- **Cross-provider contract test:** the same `NormalizedRequest` runs on Anthropic + OpenAI, yields the
  same event *types*, and recorded substitutions are correct.
- **Cost test:** for each profile, `compute_cost` matches a hand-calc from the profile pricing within
  rounding; cached/reasoning tokens priced correctly.
- **Resilience test:** injected 429 → retried; persistent 5xx → breaker opens → fallback fires and is
  recorded.
- **Leak test:** a provider-specific feature on a model without it produces the right `Substitution`,
  not a silent drop.

→ Next: `03-runtime.md`.
