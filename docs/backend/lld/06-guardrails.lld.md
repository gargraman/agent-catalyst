# 06 (LLD) — Guardrails

> **Parent:** [`../06-guardrails.md`](../06-guardrails.md) (the stage interface, the ordered runner, the
> streaming redactor, fail-open/closed).
> The parent gives the `Decision`/`StageResult`/`GuardrailStage` contract, the `run_input_stages` runner, the
> `RedactingStream`, and the fail-mode decision table — and **one** worked stage (`PiiStage`). It leaves the
> **`guardrail_config` schema** (the central data contract), the **other six stages**, the **runner's actual
> error→fail-mode code**, the **`policy_judge` post-stream flow**, and the **window-overflow leak edge**
> unspecified. This doc fills those.

## What this adds over parent `06`

| Gap in `06` | Filled here |
|---|---|
| `guardrail_config` stored "per version" but never typed (how a version lists stages + per-stage params) | §1 — `GuardrailConfig`/`StageConfig` + the per-stage param models |
| 6 of 7 stages are table rows only (`secret_leak`/`prompt_injection`/`topic_scope`/`profanity_toxicity`/`jailbreak`/`policy_judge`) | §2 — each stage's `check()` logic + params |
| `run_input_stages` (`§4`) has **no `try/except`**; fail-mode is a prose table | §3 — the fail-mode wrapper (the code behind the table) |
| `RedactingStream` runs "tier-0 redactors only" (prose) + `_scrub` reads `self.ctx` never set; window-overflow match "silently leaks" | §4 — hot-path stage filter, the `ctx` fix, window-overflow validation |
| `policy_judge` "runs post-stream, blocks → terminal event" — no flow | §5 — the post-stream output path + its honest streaming limitation |

Python 3.12, Pydantic **v2**. Snippets compile (§6). `Decision`/`StageResult`/`GuardrailStage`/
`run_input_stages`/`RedactingStream`/`PiiStage`: **see `06` — not restated.** `StageConfig`/`GuardrailConfig`
shape: [`01-data-model.lld.md §3`](01-data-model.lld.md). `OutputBlocked` is **defined in §2 here** (category B
per [`00-conventions.lld.md §1`](00-conventions.lld.md) — caught → `blocked` run, not an `AppError`). Judge
model: `05`.

---

## 1. `guardrail_config` — the typed schema

`01-data-model.lld.md §3` defined `GuardrailConfig = {input: [StageConfig], output: [StageConfig]}` and
`StageConfig = {stage, enabled, params}` with `params` left as `dict`. Here `params` is specialized per stage
id, so a version's config is validated at save time (not discovered broken at invoke).

```python
from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal


class _P(BaseModel):
    model_config = ConfigDict(extra="forbid")        # a typo'd param is a 400, not a silent no-op


class PiiParams(_P):
    mode: Literal["block", "redact"] = "redact"
    types: list[str] = Field(default_factory=lambda: ["email", "ssn", "cc", "phone", "api_key", "internal_id"])
    use_presidio: bool = False                        # optional local NER (06 §3); regex fast-path otherwise

class SecretLeakParams(_P):
    mode: Literal["block", "redact"] = "redact"
    min_entropy: float = 3.5                          # Shannon bits/char over a token → high-entropy secret
    patterns: list[str] = Field(default_factory=list)  # extra key-shaped regexes beyond the built-ins

class PromptInjectionParams(_P):
    action: Literal["block", "flag"] = "block"
    classifier_threshold: float = 0.8                 # optional small classifier; None → heuristics only
    use_classifier: bool = False

class TopicScopeParams(_P):
    mode: Literal["allow", "deny"] = "allow"          # allow-list (only these topics) | deny-list
    topics: list[str] = Field(default_factory=list)
    similarity_threshold: float = 0.6                 # embedding match cutoff when topics are phrases

class ProfanityParams(_P):
    action: Literal["block", "redact"] = "redact"
    threshold: float = 0.8                            # detoxify score; wordlist fast-path below it

class JailbreakParams(_P):
    ruleset_version: str = "jb-v1"                    # versioned pattern set (so an update is auditable)

class PolicyJudgeParams(_P):
    rubric: str                                       # the written policy the judge scores against (06 §3)
    threshold: float = 0.7                            # min pass score 0..1
    # judge model is FIXED + versioned (05) — not configurable here, so the guard can't silently drift


STAGE_PARAMS: dict[str, type[_P]] = {
    "pii": PiiParams, "secret_leak": SecretLeakParams, "prompt_injection": PromptInjectionParams,
    "topic_scope": TopicScopeParams, "profanity_toxicity": ProfanityParams, "jailbreak": JailbreakParams,
    "policy_judge": PolicyJudgeParams,
}

def validate_stage_params(stage: str, params: dict) -> _P:
    """Called when a version's guardrail_config is saved (08 PATCH) and at to_snapshot (01 LLD §1).
    Unknown stage id or bad params → 400 input.invalid (00-conv §1)."""
    model = STAGE_PARAMS.get(stage)
    if model is None:
        raise InputInvalid(f"unknown guardrail stage: {stage}", detail={"stage": stage})
    return model.model_validate(params)
```

The FE `GuardrailStage = {name, desc, on}` (`data.ts`) maps to `StageConfig {stage: name, enabled: on,
params}`; `desc` is display-only (not stored in config). `DEFAULT_GUARDRAILS` (FE) seeds the default
`input`/`output` stage lists.

---

## 2. The six stages the parent only tabled

Same shape as the parent's `PiiStage` (`06 §3`): `name`/`phase`/`cost_tier` + `async check() -> StageResult`.
Bodies are sketches at the parent's altitude — detection specifics are the stage's own module.

```python
import math
from runtime.guardrails.base import Decision, StageResult   # 06 §2


class SecretLeakStage:                      # out, tier 0 — key-shaped + high-entropy strings (09 §9)
    name, phase, cost_tier = "secret_leak", "output", 0
    def __init__(self, p: SecretLeakParams): self.p = p
    async def check(self, text, ctx) -> StageResult:
        spans = _key_shaped(text) + _high_entropy(text, self.p.min_entropy, self.p.patterns)
        if not spans:
            return StageResult(Decision.ALLOW)
        if self.p.mode == "block":
            return StageResult(Decision.BLOCK, label="Secret blocked in output")
        return StageResult(Decision.REWRITE, text=_redact(text, spans), label="Secret redacted")


class PromptInjectionStage:                 # in, tier 0–1 — "ignore previous", tool-hijack, exfil
    name, phase, cost_tier = "prompt_injection", "input", 0
    def __init__(self, p: PromptInjectionParams): self.p = p; self.cost_tier = 1 if p.use_classifier else 0
    async def check(self, text, ctx) -> StageResult:
        hit = _injection_heuristics(text)   # pattern set: override/role-swap/system-prompt-exfil/tool-hijack
        if not hit and self.p.use_classifier:
            hit = await _injection_classifier(text) >= self.p.classifier_threshold
        if not hit:
            return StageResult(Decision.ALLOW)
        return (StageResult(Decision.BLOCK, label="Prompt injection blocked")
                if self.p.action == "block" else StageResult(Decision.ALLOW, label="Injection flagged"))


class TopicScopeStage:                      # in, tier 0 — keep an HR bot out of legal advice
    name, phase, cost_tier = "topic_scope", "input", 0
    def __init__(self, p: TopicScopeParams): self.p = p
    async def check(self, text, ctx) -> StageResult:
        on_topic = _topic_match(text, self.p.topics, self.p.similarity_threshold)
        blocked = (not on_topic) if self.p.mode == "allow" else on_topic   # allow-list vs deny-list
        return StageResult(Decision.BLOCK, label="Off-topic request blocked") if blocked else StageResult(Decision.ALLOW)


class ProfanityToxicityStage:               # in+out, tier 0–1
    name, phase, cost_tier = "profanity_toxicity", "output", 0
    def __init__(self, p: ProfanityParams): self.p = p
    async def check(self, text, ctx) -> StageResult:
        score = _toxicity(text)             # wordlist fast-path; detoxify when enabled
        if score < self.p.threshold:
            return StageResult(Decision.ALLOW)
        return (StageResult(Decision.BLOCK, label="Toxic content blocked")
                if self.p.action == "block" else StageResult(Decision.REWRITE, text=_mask_profanity(text), label="Profanity masked"))


class JailbreakStage:                       # in, tier 1 — known-jailbreak pattern set
    name, phase, cost_tier = "jailbreak", "input", 1
    def __init__(self, p: JailbreakParams): self.p = p
    async def check(self, text, ctx) -> StageResult:
        if _jailbreak_ruleset(self.p.ruleset_version).matches(text):
            return StageResult(Decision.BLOCK, label="Jailbreak attempt blocked")
        return StageResult(Decision.ALLOW)


class PolicyJudgeStage:                      # out, tier 2 — LLM policy check; runs POST-STREAM (§5)
    name, phase, cost_tier = "policy_judge", "output", 2
    def __init__(self, p: PolicyJudgeParams): self.p = p
    async def check(self, text, ctx) -> StageResult:
        score = await _policy_judge(text, self.p.rubric, ctx)   # fixed judge model (05)
        if score >= self.p.threshold:
            return StageResult(Decision.ALLOW)
        return StageResult(Decision.BLOCK, label="Policy check failed", detail={"score": score})
```

---

**`phase` is set at load, not hardcoded — the `in+out` stages need both.** The catalog marks `pii` and
`profanity_toxicity` as **in+out** (`06 §3`), but the classes bake a single class-level `phase`. `load_stages`
must stamp each instance's `phase` from the config list it's loaded under — so an in+out stage yields a
*separate instance per phase*. Without this, `PiiStage.phase="input"` means **no output-phase PII instance**,
and `hot_path_stages` (which selects `phase == "output"`) never stream-redacts PII — defeating §4's whole point.

```python
def load_stages(cfg: GuardrailConfig, phase: str) -> list:
    """Instantiate the configured, enabled stages for a phase, STAMPING instance.phase = phase (overriding the
    class default) so an in+out stage (pii/profanity) has a distinct input and output instance."""
    stages = []
    for sc in (cfg.input if phase == "input" else cfg.output):
        if not sc.enabled:
            continue
        stage = STAGE_CTORS[sc.stage](validate_stage_params(sc.stage, sc.params))
        stage.phase = phase                               # instance override — the config list decides the phase
        stages.append(stage)
    return stages
```

> ⚠️ **Amendment to `06 §3`:** stage `phase` must be instance-level (stamped by `load_stages`), not a fixed
> class attribute, or in+out stages have no output instance. Folded into `06`.

**`OutputBlocked`** (raised by `RedactingStream._scrub` / `run_output_poststream` on a BLOCK) is defined here,
not in `00-conventions` — it is **category B** (`00-conventions.lld.md §1`: caught in the graph, converted to a
`blocked` run, never an HTTP error), so it is a plain exception in `runtime/guardrails/`, not an `AppError`:

```python
class OutputBlocked(Exception):
    """A tier-0 output redactor hit a BLOCK mid-stream (or policy_judge post-stream). Caught by the runtime →
    run_status='blocked', stop_reason='blocked' (03 LLD §5); HTTP stays 200 with the blocked outcome streamed."""
    def __init__(self, label: str): super().__init__(label); self.label = label
```

## 3. Runner error handling → fail-mode (the code behind `06 §6`)

The parent's `run_input_stages` (`§4`) calls `await stage.check(...)` with **no `try/except`** — a classifier
timeout or Presidio-down would crash the whole invoke. `06 §6` says fail-closed (default) / fail-open in
prose; here is the wrapper that implements it, used by both the input runner and the streaming `_scrub`:

```python
async def run_stage(stage, text: str, ctx, fail_mode: str) -> StageResult:
    """Wrap a stage so its OWN failure is a fail-mode decision, never an uncaught 500 (06 §6)."""
    try:
        return await stage.check(text, ctx)
    except Exception as exc:
        emit_alert("guardrail_stage_error", stage=stage.name, error=str(exc), ctx=ctx)   # 10 — NEVER silent
        if fail_mode == "closed":                        # security default (00 §6 AC_GUARDRAILS_FAIL_MODE)
            # label mirrors the FE's "n/a (provider timeout)" family (06 §7 → runs.guardrail)
            return StageResult(Decision.BLOCK, label=f"n/a ({stage.name} error)")
        return StageResult(Decision.ALLOW, label=f"{stage.name} error (failed open)")    # availability-critical
```

`fail_mode` is resolved per run: the version's `guardrail_config.fail_mode` override, else the global
`AC_GUARDRAILS_FAIL_MODE` (`00 §6`), default **`closed`**. The parent's `run_input_stages` loop changes one
line — `r = await stage.check(text, ctx)` → `r = await run_stage(stage, text, ctx, fail_mode)`.

> ⚠️ **Amendment to `06 §4`:** route `run_input_stages` (and the post-stream runner, §5) through `run_stage`
> so a stage exception becomes a fail-mode `StageResult` instead of propagating. Without it, a flaky
> classifier 500s the whole invoke — the opposite of "fail closed." Folded into `06` per the reconciliation.

---

## 4. Streaming hot-path filter + window-overflow (the leak edge)

Two `06 §5` gaps: "tier-0 redactors only" is a comment (not enforced), and a match longer than `window`
"silently leaks" across the buffer boundary.

```python
def hot_path_stages(stages: list) -> list:
    """The RedactingStream runs only tier-0, REWRITE-capable OUTPUT stages per token (06 §5). Everything
    else (policy_judge tier-2) runs post-stream (§5)."""
    return [s for s in stages if s.phase == "output" and s.cost_tier == 0]


# RedactingStream.__init__ must also take ctx (the parent's _scrub reads self.ctx but __init__ never sets it):
#   def __init__(self, stages, ctx, window=None):
#       self.stages = hot_path_stages(stages); self.ctx = ctx
#       self.window = window or required_window(self.stages); self.buf = ""


def required_window(stages: list) -> int:
    """Window = the retained-tail size. MUST be ≥ the longest matchable pattern so a match can't complete
    beyond the tail undetected. Computed at CONFIG SAVE (validate_stage_params), not at invoke."""
    longest = max((_max_match_len(s) for s in stages), default=64)
    return max(160, longest + 16)                        # retain ≥ longest+headroom raw chars
```

**The real bug (review HIGH): a bigger window is necessary but NOT sufficient.** The parent's `feed()` scrubs
the *isolated* head — `yield await self._scrub(self.buf[:-window])` — so a match that starts in the head and
ends in the retained tail is only *partially* present when `_scrub` runs and is flushed with the secret's
prefix intact, **at any window size**. The fix is the scrub *target*: scrub the **whole buffer**, then flush
the prefix and retain the raw tail.

```python
async def feed(self, delta: str):
    self.buf += delta
    if len(self.buf) <= self.window:
        return
    scrubbed = await self._scrub(self.buf)               # scrub the WHOLE buffer → a straddling match IS seen
    keep = self.window                                   # retain ≥ longest-match raw chars for the next round
    yield scrubbed[:-keep]                               # flush the match-safe prefix (already redacted)
    self.buf = self.buf[-keep:]                          # keep the RAW tail (re-scrubbed when more arrives)
```

- **Scrub whole, retain tail.** A complete match anywhere in `buf` is redacted by the whole-buffer scrub; the
  retained raw tail holds only a possible match *prefix*, re-scrubbed with the next delta. This is what the
  parent `06 §8` golden test ("client never saw the raw span, including across the buffer boundary") requires.
- **Minor cosmetic caveat:** a redaction token straddling the `[:-keep]` cut can be split across two flushes
  (harmless — no secret leaks; the tail re-scrub re-redacts its portion). A fully robust impl tracks redaction
  offsets; the leak is closed regardless.
- **Hot-path = tier-0 output redactors only** (`hot_path_stages`); tier-1/2 (`policy_judge`) run post-stream (§5).

> ⚠️ **Amendment to `06 §5` (2026-10-24 review), folded into `06`:** `RedactingStream.__init__` takes + stores
> `ctx`, filters to tier-0 output stages, sizes `window` via `required_window` — **and `feed()` must scrub the
> whole buffer** (above), not the isolated head, or straddling matches leak at any window size.

---

## 5. `policy_judge` post-stream flow

`06 §5` states `policy_judge` runs after the full response and "blocks → terminal event," but not how. The
flow, and its honest limitation:

```python
async def run_output_poststream(assembled: str, stages: list, ctx, fail_mode: str) -> StageResult:
    """After the stream (or the whole non-stream response) is assembled, run the tier-1/2 output stages that
    can't run per-token (policy_judge). Returns the first BLOCK, else ALLOW."""
    for stage in sorted((s for s in stages if s.phase == "output" and s.cost_tier > 0), key=lambda s: s.cost_tier):
        r = await run_stage(stage, assembled, ctx, fail_mode)      # §3 — fail-mode applies here too
        emit_span("guardrail", stage.name, r, ctx)                 # 10
        if r.decision is Decision.BLOCK:
            return r
    return StageResult(Decision.ALLOW)
```

- **Non-streaming mode:** the result is withheld until `run_output_poststream` passes → a block means the
  caller never sees the content. Clean. For a security-sensitive published endpoint, set the version to
  non-streaming so `policy_judge` is authoritative before anything leaves (`06 §5`).
- **Streaming mode — the honest limitation:** tokens are already flushed to the client when `policy_judge`
  runs, so a post-stream BLOCK can't un-send them. The run ends with a terminal `done{status:blocked}` +
  `stop_reason="policy_blocked"` and `runs.guardrail` records it (for audit/eval), but the client *did* see
  the text. This is inherent to streaming + a whole-response judge — documented so a builder chooses
  non-streaming when "too late" is unacceptable, rather than assuming streaming `policy_judge` is a hard gate.
- Eval exercises it like production (`06 §7`): because a guardrail is in the snapshot, an over-blocking
  `policy_judge` shows up as a refusal-rate/quality regression (`05`).

---

## 6. Verification performed

1. **Snippet compile** — every `python` block via `compile()` (same harness as the other LLDs).
2. **Cross-ref** — against `06` read in full: `Decision`/`StageResult`/`GuardrailStage` (`§2`), the stage
   catalog (`§3`, all 7 stages matched), `run_input_stages` (`§4`), `RedactingStream` (`§5`), fail-mode
   table (`§6`), `runs.guardrail` surfacing (`§7`); `01-data-model.lld.md §3` (`StageConfig`/`GuardrailConfig`);
   `00-conventions.lld.md §1` (`InputInvalid`/`OutputBlocked`); `05` (fixed judge).
3. **FE reconciliation** — `GuardrailStage {name,desc,on}` → `StageConfig {stage,enabled,params}`;
   `DEFAULT_GUARDRAILS` seeds the stage lists; fail-closed label matches the FE `runs.guardrail` strings.
4. **Two flagged amendments** (both folded into `06`): `run_input_stages` must route stage calls through the
   fail-mode `run_stage` wrapper; `RedactingStream` must take `ctx`, filter to tier-0 output stages, and size
   its window from the patterns.

→ Next LLD: `07-cost-cogs.lld.md` (subtask 8).
