# Implementation Plan: Eval & Drift Engine (M3 — walking-skeleton slice)

**Branch**: `004-eval-drift-engine` | **Date**: 2026-11-05 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/004-eval-drift-engine/spec.md`

## Summary

Build `services/evals/` — a **pure scoring library** (no web/DB imports, P6) that turns an eval suite + a
candidate version + a frozen baseline into one honest `Verdict`. Three layers: (1) **deterministic checks**
(ported from `mvp/evals/deterministic.py`) — the zero-cost correctness floor and judge pre-filter;
(2) **verdict math** (ported from `mvp/evals/drift.py`) — the load-bearing `low_confidence → hard-assert →
quality → efficiency → pass` ordering under a per-workspace policy, emitting the full frontend `EvalRun`
shape; (3) an **advisory, versioned, fully-mocked LLM judge** (`judge.py`, net-new) that scores quality by
calling the **same `runtime.invoke`** the playground/published endpoint use (via `invoke_result`), never on
a private model path. The judge is advisory-only (it never gates publish — it is uncalibrated, P4). Plus a
small **instance-scoped eval-fan-out circuit breaker** (F8). No live/paid calls anywhere (mirrors M2a
SC-008); first live judge call deferred to Phase 5.

## Technical Context

**Language/Version**: Python 3.12.13 (existing `services/.venv`)

**Primary Dependencies**: reuse `services/runtime/` (`invoke_result`, `RunRecord`, `InvokeResult`, IR). No
new runtime dependencies — `jsonschema` (already present) for structured-output validity; stdlib `json`,
`random` (seedable), `dataclasses`, `statistics`. **No web framework, no DB, no ORM** (P6).

**Storage**: N/A in this slice. The frozen baseline result set and the per-workspace `EvalPolicy` are passed
in as value objects; DB-backed `eval_baselines`/`eval_runs`/`eval_results` persistence is deferred to M4/M5.

**Testing**: pytest + pytest-asyncio (`asyncio_mode=auto`), run via `services/.venv/bin/python -m pytest`.
Everything mocked (a fake `runtime.invoke`/`invoke_result`); **zero live/paid calls** (SC-005). TDD per P9
(genuine behavioral RED before GREEN).

**Target Platform**: Linux server (library consumed later by the API `api` and worker `worker` units).

**Project Type**: Pure Python library (sibling of `services/runtime/`), consumed by future callers.

**Performance Goals**: Not latency-bound in this slice (eval may run inline). The relevant bound is **cost
containment** — the deterministic pre-filter skips the judge on every hard-failing case, and the circuit
breaker caps provider fan-out. No numeric throughput target for the walking-skeleton slice.

**Constraints**: `services/evals/` MUST import no inbound web framework or DB layer (enforced by a purity
test, same as `services/runtime/`). Deterministic layer MUST be reproducible (F18 — no wall-clock/RNG/
unordered-iteration). Judge pairwise order randomization MUST be seedable for test reproducibility.

**Scale/Scope**: A handful of suites, tens of cases each, single corporate pilot tenant. The engine is
task-agnostic (triage `{severity, summary, next_step}` is only the default case contract).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | How this feature complies | Status |
|---|---|---|
| **P1 Verifiable outcome / honesty** | Delivers a real honest verdict (US1 works with zero model cost); mocked behavior is never claimed as live; completion report will name commands + unverified items. | ✅ Pass |
| **P2 Govern scope / phases** | M3 is an owner-approved phase; built under the 2026-11-03 advisory-gate authorization (viability gate 🔴 GATE-OPEN, publish-gate held advisory). Non-goals explicit in spec *Out of Scope*. | ✅ Pass |
| **P3 Deterministic controls** | Deterministic checks + verdict ordering live in ordinary code, fail-closed (hard-fail forces FAIL over any judge opinion); model output treated as untrusted (parsed, validated). | ✅ Pass |
| **P4 Evidence about agent behavior** | Deterministic checks for objective requirements; **judge is versioned** (identity/prompt/rubric/schema + `judge_version`) and **advisory-only — an uncalibrated judge does NOT support any publish/release gate** (the single most load-bearing P4 rule here). Measurement-separation design noted; calibration deferred. | ✅ Pass |
| **P5 Tenant boundaries** | Eval runs are workspace-scoped; comparing cases/baseline/candidate across workspaces is rejected (FR-023), not silently compared. No secrets in eval inputs. | ✅ Pass |
| **P6 Reproducible / same runtime** | `services/evals/` is a pure library (purity test, FR-020); the judge calls the **same `runtime.invoke`** as prod; baseline treated as immutable within a run; candidate generated with caching disabled (FR-024). | ✅ Pass |
| **P7 Provider/cost honesty** | Efficiency delta uses cost from the runtime's `RunRecord` (our profiles), never transport; cost labeled estimate when the run says so. | ✅ Pass |
| **P8 Observable / honest failure** | Every verdict carries a specific, case-grounded reason; a degraded/failed candidate run is never scored as success. | ✅ Pass |
| **P9 Specify/test-first/evidence** | Full SDD chain (specify→clarify→plan→tasks→analyze→implement→converge); strict Red→Green→Refactor; RED shown to owner before implementation code. | ✅ Pass |

**Minimum Verification Checks applied** (constitution table):
- *Model or prompt behavior*: deterministic checks **plus** versioned evaluation vs a frozen baseline;
  judge calibration + measurement separation noted — **but the judge affects no gate here (advisory)**, so
  the calibration record is a documented deferral, not a skipped gate requirement.
- *Provider/runtime behavior*: candidate + judge both run through `runtime.invoke` (adapter/stream/cost
  behavior already covered by M1/M2); efficiency cost independently sourced from the `RunRecord`.
- *Security/secrets*: no secrets in eval inputs; `api_key` stays on the `RunContext` and is already scrubbed
  by the runtime; purity test proves no DB/web leakage.
- *Database or tenant behavior*: no DB in this slice; tenancy enforced as a value-level workspace check with
  a **negative cross-workspace test** (mirrors the constitution's negative cross-tenant requirement at the
  library boundary).

**Result: PASS — no violations.** Complexity Tracking is empty.

## Project Structure

### Documentation (this feature)

```text
specs/004-eval-drift-engine/
├── plan.md              # This file
├── research.md          # Phase 0 — decisions (D1–D9)
├── data-model.md        # Phase 1 — entities
├── quickstart.md        # Phase 1 — how to validate
├── contracts/
│   └── evals-library.md # Phase 1 — the library's public interface contract
├── checklists/
│   └── requirements.md  # spec-quality checklist (from /speckit-specify)
└── tasks.md             # Phase 2 — created by /speckit-tasks (NOT here)
```

### Source Code (repository root)

```text
services/
├── evals/                         # NEW — pure scoring library (sibling of runtime/)
│   ├── __init__.py
│   ├── types.py                   # EvalCase, DetResult, JudgeVerdict, EvalPolicy, EfficiencyDelta,
│   │                              #   Verdict, EvalRunResult (full EvalRun shape), CaseScore
│   ├── deterministic.py           # PORT of mvp/evals/deterministic.py — Layer 1 checks
│   ├── drift.py                   # PORT of mvp/evals/drift.py — score_quality / efficiency_delta / verdict
│   ├── judge.py                   # NEW — advisory judge via runtime.invoke (invoke_result), versioned
│   ├── runner.py                  # NEW — orchestrates: det pre-filter → judge (via breaker) → aggregate
│   └── breaker.py                 # NEW — instance-scoped eval-fan-out circuit breaker (F8)
└── tests/
    ├── test_evals_deterministic.py   # ported + extended (US1, F18 determinism)
    ├── test_evals_verdict.py         # ported + extended (US2 ordering, policy)
    ├── test_evals_score_quality.py   # ported (quality/efficiency math)
    ├── test_evals_judge.py           # NEW (US3 — mocked runtime.invoke, advisory, versioned)
    ├── test_evals_runner.py          # NEW (pre-filter honored, full EvalRun aggregate, seeded-regression SC-001)
    ├── test_evals_breaker.py         # NEW (F8 — independent instances, trips on consecutive errors)
    ├── test_evals_tenancy.py         # NEW (P5 — cross-workspace rejected)
    └── test_evals_purity.py          # NEW (P6 — services/evals imports no web/DB) — mirrors test_runtime_purity
```

**Structure Decision**: `services/evals/` is a new pure library sitting beside `services/runtime/`, matching
the designed `docs/backend/05-eval-drift.md` module layout (`services/evals/{deterministic,drift,judge}.py`).
The `runner.py` + `breaker.py` are net-new orchestration this slice needs to wire the three layers together
without a worker. No changes to `services/runtime/` are required — the judge consumes `runtime.invoke` as an
existing public contract.

## Complexity Tracking

*No constitutional violations — section intentionally empty.*
