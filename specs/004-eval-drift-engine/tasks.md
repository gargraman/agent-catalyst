---
description: "Task list — Eval & Drift Engine (M3 walking-skeleton slice)"
---

# Tasks: Eval & Drift Engine (M3 — walking-skeleton slice)

**Input**: Design documents from `specs/004-eval-drift-engine/`
**Prerequisites**: plan.md ✓, spec.md ✓, research.md ✓, data-model.md ✓, contracts/ ✓
**Tests**: REQUIRED — the constitution (P9) mandates genuine behavioral Red→Green→Refactor; every code task
names its RED test and focused command. No live/paid calls anywhere (SC-005) — all runtime calls mocked.

**Run command (all eval tests)**: `cd services && .venv/bin/python -m pytest tests/test_evals_*.py -q`
**Full regression**: `cd services && .venv/bin/python -m pytest -q`

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1 / US2 / US3 (user-story tasks only)
- All paths are repo-relative; source in `services/evals/`, tests in `services/tests/`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Package + pure data scaffolding + test harness (no tested behavior → may precede RED, P9).

- [X] T001 Create the `services/evals/` package: `services/evals/__init__.py` and `services/evals/types.py` with the pure data dataclasses per data-model.md — `EvalCase`, `Assertion` (`kind ∈ {schema, required_fields, regex, exact, tool_called, no_refusal, non_empty}`, `detail`, `hard`), `RubricDimension` (`name`, `weight`), `DetResult` (`passed`, `checks`, `hard_fail`), `JudgeVerdict` (`rubric_scores`, `pairwise_winner`, `rationale`, `judge_version`, `judge_model`, `judge_prompt_hash`, `low_confidence`), `EvalPolicy` (defaults `min_cases=10`, `quality_threshold=90`, `efficiency_budget_pct=10`, `allow_low_confidence_override=False`), `EfficiencyDelta` (`cost_pct` + `within_budget(pct)` signature only), `CaseScore`, `DimensionScore`, `FailingCase`, `EvalRunResult` (full FE EvalRun fields), and `Verdict = Literal["pass","fail","low_confidence"]`. Data containers only — no scoring/verdict logic here.
- [X] T002 [P] Add `services/tests/_evals_helpers.py`: a fake async `invoke_result` double returning a canned `InvokeResult` (text + `RunRecord` carrying `cost_usd`/`latency_ms`/`outcome`); `make_eval_case(...)` / `make_candidate_snapshot(...)` builders; and a `boom_invoke` guard that raises `AssertionError` if any live provider path is hit (mirrors M2a's `_boom_gateway`).

**Checkpoint**: package imports; types available; test doubles ready.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Lock the P6 purity guard before any logic lands. BLOCKS nothing functionally but must stay green.

- [X] T003 Write `services/tests/test_evals_purity.py` (mirror `test_runtime_purity.py`): assert every module under `services/evals/` imports no inbound web framework (`fastapi`, `starlette`) and no DB/ORM (`sqlalchemy`, `asyncpg`). Focused: `pytest tests/test_evals_purity.py`. (FR-020 / P6 / SC-008)

**Checkpoint**: purity guard present and green on the empty package.

---

## Phase 3: User Story 1 — Deterministic correctness floor (Priority: P1) 🎯 MVP

**Goal**: Zero-model Layer-1 checks; hard-fails force FAIL and skip the judge.
**Independent Test**: feed valid/empty/refusal/malformed/missing-field/out-of-enum outputs → assert each `DetResult` (passed / checks / hard_fail). No model, no DB.

- [X] T004 [P] [US1] **RED** — write `services/tests/test_evals_deterministic.py`, porting `mvp/tests/test_deterministic.py` and adding: empty/whitespace → `hard_fail` (FR-002); first-person refusal ("I'm unable to…") → `hard_fail` **and** third-person "the service was unable to start" → `no_refusal` stays true (FR-003); unparseable / non-object JSON → `schema_valid=False`, `hard_fail` (FR-004); missing/empty required field, out-of-enum severity, `expected` mismatch → soft (`passed=False`, `hard_fail=False`) (FR-005); `case.required_fields` override respected (FR-006); same `(output, case)` twice → identical `DetResult` (FR-022/SC-004). Confirm it FAILS (module absent). Focused: `pytest tests/test_evals_deterministic.py`.
- [X] T005 [US1] **GREEN** — port `mvp/evals/deterministic.py` → `services/evals/deterministic.py`: `run_deterministic(output, case) -> DetResult`, the first-person-anchored `REFUSAL_PHRASES`, `SEVERITY_ENUM`, `DEFAULT_REQUIRED=("severity","summary","next_step")`, `_is_empty`; build `DetResult` from `types.py`; honor `case.required_fields`. Make T004 green; no wall-clock/RNG/unordered iteration (F18). Focused: `pytest tests/test_evals_deterministic.py`.

**Checkpoint**: US1 is a working, zero-cost MVP — it can flag broken candidate output with no judge.

---

## Phase 4: User Story 2 — Honest drift verdict vs frozen baseline (Priority: P2)

**Goal**: Turn per-case results + quality/cost aggregates into `pass`/`fail`/`low_confidence` with a specific reason, in the load-bearing order, under a per-workspace policy.
**Independent Test**: drive `verdict(...)` with synthetic inputs across each branch; assert verdict + specific reason; test `score_quality` and `efficiency_delta` separately.

- [X] T006 [P] [US2] **RED** — write `services/tests/test_evals_score_quality.py`, porting `mvp/tests/test_score_quality.py`: `score_quality` normalizes the 1–5 weighted blend to 0–100 (RUBRIC_MIN→0, RUBRIC_MAX→100, empty→0, no-weights→equal) (FR-010); `efficiency_delta` yields cost% (positive=costlier) and handles baseline cost 0 without dividing by zero (FR-011). Confirm FAILS. Focused: `pytest tests/test_evals_score_quality.py`.
- [X] T007 [P] [US2] **RED** — write `services/tests/test_evals_verdict.py`, porting `mvp/tests/test_verdict.py` and adding the US2 acceptance scenarios: sub-`min_cases` **and** failing → `low_confidence` (statistical honesty first, AC1); `min_cases` met + hard-assert failed → `fail` (AC2); quality drop > `quality_threshold` → `fail` naming the drop (AC3); over `efficiency_budget_pct` → `fail` with "cost +N%" (AC4); all within policy → `pass` (AC5); two workspace policies → verdicts may differ (AC6, FR-012); every non-pass reason is specific (FR-009/SC-006). Confirm FAILS. Focused: `pytest tests/test_evals_verdict.py`.
- [X] T008 [US2] **GREEN** — port `mvp/evals/drift.py` → `services/evals/drift.py`: `score_quality`, `efficiency_delta` (with `EfficiencyDelta.within_budget`), `verdict(n_cases, det_all_green, quality_b, quality_c, eff, policy) -> tuple[Verdict, str]` applying the ordering **exactly** (FR-008), the `_reason_*` helpers (FR-009), `RUBRIC_MIN/MAX`, and the `DRIFT_TRIGGERS` constant (ported for later; not wired to a gate here). Thresholds read from the passed `EvalPolicy` only (FR-012). Make T006+T007 green. Focused: `pytest tests/test_evals_score_quality.py tests/test_evals_verdict.py`.

**Checkpoint**: US1 + US2 give a complete honest verdict with quality supplied synthetically (judge still stubbed).

---

## Phase 5: User Story 3 — Advisory LLM judge via the same `runtime.invoke` (Priority: P3)

**Goal**: A versioned, mocked, advisory judge that scores quality through the shared `runtime.invoke_result`.
**Independent Test**: with `invoke_result` mocked, `judge_case` returns a versioned `JudgeVerdict`; asserts the shared runtime was called; pairwise order randomized (seeded); no live call; verdict never gates publish.

- [X] T009 [P] [US3] **RED** — write `services/tests/test_evals_judge.py`: `judge_case(case, baseline_out, candidate_out, ctx, *, rng)` (with a fake `invoke_result`) returns a `JudgeVerdict` carrying `rubric_scores` (1–5), `pairwise_winner ∈ {baseline,candidate,tie}`, `rationale`, and versioned identity `judge_version="judge-v1"`, `judge_model="claude-haiku-5-5"`, `judge_prompt_hash` (FR-014/FR-017); asserts the mock `invoke_result` was invoked — not a private client (FR-015/P6); pairwise presentation order randomized via the injected seedable `rng`, reproducible across two seeded runs (FR-018/D5); unparseable judge output → `low_confidence=True`, no crash, no fabricated scores (edge case); the `boom_invoke` guard proves no live/paid call (FR-019/SC-005). Confirm FAILS. Focused: `pytest tests/test_evals_judge.py`.
- [X] T010 [US3] **GREEN** — implement `services/evals/judge.py`: module constants `JUDGE_MODEL="claude-haiku-5-5"`, `JUDGE_VERSION="judge-v1"`; a `judge_prompt_hash` over the prompt + rubric template; build a judge-agent snapshot pinned to the judge model; assemble the rubric + pairwise prompt (order via injected `rng`); call `runtime.invoke_result`; parse the JSON verdict (reuse the deterministic JSON-validity check); fail closed to `low_confidence` on unparseable output. Make T009 green. Focused: `pytest tests/test_evals_judge.py`.

**Checkpoint**: all three pure layers exist and are independently green.

---

## Phase 6: Cross-cutting integration — runner + breaker + tenancy

**Purpose**: Wire the three layers into one `run_eval`, realizing the cross-cutting FRs (pre-filter, breaker, tenancy, full aggregate, seeded-regression, advisory-at-runner). Depends on Phases 3–5.

- [X] T011 [P] **RED** — write `services/tests/test_evals_breaker.py`: a `CircuitBreaker(failure_threshold, reset_timeout_s)` opens (fail-fast) after N consecutive failures and half-opens after the timeout; **two instances share no trip state** — one opening leaves the other `closed` (FR-021/F8/SC-008). Confirm FAILS. Focused: `pytest tests/test_evals_breaker.py`.
- [X] T012 **GREEN** — implement `services/evals/breaker.py`: instance-scoped `CircuitBreaker` with `async call(coro_fn, *args, **kwargs)` (closed/open/half_open, per-instance `_consecutive_failures`), no module-global state. Make T011 green. Focused: `pytest tests/test_evals_breaker.py`.
- [X] T013 [P] **RED** — write `services/tests/test_evals_tenancy.py`: `run_eval` rejects inputs whose `workspace_id` disagrees across cases / baseline / candidate with a typed tenancy error, fail-closed, before any judge call (FR-023/P5). Confirm FAILS. Focused: `pytest tests/test_evals_tenancy.py`.
- [X] T014 [P] **RED** — write `services/tests/test_evals_runner.py`: (a) hard-failed cases skip the judge — the mock `invoke_result` is **not** called for them (FR-007/SC-002); (b) a seeded-regression candidate → `verdict=="fail"`, an unchanged candidate → `"pass"` (SC-001); (c) the returned `EvalRunResult` carries the full FE EvalRun shape — `verdict`, `reason`, `quality`, `quality_delta`, `cost_delta`, `latency_delta`, `cases`, `dimensions[]`, `failing_cases[]`, advisory `pairwise_rate` (FR-025); (d) stubbing the judge out does **not** change a hard/statistical-failure verdict (advisory, FR-016/SC-007); (e) the candidate `ctx` is built with caching disabled (FR-024). Confirm FAILS. Focused: `pytest tests/test_evals_runner.py`.
- [X] T015 **GREEN** — implement `services/evals/runner.py` `run_eval(cases, candidate_snapshot, baseline, policy, ctx, *, workspace_id, breaker=None, rng=None) -> EvalRunResult`: (1) tenancy check first (T013); (2) a per-run instance `CircuitBreaker` (T012) wrapping judge/candidate fan-out; (3) deterministic pre-filter — skip the judge on `hard_fail` (T005); (4) judge via the breaker (T010); (5) build `CaseScore` list; (6) `score_quality` + `efficiency_delta` + `verdict` (T008); (7) assemble the full `EvalRunResult` (dimensions[], failing_cases[], advisory pairwise_rate, latency_delta); candidate `ctx` has caching disabled (FR-024). Make T013 + T014 green. Focused: `pytest tests/test_evals_runner.py tests/test_evals_tenancy.py`.

**Checkpoint**: the engine runs end-to-end (mocked) and emits a drop-in FE `EvalRun`-shaped verdict.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T016 [P] Export the public surface from `services/evals/__init__.py` (`run_eval`, `run_deterministic`, `score_quality`, `efficiency_delta`, `verdict`, `judge_case`, `CircuitBreaker`, and the `types` value objects).
- [X] T017 Run the full services suite — `cd services && .venv/bin/python -m pytest -q` — confirm no regressions (prior 197 + the new eval tests all green).
- [X] T018 Run the `quickstart.md` validation scenarios (1–10) and confirm each observable outcome.
- [X] T019 **Docs reconciliation (standing instruction — update docs at DoD)**: add a built-state banner to `docs/backend/05-eval-drift.md` (built: deterministic floor + verdict math + advisory versioned judge via `runtime.invoke`; deferred: publish gate, DB baseline/`eval_runs`, verdict cache, async worker, Batch-API cost control, κ calibration) and update the `docs/implementation-plan.md` M3 row to the M3-slice built state (advisory judge, gate deferred).

---

## Dependencies & Execution Order

- **Setup (T001–T002)** → no deps; T002 is [P] with T001 only in that both are new files (T002 imports types from T001, so run T001 first).
- **Foundational (T003)** → after T001 (needs the package to import).
- **US1 (T004–T005)** → after Setup. Independent of US2/US3.
- **US2 (T006–T008)** → after Setup. Independent of US1/US3 (verdict takes synthetic quality).
- **US3 (T009–T010)** → after Setup. Independent of US1/US2 (judge is standalone).
- **Integration (T011–T015)** → T012 after T011; T015 after T005, T008, T010, T012 (needs all three layers + breaker) and after T013/T014 RED.
- **Polish (T016–T019)** → after Integration; T017/T018 after all code.

### Within each story
- RED test task is written and **confirmed failing** before its GREEN implementation task (P9).
- Show the RED test + failure evidence to the owner and get confirmation before writing implementation code.

### Parallel opportunities
- T004, T006, T007, T009 (RED tests for US1/US2/US3) are all [P] — different files, no shared state.
- T011 and T013 and T014 (integration RED tests) are [P].
- After Foundational, US1 / US2 / US3 can be built in parallel by different developers.

---

## Parallel Example: the three story RED tests

```bash
# Author all story RED tests together (different files), then confirm each FAILS:
Task T004: tests/test_evals_deterministic.py   (US1)
Task T006: tests/test_evals_score_quality.py   (US2)
Task T007: tests/test_evals_verdict.py         (US2)
Task T009: tests/test_evals_judge.py           (US3)
cd services && .venv/bin/python -m pytest tests/test_evals_deterministic.py tests/test_evals_score_quality.py tests/test_evals_verdict.py tests/test_evals_judge.py -q   # expect RED
```

---

## Implementation Strategy

### MVP first (US1 only)
Setup → Foundational → US1 (T004–T005). **Stop & validate**: the deterministic floor alone flags broken
candidate output with zero model cost — a shippable, honest increment.

### Incremental delivery
US1 (floor) → US2 (verdict) → US3 (advisory judge) → Integration (runner wires them + full aggregate +
seeded-regression) → Polish. Each story is independently testable; the runner is the integration that
realizes SC-001/SC-002/SC-007 and FR-025.

---

## Requirement → task coverage (self-check)

| Requirement | Task(s) |
|---|---|
| FR-001..006 (deterministic checks) | T004, T005 |
| FR-007 (pre-filter skips judge) | T014, T015 |
| FR-008 (verdict ordering) | T007, T008 |
| FR-009 / SC-006 (specific reason) | T007, T008 |
| FR-010 / FR-011 (quality/efficiency math) | T006, T008 |
| FR-012 (per-workspace policy) | T007, T008 |
| FR-013 (frozen baseline input) | T014, T015 |
| FR-014..019 (judge: versioned, via runtime, randomized, mocked) | T009, T010 |
| FR-016 / SC-007 (advisory, non-gating) | T014, T015 |
| FR-020 / SC-008 (purity) | T003 |
| FR-021 / F8 (independent breaker) | T011, T012, T015 |
| FR-022 / F18 / SC-004 (determinism) | T004, T005 |
| FR-023 / P5 (tenancy) | T013, T015 |
| FR-024 (cache disabled) | T014, T015 |
| FR-025 (full EvalRun aggregate) | T014, T015 |
| SC-001 (seeded-regression detection) | T014, T015 |
| SC-002 (hard-fails skip judge) | T014, T015 |
| SC-003 (low_confidence floor) | T007, T008 |
| SC-005 (no live/paid calls) | T002, T009, all |
| Docs at DoD (standing instruction) | T019 |

## Notes
- [P] = different files, no deps. [Story] maps a task to its user story.
- Verify each RED test fails before implementing; never weaken a test to pass (P9).
- No live/paid provider calls — the judge and candidate runtime are mocked throughout (SC-005).
- Commit after each task or logical group (commit only when the owner asks).

---

## Phase 8: Convergence

> Appended by `/speckit-converge` (2026-11-05). The implementation satisfies all FRs/SCs, plan decisions
> (D1–D9), and constitution principles (P1–P9) with no CRITICAL/HIGH/MEDIUM findings and no constitution
> violations. One LOW item remains (edge-case test coverage). Two further LOW items are **noted but not
> appended** because they are dependency-blocked and already recorded in the spec's *Out of Scope* / honest
> gaps: FR-024 cache-off (no response cache exists in the M2a runtime — satisfied by absence; wire a real
> cache-bypass when caching lands in M6) and the "first baseline, no prior version" reason (deferred with the
> DB-backed baseline lifecycle in M4).

- [X] T020 [P] Add committed edge-case tests in services/tests/test_evals_runner.py for the spec Edge Cases that are currently handled-but-untested — an empty/zero-case suite returns `low_confidence` without crashing, and an all-`tie` pairwise run returns a verdict with `pairwise_rate == 0.0` without crashing — per spec Edge Cases (partial)
