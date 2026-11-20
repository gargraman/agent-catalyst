# Research — Eval & Drift Engine (M3 slice)

Phase 0 decisions. No `NEEDS CLARIFICATION` remained after `/speckit-clarify` (Session 2026-11-05); the open
scope choices were resolved there. The decisions below are grounded in the existing code (`services/runtime/`,
`mvp/evals/`), the design blueprint (`docs/backend/05-eval-drift.md`), and the frontend contract
(`frontend/src/lib/data.ts`).

---

## D1 — The judge (and candidate generation) reuse `runtime.invoke_result`, not a private model path

**Decision**: Both candidate output generation (when needed) and the LLM judge call the runtime's
**`invoke_result(snapshot, inputs, ctx)`** collecting entry point. The judge builds a judge-agent snapshot
(pinned to `claude-haiku-5-5`), assembles the rubric/pairwise prompt as inputs, and reads the returned
`InvokeResult.text` (the judge's JSON) + `InvokeResult.run` (cost/latency/outcome).

**Rationale**: P6 invariant — "evaluation and production invocation MUST exercise the same runtime contract."
`invoke_result` already returns `text` + a `RunRecord` carrying `cost_usd`, `latency_ms`, `outcome`, and
substitutions — exactly the efficiency inputs the verdict math needs, with cost computed from our own
profiles (P7). Using the streaming `invoke` instead would add needless accumulation; eval is non-streaming.

**Alternatives considered**: (a) a separate judge model client — rejected, violates P6 and re-introduces a
cost path that bypasses our profiles (P7); (b) streaming `invoke` — rejected, unnecessary for a collecting
consumer.

---

## D2 — Port `deterministic.py` and `drift.py` near-verbatim; they are already pure

**Decision**: Copy `mvp/evals/deterministic.py` → `services/evals/deterministic.py` and `mvp/evals/drift.py`
→ `services/evals/drift.py`, adapting only imports and the shared types (into `services/evals/types.py`).
Preserve the load-bearing verdict ordering and the first-person-anchored refusal list verbatim.

**Rationale**: These modules are already zero-dependency, pure, and test-proven in the MVP (the H2/H1
experiment). Re-deriving them would risk regressing subtle, deliberate behavior (e.g. the first-person
refusal anchoring that avoids false-positiving on "the service was unable to start"). The tests
(`mvp/tests/test_deterministic.py`, `test_verdict.py`, `test_score_quality.py`) port alongside as the RED
baseline.

**Alternatives considered**: rewrite from the design doc — rejected (needless risk; the MVP is the proof).

---

## D3 — Judge output is structured JSON, parsed and validated; judge identity is versioned

**Decision**: The judge prompt instructs a strict JSON output `{rubric: {<dim>: 1-5, …}, pairwise:
"baseline"|"candidate"|"tie", rationale: str}`. The judge module parses it (reusing the deterministic
JSON-validity logic) and fails closed to a typed, low-confidence judge result if the output is unparseable.
Judge identity is a module-level versioned record: `JUDGE_MODEL = "claude-haiku-5-5"`, `JUDGE_VERSION =
"judge-v1"`, plus a `judge_prompt_hash` over the prompt+rubric template. All of it is carried on every
`JudgeVerdict`.

**Rationale**: P4 — "any AI judge … MUST have a versioned identity, prompt, rubric, schema." The one thing a
drift detector must never do is drift itself; a judge-version bump is explicit and (later) re-runs baselines.

**Alternatives considered**: free-text judge output scored by regex — rejected (brittle, unversionable
schema). Storing the full prompt rather than a hash — deferred to the DB-backed persistence feature.

---

## D4 — Eval-fan-out circuit breaker is instance-scoped, not a global singleton (F8)

**Decision**: Implement a small `CircuitBreaker` in `services/evals/breaker.py` that is **instantiated per
eval run** (or injected), tracking consecutive provider failures and opening (fail-fast) after a threshold,
with a half-open retry. The eval runner owns its own instance; nothing module-global is shared.

**Honest note**: There is currently **no production circuit breaker** anywhere in `services/` or `mvp/`
(verified by grep). So "separate from the prod breaker" (F8) is satisfied **by construction**: the eval
breaker is a per-run instance, so when a prod breaker is later built it cannot share state with eval's. The
F8 test asserts two independent instances do not share trip state — encoding the invariant now so a future
"convenience" module-global can't silently re-introduce the shared-breaker bug.

**Rationale**: F8 — a shared breaker lets eval fan-out trip production (or vice versa). Encoding independence
at the type level (instance, not singleton) is the cheap durable guard. A full adaptive/token-bucket breaker
is overkill for an inline, mocked slice.

**Alternatives considered**: (a) defer the breaker entirely — rejected, FR-021 requires it and the invariant
is cheapest to lock in now; (b) a global registry keyed by purpose — rejected, re-creates the shared-state
risk the finding warns about.

---

## D5 — Determinism (F18): the deterministic layer is pure; all randomness lives in the judge and is seedable

**Decision**: `deterministic.py` and `drift.py` use no wall-clock, no RNG, and no reliance on set/dict
iteration order for results. The **only** randomness in the engine is the judge's pairwise presentation-order
shuffle (to cancel position bias), which takes an injectable/ seedable RNG so tests are reproducible.

**Rationale**: F18 — determinism of the deterministic checks is a hard requirement; a flaky correctness floor
is worse than none. SC-004 asserts identical results on repeated runs. Confining randomness to the judge (an
advisory, mocked layer here) keeps the gating path deterministic.

**Alternatives considered**: global `random` seeding — rejected (test-order-fragile, leaks across modules).

---

## D6 — `EvalPolicy` is a per-workspace value object, passed in (no DB this slice)

**Decision**: Port the MVP `EvalPolicy` dataclass into `services/evals/types.py`, matching the FE
`EvalPolicy` field-for-field (`min_cases`, `quality_threshold`, `efficiency_budget_pct`,
`allow_low_confidence_override`; defaults `10 / 90 / 10 / false`). The verdict reads the policy for the
agent's workspace; in this slice it is supplied as an argument.

**Rationale**: `05 §1` — thresholds are a per-workspace policy, not global constants. Matching the FE type
(`data.ts:1064`) keeps the M4 API serialization drop-in.

**Alternatives considered**: hard-coded 90/10 constants — rejected (violates the per-workspace design and the
FE contract).

---

## D7 — The verdict aggregate is the full frontend `EvalRun` shape; pairwise is advisory

**Decision**: The runner emits an `EvalRunResult` carrying the full FE `EvalRun` fields — `quality`,
`qualityDelta`, `costDelta`, `latencyDelta`, `cases`, `verdict`, `reason`, `dimensions[]`, `failingCases[]` —
plus the advisory `pairwise_rate`. `latencyDelta` is computed from the candidate/baseline run latencies
(surfaced, not gated). The FR-008 gating ordering is unchanged; `pairwise_rate` is surfaced only (per the
clarify decision).

**Rationale**: Clarify decision 2 — full EvalRun shape so M4 serializes drop-in with no rework. The FE type
(`data.ts:855`) includes `latencyDelta`, so the aggregate includes it for fidelity even though the verdict
math gates on cost (mvp parity).

**Alternatives considered**: minimal verdict-only aggregate — rejected in clarify (would force M4 contract
rework).

---

## D8 — Tenancy: eval carries `workspace_id`; cross-workspace comparison is rejected

**Decision**: An eval run carries a `workspace_id`; the runner asserts the candidate snapshot, the baseline,
and the cases all belong to that workspace, raising a typed error on mismatch (fail closed) rather than
silently scoring across tenants.

**Rationale**: P5 — workspace separation is a security boundary from release #1. The negative cross-workspace
test mirrors the constitution's negative cross-tenant requirement at the library boundary.

**Alternatives considered**: trust the caller to pre-filter — rejected (fail-closed tenancy must be in code,
P3/P5).

---

## D9 — No new third-party dependencies

**Decision**: Reuse `services/runtime/` and the stdlib; use already-present `jsonschema` for structured-output
validity if a case declares a schema. Add nothing to `pyproject.toml`.

**Rationale**: The engine is scoring logic over the runtime's outputs; it needs no HTTP/DB/transport of its
own (P6). Fewer dependencies = smaller supply-chain surface for the pilot.

**Alternatives considered**: a stats/ML library for scoring — rejected (weighted-mean + percentage deltas are
stdlib `statistics`/arithmetic).
