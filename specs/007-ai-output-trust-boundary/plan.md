# Implementation Plan: AI-Output Trust Boundary (M2b)

**Branch**: `feat/backend-m0-foundation` (shared working branch; no per-feature branch hook) | **Date**: 2026-11-06 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/007-ai-output-trust-boundary/spec.md`

## Summary

Close the untrusted-model-output boundary on the single shared `runtime.invoke` pipeline (constitution P3). The deterministic validators already exist and are unit-tested (`validate_output` F1/F2, `validate_tool_call` F3 in `runtime/providers/validation.py`) but are **not called by the pipeline**. This feature **wires them in** and adds a **bounded self-correction loop**: on a detected defect the runtime gives the model exactly one bounded re-ask (output repair / tool-call feed-back), bounded by a dedicated correction budget (default 2 turns), all within the existing invocation deadline; unresolved defects **fail closed** with new honest outcomes (`output_invalid`, `tool_call_rejected`). Guardrail execution and real tool execution stay out of scope (P2).

**Technical approach**: refactor `runtime/invoke.py::_pipeline` into two modes — **pass-through** (plain text output, no bound tools → stream live, unchanged from today) and **buffered validated mode** (json output *or* bound tools → buffer each model turn, validate, correct within budget, then emit only the final validated events). Intermediate correction turns are suppressed from the client stream (FR-015a) but fully recorded (usage/cost summed, `correction_turns` counted). New `Outcome` values + a `correction_turns` field on `RunRecord`; a `max_corrections` field on `RunContext`. The API SSE layer is **unchanged** — its existing error-event handling + post-stream fallback + `done`-status already surface the new outcomes as typed frames. `correction_turns` persists into the existing `runs.usage` JSONB (no migration).

## Technical Context

**Language/Version**: Python 3.12.13

**Primary Dependencies**: existing only — `jsonschema` (already used by `validation.py`), stdlib `asyncio`/`dataclasses`/`json`. **No new dependencies.**

**Storage**: Postgres `runs` table via the existing `api/run_sink.py`. **No migration** — `correction_turns` is folded into the existing `usage` JSONB column.

**Testing**: `pytest` + `pytest-asyncio` (`asyncio_mode=auto`); gateway fully mocked at `runtime.providers.gateway.stream` (reuse `tests/_invoke_helpers.py` / `_evals_helpers.make_routed_gateway`). **No live/paid provider calls.**

**Target Platform**: Linux server (pure runtime library consumed by the FastAPI API + the eval engine).

**Project Type**: Backend service — a pure `runtime/` library plus a thin API caller + run sink.

**Performance Goals**: correction loop bounded to a fixed default of 2 extra model turns; the whole invocation (original + corrections) stays within `ctx.deadline_s` (default 30s).

**Constraints**: `runtime/` stays import-pure (no FastAPI/Starlette/DB imports — P6; purity tests stay green); exactly one honest `RunRecord` per invocation (P8); cost summed from our own profiles across all turns, never trusted from transport (P7); the M4a SSE frame contract is preserved (no new frame types; new outcomes reuse the `error` frame + `done` status).

**Scale/Scope**: internal pilot; single-turn-plus-bounded-correction (no general agentic loop; tool execution is P2).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Gate | Status |
|---|---|---|
| **P1 Honesty** | No mock-as-implemented; deferrals documented | ✅ Guardrail execution + tool execution explicitly deferred (P2), stated in spec + here; no behavior claimed that isn't wired. |
| **P2 Scope/phases** | Fits the phased plan | ✅ This is M2b (the deferred half of M2 from feature 003); advances the walking skeleton's trust boundary without pulling P2 work forward. |
| **P3 Deterministic fail-closed controls** | Model output treated as untrusted; enforced in code, fail-closed | ✅ **This is the feature.** Output + tool calls validated deterministically; unresolved → fail closed with typed outcome. Model instructions are never the enforcement. |
| **P4 Judge/evidence** | n/a to this feature | ✅ No judge/gate changes. |
| **P5 Tenancy/data** | No tenancy regressions; no raw content leak | ✅ No new persistence of raw content; `correction_turns`/usage are metrics only. Run still carries `workspace_id`. |
| **P6 Pure runtime** | `runtime/` has no web/DB imports | ✅ All new logic in `runtime/`; purity test stays green. The sink change is in `api/`. |
| **P7 Cost honesty** | Cost from our profiles, every turn counted | ✅ Usage/cost summed across original + correction turns from our profiles; substitutions carried; never trust transport. |
| **P8 Observable/honest failure** | Exactly one Run per invocation incl. failures; degradation explicit | ✅ One Run always; new outcomes + `correction_turns` make corrections observable; fail-closed states are explicit, not silent. |
| **P9 Genuine RED before GREEN** | Behavioral RED shown to owner before impl | ✅ TDD plan below writes behavioral RED per story first (not import/syntax failures). |

**No violations.** Complexity Tracking table omitted (nothing to justify).

## Project Structure

### Documentation (this feature)

```text
specs/007-ai-output-trust-boundary/
├── spec.md              # /speckit-specify + /speckit-clarify output
├── plan.md              # this file
├── research.md          # Phase 0 — design decisions D1–D9
├── data-model.md        # Phase 1 — Outcome/RunRecord/RunContext deltas + tool_schemas + accumulation
├── quickstart.md        # Phase 1 — how to run/verify the feature
├── contracts/
│   └── runtime-trust-boundary.md   # the invoke behavioral contract + SSE-frame mapping
├── checklists/
│   └── requirements.md  # spec-quality checklist (16/16)
└── tasks.md             # /speckit-tasks output (NOT created here)
```

### Source Code (repository root)

```text
services/
├── runtime/
│   ├── invoke.py        # CORE CHANGE: _pipeline → pass-through vs buffered-validated mode + bounded correction loop
│   ├── run.py           # Outcome += OUTPUT_INVALID, TOOL_CALL_REJECTED ; RunRecord += correction_turns:int=0
│   ├── context.py       # RunContext += max_corrections:int=2 (dedicated budget; max_tool_calls untouched)
│   ├── io.py            # + helpers: extract tool_schemas from snapshot.tools; build correction messages
│   └── providers/
│       └── validation.py   # REUSED UNCHANGED (validate_output / validate_tool_call)
├── api/
│   ├── run_sink.py      # include correction_turns in the persisted usage/metrics (no migration)
│   └── routers/invoke.py   # NO CHANGE expected (verify typed frames via test)
└── tests/
    ├── test_invoke_output_validation.py   # US1 (new)
    ├── test_invoke_tool_enforcement.py    # US2 (new)
    ├── test_invoke_corrections.py         # US1+US2 budget/deadline/accounting (new)
    ├── test_invoke_passthrough_unchanged.py # regression: text-no-tools unchanged (new)
    ├── test_api_invoke_trust_boundary.py  # US3 typed frames at HTTP boundary (new)
    └── test_runtime_purity.py             # existing — must stay green
```

**Structure Decision**: Pure edits to the existing `services/runtime/` library plus a one-line enrichment in `api/run_sink.py`. No new modules, no new top-level directories, no new dependencies, no migration. The single `_pipeline` generator remains the one place the logic lives, so `invoke` (streaming) and `invoke_result` (collecting, used by eval) get identical behavior for free (FR-016).

## Phased delivery (maps to spec user stories; each a TDD RED→GREEN increment)

- **Foundational** — `Outcome` additions, `RunRecord.correction_turns`, `RunContext.max_corrections`, and the `io.py` helpers (`tool_schemas_from_snapshot`, `build_correction_messages`). Pure data/helpers; unit-tested first.
- **US1 (P1)** — buffered validated mode for **output**: validate json-mode output, one repair re-ask, fail-closed `output_invalid`; text-mode unchanged. RED→GREEN.
- **US2 (P1)** — tool-call enforcement + feed-back: validate against `tool_schemas`, reject-never-surface, one feed-back re-ask, fail-closed `tool_call_rejected`; valid calls surfaced, not executed. RED→GREEN.
- **US3 (P2)** — honest Runs + typed frames: verify exactly-one Run with summed usage + `correction_turns`; verify the HTTP endpoint surfaces new outcomes as typed `error`+`done` (no 500); persist `correction_turns`. RED→GREEN.
- **Polish** — parity test (`invoke` vs `invoke_result`), pass-through regression, purity test green, full-suite regression.

## Complexity Tracking

*No constitution violations — section intentionally empty.*
