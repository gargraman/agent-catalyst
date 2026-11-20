# Feature Specification: Eval & Drift Engine (M3 — walking-skeleton slice)

**Feature Branch**: `feat/backend-m0-foundation` (continuing the backend build branch)

**Created**: 2026-11-05

**Status**: Draft

**Input**: User description: "M3 eval/drift engine — the second defensible core. Port mvp/evals/{deterministic.py,drift.py} into a new pure services/evals/ library and add a judge.py that runs cases through the SAME runtime.invoke (so eval evaluates exactly what production runs — the P6 invariant). Scope for this feature (walking-skeleton slice): deterministic checks against a frozen baseline; drift detection comparing a candidate version's eval results vs a frozen baseline version; an LLM judge that produces a visible Verdict with versioned identity/prompt/rubric/schema (P4) — but ADVISORY-ONLY: publish is NOT gated on the uncalibrated judge until human-rater κ≥0.60 calibration. Judge runs through runtime.invoke, fully mocked in this feature. Keep services/evals a PURE library. Eval-fan-out circuit breaker separate from the prod breaker (F8). Determinism of deterministic checks is a hard requirement (F18). Tenancy: eval runs are workspace-scoped (P5)."

> **Scope note (why this is a slice, not all of 05):** `docs/backend/05-eval-drift.md` designs the full
> engine — the publish gate, the DB-backed baseline lifecycle, the verdict cache, the async eval worker,
> incremental eval, cost controls (Batch API), and judge calibration. This feature builds the **pure
> scoring core** that everything else wraps: deterministic checks, the verdict math, and an advisory,
> mocked, versioned judge that calls the same `runtime.invoke`. The persistence-, gate-, and
> worker-dependent pieces are explicitly deferred (see *Out of Scope*) to the features that own the API
> and worker surfaces (M4+). This mirrors the M2 split (M2a spine first, validators to M2b).

## Clarifications

### Session 2026-11-05

- Q: Should the drift verdict treat the judge's pairwise win-rate as a hard gate (can cause FAIL) or only an advisory signal in this slice? → A: **Advisory signal only** — compute and surface pairwise win-rate on the Verdict, but it never flips the pass/fail outcome (matches the mvp port and the advisory-only, uncalibrated judge). The pairwise gating axis stays deferred with the publish gate.
- Q: How complete should the verdict aggregate be in this pure-library slice? → A: **Full EvalRun-shaped** — emit the complete frontend `EvalRun` shape (verdict, reason, quality, qualityDelta, costDelta, dimensions[], failingCases[]) so the M4 API can serialize it drop-in with no contract rework.
- Q: Which judge model id should this feature pin (resolving the docs-vs-approved skew)? → A: **`claude-haiku-5-5`** — the approved judge (profile already present), resolving plan step 0c's reconciliation (docs/backend/05 literally says Haiku 4.5, whose profile is deprecated). `judge_version` starts `"judge-v1"`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Deterministic correctness floor (the cheap pre-filter) (Priority: P1)

A builder has a candidate agent version and a suite of eval cases. Before spending a single model token on
judging, the platform runs cheap, zero-model deterministic checks on each candidate output. Broken outputs —
empty responses, model refusals, malformed/unparseable structured output — are caught immediately and marked
as **hard failures** that force the case to FAIL and are never sent to the (expensive) judge. Softer problems
— a missing required field, an out-of-enum value, a mismatch against an expected answer — are recorded as
non-hard failures that still inform the verdict.

**Why this priority**: This is the correctness floor and the cost pre-filter the whole engine rests on.
It delivers real, honest value with **zero model calls and zero cost** — even with the judge entirely
stubbed, this story alone can tell a builder "this candidate is producing broken output." It is the
lowest-risk, highest-leverage slice and is a near-direct port of proven `mvp/evals/deterministic.py`.

**Independent Test**: Feed a set of candidate outputs (valid JSON, empty string, a first-person refusal, a
non-object JSON, an output missing a required field, an out-of-enum severity) through the deterministic
checker with their cases and assert each produces the correct `DetResult` (passed / checks map / hard_fail)
— no model, no network, no DB.

**Acceptance Scenarios**:

1. **Given** an empty or whitespace-only candidate output, **When** deterministic checks run, **Then** the
   result is `hard_fail = true` and the case is marked FAIL without reaching the judge.
2. **Given** an output containing a first-person refusal ("I'm unable to…"), **When** checks run, **Then**
   `hard_fail = true`; **and given** an output with a third-person "the service was unable to start",
   **Then** `no_refusal` stays true (no false positive).
3. **Given** an unparseable or non-object JSON output, **When** checks run, **Then** `schema_valid = false`
   and `hard_fail = true`.
4. **Given** a well-formed output missing a required field (or with an out-of-enum severity, or mismatching
   an `expected` value), **When** checks run, **Then** `passed = false` **and** `hard_fail = false` (soft).
5. **Given** the same (output, case) pair run twice, **When** checks run both times, **Then** the two
   `DetResult`s are identical (determinism, F18).

---

### User Story 2 — Honest drift verdict vs a frozen baseline (Priority: P2)

Given per-case deterministic results plus quality and cost aggregates for a candidate version and a frozen
**baseline** version, the platform computes a single **verdict** — `pass`, `fail`, or `low_confidence` —
with a specific, human-readable reason. The verdict ordering is load-bearing: **statistical honesty first**
(a suite below the minimum case count is `low_confidence`, never `pass`, even if it is also failing), then
the **correctness floor** (any hard-assertion failure → `fail`), then the **quality axis** (a quality drop
beyond the policy threshold → `fail`), then the **efficiency axis** (cost beyond the policy budget → `fail`),
else `pass`. Thresholds are a **per-workspace eval policy**, not global constants.

**Why this priority**: This is what turns raw per-case results into the one answer a builder acts on, and
its ordering is the guarantee that the engine is statistically honest before it is opinionated. It depends
on US1's deterministic results and is a direct port of proven `mvp/evals/drift.py`.

**Independent Test**: Drive `verdict(...)` with synthetic inputs covering each branch — a sub-min-cases
suite that is also failing (→ `low_confidence`), a hard-assertion failure (→ `fail`), a quality drop past
threshold (→ `fail`), an over-budget cost delta (→ `fail`), and a clean pass — and assert both the returned
verdict and that a specific reason string is attached. Separately test `score_quality` (1–5 rubric → 0–100)
and `efficiency_delta` (cost %, baseline-zero handling).

**Acceptance Scenarios**:

1. **Given** a suite with fewer than the policy's `min_cases` **and** a hard-assertion failure present,
   **When** the verdict is computed, **Then** it is `low_confidence` (statistical honesty is checked
   before pass/fail).
2. **Given** `min_cases` met but a deterministic hard-assertion failed, **When** the verdict is computed,
   **Then** it is `fail` with a hard-assertion reason.
3. **Given** all hard assertions green but candidate quality dropped more than the policy
   `quality_threshold` below baseline, **When** the verdict is computed, **Then** it is `fail` with a
   quality reason naming the drop.
4. **Given** quality within threshold but candidate cost exceeds the policy `efficiency_budget_pct`,
   **When** the verdict is computed, **Then** it is `fail` with an efficiency reason (e.g. "cost +12%").
5. **Given** all axes within policy, **When** the verdict is computed, **Then** it is `pass`.
6. **Given** two workspaces with different `quality_threshold` policies, **When** the same scores are
   evaluated under each, **Then** the verdict can legitimately differ (policy is per-workspace, not global).

---

### User Story 3 — Advisory LLM judge through the same `runtime.invoke` (Priority: P3)

The platform scores candidate quality with an LLM judge that produces a **versioned Verdict** — per-dimension
rubric scores (1–5), a pairwise winner (baseline vs candidate), and a rationale, all stamped with a fixed
`judge_version`. Critically, the judge runs its model calls through the **same `runtime.invoke`** the
playground and published endpoint use — eval evaluates exactly what production runs. In this feature the
judge is **fully mocked** (no live/paid call) and **advisory-only**: it produces a visible Verdict but
**never gates a publish decision**, because the judge is not yet calibrated against human raters (κ≥0.60).

**Why this priority**: The judge is the highest-risk, highest-cost component and the one requiring the most
governance care (P4 evidence, advisory-until-calibrated per owner decision #3). Building it last, mocked,
lets US1+US2 stand as a working honest-verdict MVP first, and proves the P6 invariant (judge → same
`runtime.invoke`) without any live spend.

**Independent Test**: With `runtime.invoke` replaced by a mock that returns canned judge outputs, call the
judge for a case and assert it returns a `JudgeVerdict` carrying rubric scores, a pairwise winner, a
rationale, and the current `judge_version`; assert the judge invoked the shared `runtime.invoke` (not a
private model path); assert pairwise ordering is randomized; and assert no code path lets the judge's
verdict flip a publish decision.

**Acceptance Scenarios**:

1. **Given** a case and candidate+baseline outputs, **When** the judge runs (mocked), **Then** it returns a
   `JudgeVerdict` with rubric scores, a pairwise winner ∈ {baseline, candidate, tie}, a rationale, and the
   current `judge_version`.
2. **Given** the judge needs a model call, **When** it executes, **Then** it calls the shared
   `runtime.invoke` (same path as production), not a separate model client.
3. **Given** the judge produces any verdict, **When** a publish decision is evaluated in this feature,
   **Then** the judge verdict does not block or force publish (advisory-only).
4. **Given** the judge is invoked anywhere in the test suite, **When** tests run, **Then** no live/paid
   provider call is made (fully mocked, mirrors M2a SC-008).
5. **Given** a case whose candidate output already hard-failed deterministically, **When** the pipeline
   runs, **Then** the judge is not invoked for that case (pre-filter honored).

---

### Edge Cases

- **Empty suite / zero cases**: verdict is `low_confidence` (below `min_cases`), never `pass`.
- **First baseline, no prior version**: comparison has no trusted prior → `low_confidence` with a
  first-baseline reason.
- **Baseline cost of zero**: efficiency delta must not divide by zero — defined behavior (0% if both zero,
  otherwise treat as unbounded/over-budget rather than crashing).
- **Judge returns a close/tied pairwise call**: recorded as `tie`; does not crash the aggregate.
- **Eval fan-out overwhelms a provider**: the eval circuit breaker trips **independently** of the
  production invoke breaker — a tripped eval breaker must not degrade production availability, and vice
  versa (F8).
- **Mixed-workspace inputs**: cases/baseline from workspace A must never be scored against a candidate from
  workspace B — a tenancy mismatch is rejected, not silently compared (P5).
- **Non-triage output contracts**: the engine must not hard-code the triage `{severity, summary, next_step}`
  contract; required fields and enums are per-case configuration, with the triage shape only as a default.

## Requirements *(mandatory)*

### Functional Requirements

**Deterministic checks (US1)**

- **FR-001**: The engine MUST produce, for each (candidate output, case), a deterministic result reporting
  overall pass/fail, a per-check boolean map, and a `hard_fail` flag.
- **FR-002**: An empty or whitespace-only output MUST be a hard failure.
- **FR-003**: A first-person model refusal MUST be a hard failure; a third-person description of inability
  ("the service was unable to…") MUST NOT be flagged as a refusal (no false positive).
- **FR-004**: An output that is declared structured but is unparseable or not a JSON object MUST be a hard
  failure (`schema_valid = false`).
- **FR-005**: A missing/empty required field, an out-of-enum value, or a mismatch against a case's
  `expected` value MUST be a **soft** failure (`passed = false`, `hard_fail = false`).
- **FR-006**: Required fields and value enums MUST be configurable per case, with the incident-triage shape
  (`severity`, `summary`, `next_step`; severity enum) as the default — the engine MUST NOT hard-code one
  task's contract.
- **FR-007**: A case that hard-fails deterministically MUST force the case to FAIL regardless of any judge
  result, and MUST NOT be sent to the judge (the pre-filter).

**Verdict & drift math (US2)**

- **FR-008**: The engine MUST compute a verdict ∈ {`pass`, `fail`, `low_confidence`} applying this ordering
  exactly: (1) if case count < policy `min_cases` → `low_confidence`; (2) else if any hard assertion failed
  → `fail`; (3) else if quality drop vs baseline > policy `quality_threshold` → `fail`; (4) else if cost
  delta exceeds policy `efficiency_budget_pct` → `fail`; (5) else `pass`. *(Clarified 2026-11-05: the
  judge's pairwise win-rate is NOT part of this gating ordering in this slice — see FR-025.)*
- **FR-009**: Every verdict MUST carry a specific, case-grounded human-readable reason (e.g. which axis
  failed and by how much), not a generic string.
- **FR-010**: The engine MUST compute a quality score as a weighted blend of per-dimension rubric means,
  normalized from the 1–5 rubric scale to a 0–100 range.
- **FR-011**: The engine MUST compute an efficiency (cost %) delta of candidate vs baseline (positive =
  candidate more expensive), with defined, non-crashing behavior when the baseline cost is zero.
- **FR-012**: Verdict thresholds (`min_cases`, `quality_threshold`, `efficiency_budget_pct`,
  `allow_low_confidence_override`) MUST come from a **per-workspace eval policy**, not hard-coded global
  constants.
- **FR-013**: Comparison MUST be against a **frozen baseline** result set treated as immutable within a run
  (in this slice the baseline is supplied as an input; persistence and audited re-freeze are deferred).
- **FR-025** *(Clarified 2026-11-05)*: The suite-level verdict aggregate MUST be emitted in the **full
  frontend `EvalRun` shape** — `verdict`, `reason`, `quality`, `qualityDelta`, `costDelta`, `dimensions[]`
  (per-dimension scores), and `failingCases[]` — so the M4 API can serialize it drop-in. The judge's
  **pairwise win-rate MUST be computed and surfaced** on the aggregate as an advisory signal, but MUST NOT
  participate in the FR-008 gating ordering in this feature (pairwise gating deferred with the publish gate).

**Advisory judge (US3)**

- **FR-014**: The judge MUST produce a `JudgeVerdict` carrying per-dimension rubric scores (1–5), a pairwise
  winner ∈ {baseline, candidate, tie}, a rationale, and a `judge_version`.
- **FR-015**: The judge MUST perform its model calls through the **same `runtime.invoke`** used by the
  playground and published endpoint — no separate model-call path (P6 invariant).
- **FR-016**: The judge MUST be **advisory-only** in this feature: it produces a visible Verdict but MUST
  NOT gate, block, or force any publish decision (owner decision #3; publish-gate enforcement deferred).
- **FR-017**: The judge's model identity, prompt, rubric, and output schema MUST be **versioned** and carried
  on the verdict so the judge itself cannot silently drift (P4); a judge-version change is explicit. *(Clarified
  2026-11-05: the judge model is pinned to **`claude-haiku-5-5`** — the approved profile — and `judge_version`
  starts `"judge-v1"`, resolving the docs-vs-approved naming skew per plan step 0c.)*
- **FR-018**: Pairwise judging MUST randomize candidate/baseline presentation order to cancel position bias.
- **FR-019**: No live/paid provider call may occur in this feature — the judge is fully mocked (mirrors M2a
  SC-008); the first live judge call is deferred to Phase 5 under the paid-call protocol.

**Cross-cutting (apply to all stories)**

- **FR-020**: `services/evals/` MUST be a **pure library** — no inbound web-framework or database imports —
  enforced by an automated purity test (same guarantee as `services/runtime/`, P6).
- **FR-021**: The eval-fan-out circuit breaker MUST be a **separate instance** from the production invoke
  circuit breaker; tripping one MUST NOT trip or degrade the other (F8).
- **FR-022**: Deterministic checks MUST be **deterministic** (F18): identical (output, case) inputs MUST
  yield identical results across repeated runs and process restarts (no wall-clock, RNG, set-ordering, or
  dict-ordering nondeterminism in the deterministic layer).
- **FR-023**: Eval runs MUST be **workspace-scoped** (P5): a run carries a workspace id and MUST reject
  comparing cases/baseline and candidate that belong to different workspaces.
- **FR-024**: Candidate outputs used for eval MUST be produced with response caching disabled so a cached
  response cannot mask a regression.

### Key Entities *(include if feature involves data)*

- **EvalCase**: one test case — an input message, typed input variables, deterministic assertions (with a
  `hard` flag), judge rubric dimensions (name + weight), and an optional `expected` reference answer.
- **DetResult**: the deterministic-check outcome for one case — overall `passed`, a per-check boolean map,
  and `hard_fail`.
- **JudgeVerdict**: the judge outcome for one case — per-dimension rubric scores (1–5), a pairwise winner,
  a rationale, and the `judge_version`.
- **EvalPolicy**: per-workspace thresholds — `min_cases`, `quality_threshold`, `efficiency_budget_pct`,
  `allow_low_confidence_override`. Maps 1:1 to the frontend `EvalPolicy` type.
- **EfficiencyDelta**: cost-% delta of candidate vs baseline, with a `within_budget(pct)` test.
- **Verdict**: the suite-level outcome ∈ {`pass`, `fail`, `low_confidence`} plus its reason, quality score,
  quality/efficiency deltas, scored dimensions, and failing cases. Maps to the frontend `EvalRun` type.
- **Baseline**: a frozen, trusted result set anchored to a specific version + suite (supplied as input in
  this slice; DB-backed lifecycle and audited re-freeze deferred).
- **DriftTriggers**: the set of frozen fields whose change invalidates a version's publishable state
  (ported as a documented constant in this slice; wiring to a persisted `publishable` flag and the publish
  gate is deferred).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Given a deliberately degraded candidate (seeded regression) and an unchanged candidate, the
  engine returns `fail` for the regression and `pass` for the unchanged one — demonstrating **mechanical**
  drift detection (engine-plumbing evidence, explicitly **not** the κ≥0.60 human calibration).
- **SC-002**: 100% of hard-failing outputs (empty, first-person refusal, malformed/non-object structured
  output) are caught by the deterministic layer and none of them reach the judge.
- **SC-003**: A suite below the policy minimum case count returns `low_confidence` in 100% of cases and
  never returns `pass`.
- **SC-004**: Running the deterministic checks on the same input twice returns identical results on 100% of
  trials (determinism).
- **SC-005**: Zero live/paid model calls occur across the entire test suite for this feature.
- **SC-006**: Every non-pass verdict produced in the acceptance tests carries a reason that names the
  specific failing axis and magnitude (no generic reasons).
- **SC-007**: In this feature, the judge's verdict changes a publish decision in 0% of cases (advisory-only).
- **SC-008**: An automated purity check confirms `services/evals/` imports no inbound web framework or
  database layer; tripping the eval circuit breaker leaves production invoke availability unaffected.

## Assumptions

- **Scope decisions resolved in `/speckit-clarify` (Session 2026-11-05):**
  - **Pairwise-rate gating**: the newer `pairwise_rate` axis in `05-eval-drift.md` (LLD review A2) is
    **surfaced as an advisory signal on the Verdict but is NOT a hard gate** in this slice — consistent with
    the advisory-only judge and the ported `mvp/evals/drift.py` verdict signature, which does not gate on it
    (see FR-025; the pairwise gating axis is deferred with the publish gate).
  - **Judge model id**: the judge is pinned to the approved **`claude-haiku-5-5`** profile (already present in
    `profiles_data/`), resolving the docs-vs-approved naming (docs say Haiku 4.5) per plan step 0c.
    `judge_version` starts at `"judge-v1"` (see FR-017).
  - **Verdict aggregate completeness**: the verdict is emitted in the **full frontend `EvalRun` shape**
    (including `dimensions[]` and `failingCases[]`) so the M4 API serializes it drop-in (see FR-025).
  - **Baseline as input**: in this pure-library slice the frozen baseline result set is passed in; it is not
    loaded from or persisted to a database.
- The incident-triage output contract (`severity`, `summary`, `next_step`) from the MVP is used as the
  **default** case contract and example task; the engine remains task-agnostic via per-case configuration.
- Governance context: the viability gate is **🔴 GATE-OPEN** (no GO); the owner authorized building this in
  parallel with the publish-gate held **advisory-only** until κ≥0.60 calibration (owner decision #3). This
  feature does not change that posture.
- `runtime.invoke` (feature 003, M2a) is available and is the single shared execution path the judge uses.
- Test strategy mirrors M2a: genuine behavioral RED before GREEN (P9), everything mocked, no live/paid calls.

## Out of Scope *(deferred to later features)*

- **Publish-gate enforcement** (`assert_publishable`, the `409 publish.eval_gate` / `publish.low_confidence`
  responses) and wiring `DRIFT_TRIGGERS` to a persisted `publishable` flag — depends on the API + DB
  (M4/M5).
- **DB-backed baseline lifecycle** and audited re-freeze (`eval_baselines`, `audit_logs`).
- **The verdict cache** (`(case_id, baseline_hash, candidate_hash, judge_version)`) and its C2 hash-coverage
  / `latest_trigger_time` property tests.
- **The async eval worker**, Redis queue, `GET /jobs/:id` progress, and incremental eval — eval may run
  inline in this slice (M4/M11).
- **Cost controls** that need infrastructure: provider Batch API, single-order-pairwise optimization.
- **Judge calibration** (the κ meta-eval, human-label held-out set) and **production feedback loop** —
  calibration is what later flips the judge from advisory to gating.
- **Live/paid judge calls** — first live call deferred to Phase 5.
- **Langfuse spans / observability wiring** beyond the honest in-record fields.
- **Retrieval-quality eval, multi-judge ensembles, auto-suite-expansion, ClickHouse rollups** (all P2 in 05).
