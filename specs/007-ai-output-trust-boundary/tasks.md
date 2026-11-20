# Tasks: AI-Output Trust Boundary (M2b)

**Feature**: `specs/007-ai-output-trust-boundary` | **Branch**: `feat/backend-m0-foundation` | **Date**: 2026-11-06

**Inputs**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md) (D1–D9), [data-model.md](./data-model.md), [contracts/runtime-trust-boundary.md](./contracts/runtime-trust-boundary.md), [quickstart.md](./quickstart.md)

**TDD is mandatory** (constitution P9): each story's behavioral RED test is written and shown to fail **before** its implementation. The gateway is mocked for every test — **no live/paid calls** (reuse `tests/_invoke_helpers.py` / `tests/_evals_helpers.make_routed_gateway`). All runtime changes keep `runtime/` import-pure (P6).

**Conventions**: tests run with `cd services && .venv/bin/python -m pytest`. `[P]` = parallelizable (different files, no incomplete-dependency). Story labels `[US1]/[US2]/[US3]` on story-phase tasks only.

---

## Phase 1: Setup

- [x] T001 Establish the green baseline and map the blast radius: run `cd services && .venv/bin/python -m pytest -q` (record pass count), then `grep -rn "output_format\|tools=" services/tests/test_invoke*.py services/tests/_invoke_helpers.py` to list existing invoke tests that use json-output or tool-bound snapshots (these move to buffered-mode expectations in T009 per research D1). No code change.

## Phase 2: Foundational (blocking prerequisites for all stories)

- [x] T002 Add `OUTPUT_INVALID = "output_invalid"` and `TOOL_CALL_REJECTED = "tool_call_rejected"` to the `Outcome` enum in `services/runtime/run.py`, preserving every existing member and its meaning; leave `BOUNDED_EXIT` reserved/unused (data-model.md §Outcome).
- [x] T003 Add `correction_turns: int = 0` to the `RunRecord` frozen dataclass in `services/runtime/run.py` (defaulted so all existing construction stays valid) (data-model.md §RunRecord).
- [x] T004 [P] Add `max_corrections: int = 2` to `RunContext` in `services/runtime/context.py` (dedicated correction budget, caller-overridable; **do not** modify `max_tool_calls`) (data-model.md §RunContext, FR-010).
- [x] T005 [P] RED: write `services/tests/test_trust_boundary_helpers.py` asserting (a) `tool_schemas_from_snapshot(snapshot)` returns `{name: args_schema}` from named tool defs, honoring both `input_schema` and `parameters`, defaulting `{}` when neither, and **skipping** unnamed/bare defs (research D4); (b) `build_correction_messages(messages, turn, defects)` returns a new list appending one assistant echo + one plain user-role message for an output-repair defect and, for a tool-rejection defect, a user message naming the rejection reason and the available tool names (research D3). Run it; confirm it FAILS (helpers absent).
- [x] T006 GREEN: implement `tool_schemas_from_snapshot` and `build_correction_messages` in `services/runtime/io.py` to pass T005; keep the module import-pure.

## Phase 3: User Story 1 (P1) — Structured output validated, repaired once, or fails closed

**Goal**: json-mode output is validated against the frozen `io_contract`; one bounded repair re-ask on a defect; still-invalid → fail closed `output_invalid`. Text-mode unchanged.
**Independent test**: with the gateway mocked for valid / malformed-then-valid / always-malformed JSON, drive `runtime.invoke` on a json-mode snapshot and assert the three outcomes + a text-mode pass-through control.

- [x] T007 [US1] RED: write `services/tests/test_invoke_output_validation.py` (gateway mocked) covering spec AC1–AC5: (a) valid json → `success`, `correction_turns==0`, structured result surfaced; (b) malformed JSON then valid on the repair turn → `success`, `correction_turns==1`, usage/cost **summed over both turns** (SC-005); (c) malformed on both turns → outcome `output_invalid`, **no** structured output surfaced as valid, exactly one Run (SC-001); (d) text-mode snapshot (no schema) → unchanged, no repair, events streamed live (AC4); (e) a repair that would exceed the deadline/budget → fails closed, not unbounded (AC5). Run it; confirm it FAILS against today's pass-through pipeline (which surfaces raw text as `success`) — a genuine behavioral RED (P9).
- [x] T008 [US1] GREEN: refactor `services/runtime/invoke.py` `_pipeline` into **pass-through vs buffered validated mode** (research D1) and add the **bounded correction loop** (D2) wired for OUTPUT validation: in buffered mode buffer each turn's events, call `validate_output(text, output_format=…, output_schema=…)` from `io_contract`; on `RepairNeeded` and budget>0 issue one repair re-ask via `build_correction_messages` (suppressing the intermediate turn, FR-015a/D7), decrement `ctx.max_corrections`, increment `correction_turns`; on success emit the validated `TEXT_DELTA`+`USAGE`(summed)+`STOP`; on budget exhaustion emit `EventType.ERROR{type:"output_invalid"}`+`USAGE` and set outcome `OUTPUT_INVALID`; keep the whole loop inside the existing `asyncio.timeout(deadline)` (FR-011) and accumulate usage/cost/substitutions per turn (D5). All logic stays in the single `_pipeline` so both entry points inherit it (FR-016). Make T007 pass.
- [x] T009 [US1] Update the existing json-output invoke tests identified in T001 to the buffered-mode expectations (single final `TEXT_DELTA`, `correction_turns` present) per research D1; leave text-mode tests unchanged. Re-run `test_invoke*.py` green.

**Checkpoint**: US1 is independently shippable — json-mode agents can no longer hand invalid output to a caller.

## Phase 4: User Story 2 (P1) — Tool calls rejected, fed back once, or fail closed

**Goal**: model tool calls validated against frozen bound-tool schemas; rejected calls never surfaced as executable; one feed-back re-ask; still-invalid → `tool_call_rejected`; valid calls surfaced (not executed).
**Independent test**: with the gateway mocked and a snapshot binding a named tool, assert valid / unbound-name / bad-args paths.
**Depends on**: Phase 3 (US2 extends the buffered loop built in T008).

- [x] T010 [US2] RED: write `services/tests/test_invoke_tool_enforcement.py` (gateway mocked, snapshot binding a **properly-named** tool def e.g. `{"name":"search_runbook","input_schema":{…}}` — not the bare seeder shape, research D4) covering spec AC1–AC5: (a) valid tool call → surfaced unchanged as `TOOL_CALL_END`, tool **not executed**, outcome `success` (FR-009); (b) unbound tool name → one feed-back re-ask naming the available tools, a corrected valid call → proceeds, `correction_turns==1`; (c) schema-violating args on both turns → outcome `tool_call_rejected`, rejected call **never** surfaced (SC-002), one Run; (d) budget/deadline exhaustion → fail closed; (e) a correction run accounts usage over all turns. Run it; confirm it FAILS (no tool validation wired).
- [x] T011 [US2] GREEN: extend the `_pipeline` buffered loop in `services/runtime/invoke.py` to validate each buffered `TOOL_CALL_END` via `validate_tool_call(name, args, tool_schemas_from_snapshot(snapshot))`; a `ToolCallRejected` with budget>0 triggers one feed-back re-ask via `build_correction_messages` (suppressed intermediate turn); exhaustion emits `EventType.ERROR{type:"tool_call_rejected"}`+`USAGE` and sets outcome `TOOL_CALL_REJECTED`; valid calls are emitted unchanged and **not executed**; defect precedence tool>output (D2); corrections share the single `max_corrections` budget with US1. Make T010 pass.

**Checkpoint**: US1+US2 complete — the full trust boundary is enforced with a shared bounded correction loop.

## Phase 5: User Story 3 (P2) — Honest Runs + typed API frames + persistence

**Goal**: new states produce exactly one honest Run and surface at the HTTP endpoint as typed `error`+`done` (never a 500); `correction_turns` persisted.
**Independent test**: via the ASGI client with the gateway mocked, force each new outcome and assert the SSE frame sequence + persisted Run.
**Depends on**: Phases 3–4 (the runtime must produce the new outcomes).

- [x] T012 [US3] RED: write `services/tests/test_api_invoke_trust_boundary.py` (ASGI client, mocked gateway, live compose Postgres) asserting: (a) an unrepairable json agent → SSE stream ends with `event: error`(code `output_invalid`) + `event: done`(status `output_invalid`), **no HTTP 500, no truncated stream** (SC-006); (b) a persistently-invalid tool call → `error`(code `tool_call_rejected`) + `done`; (c) a repaired-success invocation → normal successful terminal; (d) the persisted `runs` row carries the correct outcome and a `correction_turns` value in its stored `usage`. Run it; confirm it FAILS (at least on the `correction_turns` persistence; capture whether the router/sse already handle the frames).
- [x] T013 [US3] GREEN: update `services/api/run_sink.py` to include `correction_turns` in the persisted `usage` JSON (e.g. `usage={**asdict(rec.usage), "correction_turns": rec.correction_turns}`) — **no migration** (research D9). Only if T012 reveals a real gap, make the minimal change to `services/api/routers/invoke.py` / `services/api/sse.py` so the new outcomes surface as typed frames; otherwise assert they already do. Make T012 pass.

## Phase 6: Polish & Cross-Cutting

- [x] T014 [P] Write `services/tests/test_invoke_parity.py` asserting `invoke` (streaming) and `invoke_result` (collecting) produce **identical** outcome + RunRecord (incl. `correction_turns`, summed usage) for equivalent json-mode and tool-mode inputs (FR-016, SC-008).
- [x] T015 [P] Write `services/tests/test_invoke_passthrough_unchanged.py` asserting a text-mode, no-tools snapshot streams live with no buffering, no repair, and never yields the new outcomes — the pass-through path is behavior-identical to M2a (research D1 regression guard).
- [x] T016 Write/extend `services/tests/test_invoke_corrections.py` for the shared budget/accounting edges: an adversarial always-invalid model caps total correction turns at `ctx.max_corrections` (default 2) then fails closed (SC-004); a deadline reached during a correction → `timeout` + one Run; mixed output+tool defects stay bounded by the one shared budget; `cost_is_estimate` stays sticky across turns.
- [x] T017 Run the purity + full regression: `cd services && .venv/bin/python -m pytest tests/test_runtime_purity.py -q` (P6 green) then `.venv/bin/python -m pytest -q` (no regressions vs the T001 baseline); record the pass count.
- [x] T018 Reconcile docs per the standing "update docs after feature" rule: add an M2b built-state banner to `docs/implementation-plan.md` (the Phase 2 / M2 split note — M2a done, **M2b done**) and correct the stale header (lines ~16–62 still claim "SDD artifacts absent / first feature is the mvp experiment"); note the AI-output trust boundary is now enforced and guardrail execution + tool execution remain P2.

---

## Dependencies

```
Setup (T001)
  └─► Foundational (T002, T003 [run.py, sequential]; T004 [P]; T005 RED ─► T006 GREEN)
        └─► US1 (T007 RED ─► T008 GREEN ─► T009 update existing)      ← MVP
              └─► US2 (T010 RED ─► T011 GREEN)   [extends US1's loop]
                    └─► US3 (T012 RED ─► T013 GREEN)
                          └─► Polish (T014 [P], T015 [P], T016, T017, T018)
```

- **Foundational blocks everything** (helpers + types used by every story).
- **US2 depends on US1** — it extends the buffered correction loop built in T008 (not independently parallelizable; honest coupling, both P1).
- **US3 depends on US1+US2** producing the new outcomes.
- Within Foundational: T002 and T003 touch the same file (`run.py`) → sequential; T004 (`context.py`) and T005 (new test file) are `[P]`.

## Parallel opportunities

- Foundational: **T004** and **T005** can run in parallel with **T002/T003**.
- Polish: **T014** and **T015** are independent new test files → parallel.
- US1 and US2 cannot run in parallel (shared `_pipeline` loop).

## Implementation strategy

- **MVP = Phase 1 + 2 + 3 (US1)**: delivers the highest-value half of the trust boundary (no caller receives invalid structured output) and is independently shippable/verifiable.
- **Incremental**: add US2 (tool enforcement) on the established loop, then US3 (API/persistence verification), then polish/regression.
- **Every GREEN** runs its focused test + the affected regressions; **never** weaken or skip a test to pass (P9). Show each RED to the owner before the paired GREEN.

## Task count & format

- **Total: 18 tasks** — Setup 1, Foundational 5, US1 3, US2 2, US3 2, Polish 5.
- Per story: **US1 = 3**, **US2 = 2**, **US3 = 2**.
- All tasks carry a checkbox, sequential ID, optional `[P]`, story label where applicable, and an explicit file path.

## Phase 7: Convergence

- [x] T019 In `services/runtime/invoke.py` buffered validated loop, skip output validation when the turn produced tool calls (guard the `validate_output` block with `not turn.tool_calls`) so a json-mode agent that emits a VALID tool call is surfaced unchanged rather than mis-flagged `invalid_json`/`output_invalid`; add a regression test (json io_contract + bound tool, model returns a valid tool-call-only turn → outcome `success`, tool call surfaced, `correction_turns==0`) per FR-009 / FR-001 (contradicts)
