# Quickstart — validating the Eval & Drift Engine (M3 slice)

How to prove this feature works end-to-end. Everything is **pure and mocked** — no running DB, no provider
credentials, no live/paid calls.

## Prerequisites

- `services/.venv` (existing) with pytest + pytest-asyncio. No new dependencies.
- Feature 003 (`services/runtime/`) present — the judge consumes `runtime.invoke_result`.

## Run the eval test suite

```bash
cd services
.venv/bin/python -m pytest tests/test_evals_*.py -q
```

Expected after implementation: all eval tests green, and the full services suite still green:

```bash
.venv/bin/python -m pytest -q          # whole services suite (no regressions)
```

## Validation scenarios (map to spec Success Criteria)

| # | Scenario | How to observe | Proves |
|---|---|---|---|
| 1 | **Deterministic hard-fails never reach the judge** | `test_evals_runner.py`: feed empty / refusal / malformed outputs; assert the mocked `runtime.invoke_result` was **not** called for those cases. | SC-002, FR-007 |
| 2 | **Seeded-regression detection** | `test_evals_runner.py`: a degraded candidate (lower rubric scores / higher cost) → `verdict == "fail"`; an unchanged candidate → `"pass"`. | SC-001 |
| 3 | **Statistical honesty floor** | `test_evals_verdict.py`: a suite below `policy.min_cases` → `"low_confidence"`, never `"pass"`, even when also failing. | SC-003, FR-008 |
| 4 | **Determinism** | `test_evals_deterministic.py`: run the same `(output, case)` twice; assert identical `DetResult`. | SC-004, FR-022 |
| 5 | **No live/paid calls** | All judge tests inject a fake `invoke_result`; a guard double raises if a real provider path is hit. | SC-005, FR-019 |
| 6 | **Specific reasons** | `test_evals_verdict.py`: each non-pass verdict's reason names the failing axis + magnitude. | SC-006, FR-009 |
| 7 | **Judge is advisory** | `test_evals_runner.py`: with the judge removed/stubbed, the gating `verdict` is unchanged on a hard/stat failure; no publish-decision code consults the judge. | SC-007, FR-016 |
| 8 | **Purity + independent breaker** | `test_evals_purity.py` asserts no web/DB imports; `test_evals_breaker.py` asserts two breaker instances don't share trip state. | SC-008, FR-020, FR-021 |
| 9 | **Full EvalRun shape** | `test_evals_runner.py`: the returned `EvalRunResult` carries verdict, reason, quality, qualityDelta, costDelta, latencyDelta, cases, dimensions[], failingCases[], pairwise_rate. | FR-025 |
| 10 | **Cross-workspace rejected** | `test_evals_tenancy.py`: cases/baseline/candidate with mismatched `workspace_id` → typed tenancy error. | FR-023, P5 |

## What this slice does NOT prove (honest deferrals)

- No publish-gate enforcement, no `409` — the judge is advisory; the gate is M4/M5.
- No DB persistence, no verdict cache, no async worker/job status — eval runs inline here.
- No live judge call and **no κ calibration** — SC-001 is mechanical plumbing evidence only, explicitly not
  the κ≥0.60 human-rater calibration that would later let the judge gate publish.
- No Batch-API cost optimization, no incremental eval.

See `plan.md` *Constitution Check* and `spec.md` *Out of Scope* for the full deferral list.
