# Data Model — Eval & Drift Engine (M3 slice)

All types are **pure, in-memory value objects** in `services/evals/types.py` (frozen dataclasses where
immutable). No DB tables in this slice — persistence (`eval_runs`/`eval_results`/`eval_baselines`) is deferred
to M4/M5. Field names track the frontend contract (`frontend/src/lib/data.ts`) so the M4 API serializes them
drop-in.

---

## EvalCase

One authored test case (maps to FE `EvalCaseDef` / `EvalCase`; `05 §2a`).

| Field | Type | Notes |
|---|---|---|
| `id` | `str` | Stable case id (part of the future verdict-cache key). |
| `input` | `str` | The user message / scenario (authored). |
| `vars` | `dict[str, str] \| None` | Typed values for the agent's io_contract input variables. |
| `assertions` | `tuple[Assertion, …]` | Deterministic checks (Layer 1). |
| `rubric` | `tuple[RubricDimension, …]` | Judge dimensions (name + weight), Layer 2. |
| `expected` | `str \| dict \| None` | Optional reference answer; when absent, score is inferred pairwise. |
| `required_fields` | `tuple[str, …] \| None` | Overrides the default triage contract (`severity,summary,next_step`). |
| `workspace_id` | `str` | Tenant the case belongs to (P5). |

**Assertion**: `{ kind: AssertionKind, detail: str \| None, hard: bool }`. `AssertionKind ∈ {schema,
required_fields, regex, exact, tool_called, no_refusal, non_empty}`. Kinds that carry a `detail` (`regex`,
`required_fields`, `tool_called`, `exact`) **require** it. `hard=True` forces case FAIL regardless of judge.

**RubricDimension**: `{ name: str, weight: float }` (e.g. `{accuracy, 0.5}`).

---

## DetResult  *(ported from mvp)*

Deterministic-check outcome for one case.

| Field | Type | Notes |
|---|---|---|
| `passed` | `bool` | All checks green. |
| `checks` | `dict[str, bool]` | e.g. `{"schema_valid": True, "required_fields": False, …}`. |
| `hard_fail` | `bool` | A hard failure forces case FAIL and **skips the judge** (FR-007). |

Hard-fail conditions: empty output, first-person refusal, unparseable/non-object structured output. Soft:
missing/empty required field, out-of-enum value, `expected` mismatch.

---

## JudgeVerdict

One judge outcome for one case (maps to `eval_results.judge_verdict`; `05 §2`).

| Field | Type | Notes |
|---|---|---|
| `rubric_scores` | `dict[str, float]` | Per-dimension 1–5. |
| `pairwise_winner` | `Literal["baseline","candidate","tie"]` | Order randomized to cancel position bias. |
| `rationale` | `str` | Human-readable justification. |
| `judge_version` | `str` | `"judge-v1"` — bump = explicit re-run. |
| `judge_model` | `str` | `"claude-haiku-5-5"` (pinned, P4). |
| `judge_prompt_hash` | `str` | Hash over the judge prompt + rubric template (P4). |
| `low_confidence` | `bool` | True if the judge output was unparseable/failed closed. |

**Advisory-only (FR-016):** no field of `JudgeVerdict` participates in the publish decision in this feature.

---

## EvalPolicy  *(ported from mvp; maps 1:1 to FE `EvalPolicy`)*

Per-workspace thresholds (`05 §1`; `data.ts:1064`).

| Field | Type | Default | Notes |
|---|---|---|---|
| `min_cases` | `int` | `10` | Below this → `low_confidence`, never `pass` (statistical honesty). |
| `quality_threshold` | `float` | `90` | Max tolerated quality drop vs baseline before FAIL. |
| `efficiency_budget_pct` | `float` | `10` | Max tolerated candidate cost increase % before FAIL. |
| `allow_low_confidence_override` | `bool` | `False` | (Publish-gate knob — gate deferred; carried for fidelity.) |

---

## EfficiencyDelta  *(ported from mvp)*

| Field | Type | Notes |
|---|---|---|
| `cost_pct` | `float` | Candidate cost % vs baseline (positive = more expensive). Baseline-zero handled. |

Method: `within_budget(pct) -> bool`.

---

## CaseScore

Per-case rolled-up result the aggregate is built from.

| Field | Type | Notes |
|---|---|---|
| `case_id` | `str` | |
| `det` | `DetResult` | Layer-1 outcome. |
| `judge` | `JudgeVerdict \| None` | `None` when hard-failed (pre-filter skipped the judge). |
| `quality` | `float` | 0–100 for this case (from rubric blend), or 0 on hard-fail. |
| `candidate_cost_usd` | `Decimal \| None` | From the candidate `RunRecord` (P7). |
| `candidate_latency_ms` | `int \| None` | From the candidate `RunRecord`. |
| `failed` | `bool` | Case did not pass (hard or soft). |

---

## Verdict  *(ported literal from mvp)*

`Verdict = Literal["pass", "fail", "low_confidence"]`.

---

## EvalRunResult  *(the full FE `EvalRun` shape — clarify decision 2 / FR-025)*

The suite-level aggregate the runner returns. Field names match `data.ts:855` `EvalRun`.

| Field | Type | Notes |
|---|---|---|
| `verdict` | `Verdict` | From the FR-008 ordering. |
| `reason` | `str` | Specific, case-grounded (FR-009). |
| `quality` | `float` | Candidate weighted quality 0–100. |
| `quality_delta` | `float` | `quality_candidate − quality_baseline`. |
| `cost_delta` | `float` | % (from `EfficiencyDelta.cost_pct`). |
| `latency_delta` | `float` | ms delta (surfaced, not gated). |
| `cases` | `int` | Suite size actually run (drives `low_confidence`). |
| `dimensions` | `tuple[DimensionScore, …]` | Per-rubric-dimension candidate scores. |
| `failing_cases` | `tuple[FailingCase, …]` | Cases that failed (det or quality). |
| `pairwise_rate` | `float` | Advisory: fraction of decided cases candidate won head-to-head (NOT gated). |
| `workspace_id` | `str` | Tenant (P5). |
| `judge_version` | `str` | Carried for provenance (P4). |

**DimensionScore**: `{ name: str, candidate: float, baseline: float, delta: float }`.
**FailingCase**: `{ case_id: str, reason: str, hard: bool }`.

---

## CircuitBreaker (state, not a persisted entity)

`services/evals/breaker.py` — **instance-scoped** (F8 / D4).

| Field | Type | Notes |
|---|---|---|
| `failure_threshold` | `int` | Consecutive provider failures before opening. |
| `reset_timeout_s` | `float` | Half-open retry delay. |
| `_state` | `Literal["closed","open","half_open"]` | Per-instance; never module-global. |
| `_consecutive_failures` | `int` | Reset on success. |

Invariant (F8): two instances share no state — one tripping leaves the other `closed`.

---

## Relationships

```text
EvalPolicy ──(thresholds)──▶ verdict()
EvalCase ──▶ run_deterministic() ──▶ DetResult ──(not hard_fail)──▶ judge_case() ──▶ JudgeVerdict
   │                                   │                                 │
   └──────────────▶ CaseScore ◀────────┴─────────────────────────────────┘
                        │
         score_quality() + efficiency_delta()  (drift.py)
                        │
                   verdict() + reasons
                        │
                 EvalRunResult (full FE EvalRun shape)

runner orchestrates all of the above; judge_case() calls runtime.invoke_result (P6);
breaker (instance) wraps the judge/candidate fan-out (F8).
```

All inputs for one run MUST share a `workspace_id` (P5); a mismatch raises a typed tenancy error.
