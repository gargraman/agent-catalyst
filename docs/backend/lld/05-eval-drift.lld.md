# 05 (LLD) — Eval & Drift Engine (hard core #2)

> **Parent:** [`../05-eval-drift.md`](../05-eval-drift.md) (the drift definition, the two scoring layers,
> `verdict()`, `DRIFT_TRIGGERS`, the publish gate, cost levers).
> The parent nails the *flow and the gate*, but leaves the **scoring math as docstrings**: `score_quality`,
> `efficiency_delta`, and the rubric↔pairwise fusion are `"""..."""` only, and `CaseScore`/`EfficiencyDelta`/
> `RunAgg` are referenced but undefined. It also leaves judge-output validation, the eval error path, the
> concurrent-run race, incremental eval, and `generateStarterEval` to the implementer. This doc writes those.

## What this adds over parent `05`

| Gap in `05` | Filled here |
|---|---|
| `score_quality(...) -> float` is a docstring; `CaseScore` undefined; rubric↔pairwise fusion "never stated" | §1–§2 — `CaseScore`, the 1–5→0–100 normalization, the weighted blend, and how pairwise fuses in |
| `efficiency_delta(...)` / `EfficiencyDelta.within_budget(EFF_BUDGET)` / `RunAgg` referenced, not defined | §3 — all three, with the budget predicate |
| judge may return non-JSON / out-of-range; `JudgeVerdict` has no validation or error path | §5 — output validation, one strict-reprompt retry, `JudgeError`, close-call ε |
| `eval_failed` notification named; no eval error taxonomy (judge/batch/runtime failure) | §6 — the worker error model (job + notification, **not** HTTP) |
| two queued runs on one version racing to set `publishable` not addressed | §7 — the per-version advisory lock on the worker write path |
| "incremental eval (tagged cases)" — tagging mechanism undefined | §8 — `cases_to_run`, case tags, the honest P1 scope |
| `generateStarterEval` behavior described, no signature/logic | §9 — signature + generation approach |
| no validation bounds on `EvalPolicy` values | §10 — the Pydantic model with ranges |
| judge calibration κ "target documented" — value absent | §5.4 — κ ≥ 0.6 target |

Python 3.12, `from __future__ import annotations`. Snippets compile (§11). `verdict()`, `DRIFT_TRIGGERS`,
`judge_cache_key`, `JudgeVerdict`, `DetResult`, the publish gate `assert_publishable`: **see `05` — not
restated.** Exceptions reuse [`00-conventions.lld.md §1`](00-conventions.lld.md); `snapshot_hash` is the
verdict-cache key from [`01-data-model.lld.md §4`](01-data-model.lld.md).

---

## 1. `CaseScore` and the scoring pipeline

`05 §2` produces two judgments per case — an absolute **rubric** (each dimension 1–5) and a **pairwise**
winner (baseline vs candidate). The parent never says how they combine. The design:

- **Rubric → the quality number** (0–100). This is the FE `EvalRun.quality` / `EvalDimension.score`.
- **Pairwise → an independent gate signal**, not folded into the quality number. A candidate can score a flat
  rubric yet clearly lose head-to-head ("technically fine, obviously worse"); pairwise catches that. It
  contributes a `pairwise_win_rate` the verdict reads separately.

Keeping them separate is deliberate: blending pairwise into quality would double-count and make the FE
`dimensions[]` (pure rubric) inconsistent with the headline number.

```python
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class CaseScore:
    case_id: str
    quality: float                       # 0–100, this case's weighted rubric blend (§2)
    dimension_scores: dict[str, float]   # per-dimension mean on the 0–100 scale (→ FE EvalDimension.score)
    pairwise_winner: Literal["baseline", "candidate", "tie"]
    hard_fail: bool                      # a deterministic hard-assertion failed (05 §2 Layer 1) → case fails
    judge_errored: bool = False          # judge output unusable after retry (§5); excluded from the quality blend
```

---

## 2. `score_quality` — the body (fills `05 §3`)

```python
def _dim_to_100(mean_1_5: float) -> float:
    """One rubric dimension's 1–5 mean → 0–100. 1→0, 3→50, 5→100 (linear)."""
    return (max(1.0, min(5.0, mean_1_5)) - 1.0) / 4.0 * 100.0


def case_quality(rubric_means: dict[str, float], weights: dict[str, float]) -> tuple[float, dict[str, float]]:
    """One case: per-dimension 1–5 means + rubric weights → (quality 0–100, per-dimension 0–100 scores).
    Weights are normalized so they need not sum to 1 in the authored rubric (FE RubricDimension.weight)."""
    dims = {d: _dim_to_100(rubric_means[d]) for d in rubric_means}
    total_w = sum(weights.get(d, 0.0) for d in dims) or 1.0
    quality = sum(dims[d] * weights.get(d, 0.0) for d in dims) / total_w
    return quality, dims


def score_quality(results: list[CaseScore], rubric_weights: dict[str, float]) -> float:
    """Suite quality (FE EvalRun.quality): mean of per-case quality over the SCORED cases.
    Hard-failed and judge-errored cases are EXCLUDED from the mean — a hard fail is handled by the verdict
    (det_all_green, 05 §3), not by dragging the quality number; a judge error is our fault, not the agent's."""
    scored = [c.quality for c in results if not c.hard_fail and not c.judge_errored]
    return sum(scored) / len(scored) if scored else 0.0
```

**Why exclude hard-fails from the quality mean:** `verdict()` (`05 §3`) already fails the run on
`not det_all_green`, so folding a hard-failed case's (likely low) quality into the mean would double-penalize
*and* muddy the quality delta the FE shows. Hard assertions are a floor; quality is a separate axis (`05 §1`).

---

## 3. `efficiency_delta`, `RunAgg`, `within_budget` (fills `05 §3`)

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class RunAgg:
    """Aggregate of a candidate (or baseline) run across the suite — all from the gateway IR (02)."""
    total_cost_usd: float
    mean_latency_ms: float
    total_tokens: int
    mean_tool_calls: float


@dataclass(frozen=True)
class EfficiencyDelta:
    cost_pct: float          # (cand - base) / base * 100  → FE EvalRun.costDelta
    latency_ms: float        # cand - base (mean)          → FE EvalRun.latencyDelta
    token_delta: int         # cand - base (total)
    tool_call_delta: float   # cand - base (mean)

    def within_budget(self, budget_pct: float) -> bool:
        """The gate is COST (05 §1 efficiency axis): candidate may not exceed baseline cost by > budget_pct.
        Latency/tokens/tool-calls are reported (FE shows them) but do NOT gate in P1 — cost is the COGS lever
        the product sells. (A latency budget is a P2 policy knob.)"""
        return self.cost_pct <= budget_pct


def efficiency_delta(candidate: RunAgg, baseline: RunAgg) -> EfficiencyDelta:
    def pct(c, b): return ((c - b) / b * 100.0) if b else 0.0
    return EfficiencyDelta(
        cost_pct=pct(candidate.total_cost_usd, baseline.total_cost_usd),
        latency_ms=candidate.mean_latency_ms - baseline.mean_latency_ms,
        token_delta=candidate.total_tokens - baseline.total_tokens,
        tool_call_delta=candidate.mean_tool_calls - baseline.mean_tool_calls,
    )
```

These feed `verdict()` (`05 §3`) unchanged: `eff.within_budget(policy.efficiency_budget_pct)`. `cost_pct` maps
1:1 to FE `EvalRun.costDelta`, `latency_ms` to `latencyDelta` — the reconciliation the parent asserts.

---

## 4. Wiring into `verdict()` + the pairwise gate

`verdict()` (`05 §3`) stays as the parent wrote it. One addition this LLD makes explicit: the **pairwise
gate**, which `05 §2` describes ("which better satisfies the case?") but `verdict()` doesn't consume.

```python
PAIRWISE_LOSS_LIMIT = 0.5     # candidate may not lose head-to-head on > 50% of decided cases

def pairwise_win_rate(results: list[CaseScore]) -> float:
    decided = [c for c in results if c.pairwise_winner in ("baseline", "candidate")]
    if not decided:
        return 1.0
    wins = sum(1 for c in decided if c.pairwise_winner == "candidate")
    return wins / len(decided)
```

This catches the "rubric flat but clearly worse than baseline" regression the quality axis can miss — but it
needs `verdict()` to **see** the pairwise result, which the parent signature doesn't pass.

> ⚠️ **Amendment to `05 §3`:** `verdict()` must gain the pairwise signal and a branch. The parent signature,
> `verdict(quality_c, quality_b, eff, det_all_green, n_cases, policy)`, has **no** `results`/pairwise parameter,
> so the branch can't be added as-is — it needs the precomputed rate passed in (shown below). The parent runs
> pairwise (`§2`) and caches it (`§6`) but never gates on it; without this a head-to-head loss with a flat
> rubric passes. Flagged, not edited in the parent.

```python
# the amended verdict() — one new parameter + one branch:
def verdict(quality_c, quality_b, eff, det_all_green, n_cases, policy, pairwise_rate):
    ...                                             # existing checks unchanged (05 §3)
    if pairwise_rate < (1 - PAIRWISE_LOSS_LIMIT):   # candidate lost head-to-head on > PAIRWISE_LOSS_LIMIT of cases
        return "fail", _reason_pairwise(pairwise_rate)
    return "pass", _reason_pass()
# caller: verdict(..., pairwise_rate=pairwise_win_rate(results))
```

---

## 5. The judge: output contract, validation, failure

### 5.1 Output contract

The judge returns ONE JSON object per `judge_case` call (`05 §2`), validated on parse:

```python
from __future__ import annotations
from pydantic import BaseModel, Field, field_validator
from typing import Literal


class JudgeOutput(BaseModel):
    rubric_scores: dict[str, float]      # dimension → 1..5
    pairwise_winner: Literal["baseline", "candidate", "tie"]
    rationale: str = Field(min_length=1)

    @field_validator("rubric_scores")
    @classmethod
    def _scores_in_range(cls, v: dict[str, float]) -> dict[str, float]:
        for dim, s in v.items():
            if not (1.0 <= s <= 5.0):
                raise ValueError(f"rubric score for {dim} out of 1..5: {s}")
        return v
```

### 5.2 Malformed-output handling

Judge models occasionally emit non-JSON, extra prose, or an out-of-range score. The policy — **our judge
flakiness must never silently fail the agent** (`05 §8` "a drift detector you don't trust is worthless"):

```python
class JudgeError(Exception):
    """Judge output unusable after one strict-reprompt retry. Caught by the worker → case.judge_errored=True."""


async def judge_case_validated(case, baseline_out, candidate_out, *, call_judge) -> "JudgeOutput | JudgeError":
    for attempt in (1, 2):
        raw = await call_judge(case, baseline_out, candidate_out, strict=(attempt == 2))  # 2nd adds a strict JSON reminder
        try:
            return JudgeOutput.model_validate_json(_extract_json(raw))
        except Exception:
            if attempt == 2:
                return JudgeError(f"case {case.id}: judge output invalid after retry")
            continue
```

Downstream: a `JudgeError` → `CaseScore(judge_errored=True)` (excluded from the quality blend, §2). If the
**share of judge-errored cases exceeds `JUDGE_ERROR_ABORT = 0.2`**, the run is aborted as `eval_failed` (§6) —
an *error*, not a `fail` verdict — because the result can't be trusted. A few scattered judge errors just
shrink the scored set (and may tip `n_cases < min_cases` → `low_confidence`, which is the honest outcome).

### 5.3 Close-call ε (fills `05 §6` "margin < ε")

```python
EPSILON_CLOSE = 5.0     # 0–100 quality points

def needs_both_orders(cand_quality: float, base_quality: float) -> bool:
    """Run pairwise in BOTH orders (to cancel position bias) only when the case is close — a cost knob
    (05 §6). When the candidate clearly wins/loses on rubric, one order suffices."""
    return abs(cand_quality - base_quality) < EPSILON_CLOSE
```

### 5.4 Calibration target (fills `05 §8`)

Judge agreement with human labels on the held-out calibration set: **Cohen's κ ≥ 0.6** (substantial
agreement) is the ship bar; recompute on every `JUDGE_VERSION` bump. This operationalizes `05 §8`'s "target
documented" and backstops `scope.md` metric 2 (≥80% seeded-regression detection, <10% FP) — if κ drops below
0.6, the judge is recalibrated before the detection metric is trusted.

---

## 6. Eval error taxonomy (the `eval_failed` path)

Eval runs are async jobs (`05 §7`), so their failures are **not** HTTP errors — they surface via `jobs.status`
+ an owner notification, per the `00-conventions.lld.md §1` category model (worker-internal, category B/C, not
`AppError`).

```python
class EvalRunError(Exception):
    """A run that could not produce a trustworthy verdict (vs. a FAIL verdict, which IS trustworthy)."""

class BatchSubmitError(EvalRunError): ...     # provider Batch API rejected/failed (05 §6 Batch lever)
# JudgeError (§5) with >JUDGE_ERROR_ABORT share → raised as EvalRunError too.
```

Worker handling:
- `EvalRunError` (or an unrecovered `runtime.invoke` failure producing candidates) → `jobs.status='failed'`
  with `error` set, `eval_runs` row **not** written (or written with no verdict), and a
  `notification_kind='eval_failed'` emitted to the agent owner (`01 §4.9`, `16`), deduped per
  `(agent_id, kind, trigger)` like the drift notices (`05 §5`).
- This is distinct from `verdict='fail'`: a FAIL is a *successful* eval that found a regression (job
  `completed`, agent → `eval_failing`); `eval_failed` is the eval itself breaking (job `failed`, agent status
  untouched). Conflating them would either hide real regressions or cry wolf on judge/infra blips.

---

## 7. Concurrency: the `publishable` write race

`05 §5` puts a per-version advisory lock on the **publish gate**. The symmetric race is on the **eval worker**:
two runs queued for the same version both finish and both try to set `publishable`. Without coordination, an
older run completing *after* a newer trigger could set `publishable=True` stale.

```python
def finalize_eval_run(version_id: str, run, db) -> None:
    """Worker write path. Same per-version advisory lock as assert_publishable (05 §5) so the gate-read and
    the flag-write can't interleave with a concurrent finalize or a new trigger."""
    db.execute(text("select pg_advisory_xact_lock(hashtextextended(:v, 0))"), {"v": version_id})
    db.add(run)                                            # eval_runs + eval_results
    last_trigger = latest_trigger_time(version_id)
    # Only a PASS that is NEWER than the last behavior-changing edit may arm publish (05 §5 condition, mirrored).
    if run.verdict == "pass" and run.started_at >= last_trigger:
        set_publishable(version_id, True, db)
    # a FAIL / low_confidence / stale PASS leaves publishable untouched (default False after a trigger)
```

Using the **same lock key** (`hashtextextended(version_id, 0)`) as `assert_publishable` means publish and
finalize fully serialize on a version: no finalize can set `publishable=True` while a publish is mid-check, and
no two finalizes interleave. This closes the worker-side half of the TOCTOU the parent only closed at publish.

---

## 8. Incremental tagged-case eval (fills `05 §6`)

`05 §6` says "on a prompt-only change, re-run only cases whose inputs exercise the change (tagged cases); full
suite on model swap." The mechanism:

```python
# eval_cases gain an optional tags[] (jsonb; additive to 01 §4.5 — see amendment). A tag names a prompt
# section or io_contract variable a case exercises, e.g. "prompt:refusal", "var:severity_hint".

FULL_SUITE_TRIGGERS = {"model", "provider", "api_version", "embedding_model", "kb_binding", "retrieval_config"}

def cases_to_run(changed: set[str], suite_cases: list, changed_tags: set[str]) -> list:
    """Which cases to re-run for a given change set (a subset of DRIFT_TRIGGERS, 05 §5)."""
    if changed & FULL_SUITE_TRIGGERS:
        return suite_cases                                 # anything model/RAG-level ⇒ full suite
    if not changed_tags:                                   # prompt/guardrail edit with no tag info ⇒ be safe
        return suite_cases
    # prompt/guardrail/skill edit with tag info ⇒ only the cases touching the changed areas
    return [c for c in suite_cases if set(c.tags or []) & changed_tags] or suite_cases
```

- **P1 honesty:** tags are optional and most seed suites won't have them, so `cases_to_run` **defaults to the
  full suite** whenever tag info is absent — the cost saving only kicks in once authors tag cases. This matches
  the parent's "fewer cases per trigger" as an *optimization*, never a correctness shortcut (a missed case is a
  missed regression, so the default must be safe).
- `changed_tags` derivation (which prompt sections a prompt diff touched) is a P2 refinement; P1 can pass the
  whole changed prompt's tags or fall back to full suite.

> ⚠️ **Amendment to `01 §4.5`:** add `eval_cases.tags text[] not null default '{}'` (matching the repo's
> existing tag convention — `agents.tags text[]`, 01 §4.2 — not jsonb; `text[]` is the queryable idiomatic
> choice) and FE `EvalCaseDef.tags?: string[]`. Additive, nullable-safe. Flagged for the `01` reconciliation.

---

## 9. `generateStarterEval` (fills `05 §2a`)

```python
STARTER_CASE_COUNT = 12     # > default MIN_CASES (10) so the first verdict is confident (05 §2a)

async def generate_starter_eval(agent, *, call_model) -> "EvalSuite":
    """Synthesize an editable 12-case starter suite from the agent's own contract. Inferred ONCE, then owned
    by the author (05 §2a). Uses the cheap tier model (07), not the judge."""
    # 1. Prompt the cheap model to produce 12 diverse, realistic inputs that exercise the agent's purpose,
    #    seeded from system_prompt + io_contract.input_variables (so vars are typed-valid).
    inputs = await _synth_inputs(agent.system_prompt, agent.io_contract, n=STARTER_CASE_COUNT, call_model=call_model)
    # 2. Default criteria: safe deterministic floor + a generic rubric. The author edits from here.
    cases = [
        EvalCaseDef(
            input=i.message, vars=i.vars,
            assertions=[Assertion(kind="no_refusal", hard=False)]
                       + ([Assertion(kind="schema_valid", hard=True)] if agent.io_contract.output_format == "json" else [])
                       + ([Assertion(kind="has_citation", hard=False)] if agent.io_contract.include_citations else []),
            rubric=DEFAULT_RUBRIC,              # FE DEFAULT_RUBRIC (data.ts)
            expected=None,                      # pairwise vs baseline, not golden (05 §2a)
        ) for i in inputs
    ]
    return EvalSuite(agent_id=agent.id, name="Starter set", cases=cases)
```

Assertions are deliberately conservative (nothing that would false-fail a reasonable answer); the rubric is the
FE default. The value is a *confident first verdict* + a real editing target, not a perfect suite.

---

## 10. `EvalPolicy` validation bounds

FE `EvalPolicy` (`data.ts`) has no bounds; the API must reject nonsense (a 150% quality threshold, 0 min
cases) before it drives the gate (`05 §1`).

```python
from pydantic import BaseModel, ConfigDict, Field


class EvalPolicy(BaseModel):
    model_config = ConfigDict(populate_by_name=True)   # accept snake_case (min_cases=) OR the FE camelCase alias
    min_cases: int = Field(10, ge=1, le=1000, alias="minCases")                 # < 1 is meaningless; cap sanity
    quality_threshold: float = Field(90.0, ge=0.0, le=100.0, alias="qualityThreshold")
    efficiency_budget_pct: float = Field(10.0, ge=0.0, le=1000.0, alias="efficiencyBudgetPct")  # % over baseline
    allow_low_confidence_override: bool = Field(False, alias="allowLowConfidenceOverride")
```

Stored on `workspaces.eval_*` columns (`01 §4.1`); `PUT …/eval-policy` validates against this (422
`input.invalid` on a breach, `00-conventions §1`). The per-run quality-threshold override (`05 §1`) reuses the
same `0..100` bound.

---

## 11. Verification performed

1. **Snippet compile** — every `python` block via `compile()` (same harness as `01`/`00`/`02` LLD).
2. **Cross-ref** — against `05` read in full: `verdict()`/`DetResult`/`JudgeVerdict`/`DRIFT_TRIGGERS`/
   `judge_cache_key`/`assert_publishable` (`§3`/§5`/§6`), the two scoring layers (`§2`), starter set (`§2a`),
   calibration (`§8`); `01 §4.5` eval DDL; `01-data-model.lld.md §4` (`snapshot_hash` cache key);
   `00-conventions.lld.md §1` (error categories); FE `EvalRun`/`EvalDimension`/`RubricDimension`/`EvalPolicy`/
   `AssertionKind` (`data.ts`).
3. **FE reconciliation** — `score_quality`→`EvalRun.quality`, `dimension_scores`→`EvalDimension.score`,
   `efficiency_delta.cost_pct`→`costDelta`, `latency_ms`→`latencyDelta`; `EvalPolicy` aliases match the FE
   camelCase fields.
4. **Two flagged amendments** — the `verdict()` pairwise branch (§4) and `eval_cases.tags` (§8), recorded as
   callouts, not parent edits.

→ Next LLD: `03-runtime.lld.md` (subtask 5, the shared spine).
