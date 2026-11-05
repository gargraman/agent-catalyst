# Contract — `services/evals/` public library interface (M3 slice)

This feature exposes a **Python library interface**, not an HTTP surface (the API is M4). The signatures
below are the contract later callers (the M4 eval endpoint, the worker) depend on. Types are defined in
`services/evals/types.py` (see data-model.md). Everything here is pure (P6) and fully mockable (no live calls).

> Convention: `▶ RED` marks the behavior a failing test must pin before implementation (P9). `runtime.invoke`
> dependencies are injected/mocked in tests — no live/paid calls (SC-005).

---

## `deterministic.py`

```python
def run_deterministic(output: str | dict, case: EvalCase) -> DetResult: ...
```

- **MUST** return `hard_fail=True` for: empty/whitespace output; a first-person refusal
  (`REFUSAL_PHRASES`, first-person-anchored); unparseable or non-object structured output. ▶ RED (FR-002/3/4)
- **MUST** return soft failure (`passed=False, hard_fail=False`) for: missing/empty required field; out-of-enum
  severity; `expected` mismatch. ▶ RED (FR-005)
- **MUST** honor `case.required_fields` override (task-agnostic; triage shape is only the default). ▶ RED (FR-006)
- **MUST** be deterministic: identical `(output, case)` → identical `DetResult` across repeated calls. ▶ RED (FR-022/F18/SC-004)

---

## `drift.py`

```python
def score_quality(rubric_scores: dict[str, float], weights: dict[str, float]) -> float: ...
def efficiency_delta(candidate, baseline) -> EfficiencyDelta: ...
def verdict(n_cases: int, det_all_green: bool, quality_b: float, quality_c: float,
            eff: EfficiencyDelta, policy: EvalPolicy) -> tuple[Verdict, str]: ...
```

- `score_quality` **MUST** normalize the 1–5 weighted blend to 0–100 (RUBRIC_MIN→0, RUBRIC_MAX→100). ▶ RED (FR-010)
- `efficiency_delta` **MUST** compute cost% (positive = candidate costlier) and **MUST NOT** divide by zero
  when baseline cost is 0. ▶ RED (FR-011, edge case)
- `verdict` **MUST** apply the ordering exactly: `n_cases < policy.min_cases` → `low_confidence` (even when
  also failing) → `not det_all_green` → `fail` → quality drop > `policy.quality_threshold` → `fail` →
  `not eff.within_budget(policy.efficiency_budget_pct)` → `fail` → else `pass`. ▶ RED (FR-008, US2 all ACs)
- `verdict` **MUST** return a specific, case-grounded reason string (not generic). ▶ RED (FR-009/SC-006)
- Thresholds **MUST** come from the passed `EvalPolicy`, never module constants. ▶ RED (FR-012)

---

## `judge.py`

```python
JUDGE_MODEL: str = "claude-haiku-5-5"
JUDGE_VERSION: str = "judge-v1"

async def judge_case(case: EvalCase, baseline_out: str, candidate_out: str, ctx,
                     *, rng=None) -> JudgeVerdict: ...
```

- **MUST** perform its model call through `runtime.invoke_result` (the same shared runtime path), not a
  private client. ▶ RED (FR-015/P6) — test asserts the mock runtime was called.
- **MUST** return a `JudgeVerdict` with rubric scores, `pairwise_winner`, rationale, and the versioned
  identity (`judge_version`, `judge_model`, `judge_prompt_hash`). ▶ RED (FR-014/FR-017/P4)
- **MUST** randomize pairwise presentation order via an injectable seedable `rng` (reproducible in tests). ▶ RED (FR-018/D5)
- **MUST** fail closed to `low_confidence=True` (never crash, never fabricate scores) on unparseable judge
  output. ▶ RED (edge case)
- **MUST NOT** make any live/paid call — fully mocked. ▶ RED (FR-019/SC-005)
- The returned verdict **MUST NOT** be consulted by any publish-decision code path in this feature
  (advisory-only). ▶ RED (FR-016/SC-007) — asserted at the runner level.

---

## `breaker.py`

```python
class CircuitBreaker:
    def __init__(self, *, failure_threshold: int = 5, reset_timeout_s: float = 30.0): ...
    async def call(self, coro_fn, *args, **kwargs): ...   # fail-fast when open
```

- **MUST** open (fail-fast) after `failure_threshold` consecutive provider failures and half-open after
  `reset_timeout_s`. ▶ RED
- Two instances **MUST** share no trip state — one opening leaves the other `closed` (F8). ▶ RED (FR-021/SC-008)

---

## `runner.py`

```python
async def run_eval(cases: Sequence[EvalCase], candidate_snapshot, baseline, policy: EvalPolicy,
                   ctx, *, workspace_id: str, breaker: CircuitBreaker | None = None,
                   rng=None) -> EvalRunResult: ...
```

- **MUST** run Layer-1 deterministic checks first and **skip the judge** for any hard-failing case. ▶ RED (FR-007/SC-002)
- **MUST** route judge/candidate provider fan-out through an **instance** `CircuitBreaker` (its own, not a
  global). ▶ RED (FR-021/F8)
- **MUST** reject inputs whose `workspace_id` disagrees across cases/baseline/candidate (fail closed). ▶ RED (FR-023/P5)
- **MUST** generate candidate outputs with caching disabled so a cached response can't mask a regression. ▶ RED (FR-024)
- **MUST** return the full FE `EvalRun`-shaped `EvalRunResult` (verdict, reason, quality, qualityDelta,
  costDelta, latencyDelta, cases, dimensions[], failingCases[], advisory pairwise_rate). ▶ RED (FR-025)
- **MUST** produce `fail` for a seeded-regression candidate and `pass` for an unchanged candidate — mechanical
  detection, not κ calibration. ▶ RED (SC-001)
- The judge verdict **MUST NOT** flip the returned `verdict` (advisory): removing the judge entirely changes
  only `dimensions`/`quality` provenance, never the gating outcome on a hard/stat failure. ▶ RED (FR-016/SC-007)

---

## Purity contract (`test_evals_purity.py`)

- `services/evals/*` **MUST** import no inbound web framework (`fastapi`, `starlette`) and no DB/ORM
  (`sqlalchemy`, `asyncpg`) — mirror of `test_runtime_purity.py`. ▶ RED (FR-020/P6/SC-008)
