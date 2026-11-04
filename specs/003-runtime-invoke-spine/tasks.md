---

description: "Task list: Runtime Invoke Spine (M2a)"
---

# Tasks: Runtime Invoke Spine (M2a)

**Input**: Design documents from `specs/003-runtime-invoke-spine/`

**Prerequisites**: plan.md ✓, spec.md ✓, research.md ✓ (D1–D7), data-model.md ✓, contracts/runtime-invoke.md ✓, quickstart.md ✓

**Tests**: REQUIRED. The constitution's **P9** mandates a genuine *behavioral* RED (not an import/syntax failure) before each behavior-changing task. Pure value objects (Phase 2) are structural and exempt from the RED gate; behavior lives in `tokens.py`, `io.py`, and `invoke.py`.

**Organization**: by user story (US1–US5). Fully mocked — **no live/paid provider calls anywhere** (SC-008).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US5 (user-story phases only)
- Paths are repo-relative; run pytest from `services/` via `.venv/bin/python -m pytest`.

## Conventions for this feature

- New runtime modules live at the **runtime root**: `services/runtime/{context,run,tokens,io,invoke}.py` (beside `snapshot.py`/`hashing.py`), consuming the M1 layer under `services/runtime/providers/` unchanged (plan: Structure Decision).
- Orchestration is a **plain async pipeline**, not LangGraph (research **D1**).
- The Run is delivered via a **sink on the context**, never persisted by the runtime (research **D2/D3**; P6).
- Behavioral REDs monkeypatch the gateway/transport seam — see the Phase-1 harness.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: the mocked test scaffolding every story test reuses (no production behavior).

- [X] T001 [P] Create the invoke test harness in `services/tests/_invoke_helpers.py`: a canned `VersionSnapshot` factory (minimal valid model_config/io_contract/profile_snapshot incl. `limits.context_tokens`), a mock gateway stream builder yielding a scripted sequence of `NormalizedStreamEvent`s (content / reasoning / tool-call / USAGE / STOP / ERROR / degraded), and an in-memory `RunSink` that captures the emitted `RunRecord`. No network, no api_key use (SC-008).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: pure value objects/contracts every story depends on. **Structural — no RED gate** (an import-only failure is not a valid P9 RED).

**⚠️ CRITICAL**: no user-story work begins until this phase is complete.

- [X] T002 [P] Implement `RunContext` dataclass + `RunSink` protocol in `services/runtime/context.py` with defaults `deadline_s: float = 30.0`, `max_tool_calls: int = 8`, optional `correlation_id`, plus `workspace_id`, `source`, `caller`, `api_key`, `record_run` (per data-model.md → RunContext). `max_tool_calls` is a **carried-but-not-enforced** field (Clarifications 2026-11-04 / FR-010).
- [X] T003 [P] Implement `Outcome` enum + `RunRecord` + `InvokeResult` frozen value objects in `services/runtime/run.py`. `Outcome` members: `success`, `invalid_input`, `context_overflow`, `config_error`, `provider_error`, `timeout`, `degraded`, and `bounded_exit` (**reserved** — defined for the later tool-exec loop, not produced in M2a). `RunRecord`/`InvokeResult` fields per data-model.md.

**Checkpoint**: value types ready — story implementation can begin.

---

## Phase 3: User Story 1 - One shared execution path runs a frozen snapshot (Priority: P1) 🎯 MVP

**Goal**: a single pure `invoke(snapshot, inputs, ctx)` that reads the frozen snapshot, runs it through the M1 gateway, re-surfaces the event stream, and returns a normalized result — identical path for all callers.

**Independent Test**: call `invoke`/`invoke_result` with a canned snapshot + valid inputs + a mocked gateway; confirm content events are re-surfaced and the result is assembled from the snapshot; mutating an unrelated "live" structure after the snapshot changes nothing.

### Tests for User Story 1 ⚠️ (write first, must FAIL behaviorally)

- [X] T004 [US1] RED behavioral tests in `services/tests/test_invoke.py`: (a) `invoke()` re-surfaces the mocked gateway's content events and `invoke_result()` returns the accumulated text (US1/AC1); (b) the run is driven only by the passed `VersionSnapshot` — mutating a sibling structure after capture leaves behavior unchanged (US1/AC2, FR-002); (c) the single path has no caller-specific branch — the same `source` variations yield the identical code path/result (US1/AC3, FR-003). Must fail against the not-yet-built pipeline for a *behavioral* reason (missing surfaced events / result), not an import error.

### Implementation for User Story 1

- [X] T005 [US1] Implement `build_normalized_request(snapshot, inputs) -> NormalizedRequest` in `services/runtime/io.py` using the data-model mapping (system_prompt→system, model_config→provider/model/api_version/params/reasoning, tools→`ToolRef.schema_snapshot`, io_contract.output_format=="json"→response_format, inputs→messages). (FR-001 assembly)
- [X] T006 [US1] Implement the pipeline in `services/runtime/invoke.py`: `async def invoke(snapshot, inputs, ctx) -> AsyncIterator[NormalizedStreamEvent]` (resolve profile from snapshot → `gateway.stream(req, api_key=ctx.api_key, profile=…)` → re-yield each event, accumulate text) and `async def invoke_result(...) -> InvokeResult`. Execute **only** via the M1 gateway (FR-006); credential from `ctx.api_key` (FR-019). Depends on T002, T003, T005. (US1/AC1-3, FR-001/002/003/006)

**Checkpoint**: happy-path single invocation works through the mocked gateway.

---

## Phase 4: User Story 2 - One honest, observable Run record per invocation (Priority: P1)

**Goal**: every invocation produces exactly one `RunRecord` with full identity/usage/cost/latency/outcome/correlation, honest on failure/degradation.

**Independent Test**: run success / provider-error / degraded paths; confirm each yields exactly one Run with the correct distinguishing outcome and populated fields; cost equals the gateway's.

### Tests for User Story 2 ⚠️

- [X] T007 [US2] RED behavioral tests in `services/tests/test_invoke_run_record.py`: exactly one `RunRecord` captured by the sink per invocation across `success`, `provider_error`, and `degraded` (no-usage) streams (FR-011, SC-001); each Run carries model/profile/version/prompt/snapshot identity, usage, latency, `source`/`caller`, workspace, and a non-empty correlation id — minted when the ctx omits it (FR-012, SC-007); `RunRecord.cost_usd` equals the gateway's computed cost and is not recomputed (FR-014, SC-006); `substitutions` carried through unchanged (FR-017); a failed/degraded run is never `success` and never dropped (FR-013).

### Implementation for User Story 2

- [X] T008 [US2] In `services/runtime/invoke.py`, build and populate the `RunRecord` from the snapshot identity + the gateway's terminal `USAGE` (usage, `cost_usd`, `cost_is_estimate`, `substitutions` carried verbatim — never recomputed), measured `latency_ms`, `workspace_id` from ctx/snapshot (FR-015), `source`/`caller`, and a correlation id (mint if absent); deliver it via `ctx.record_run(run)` **exactly once** for every invocation. Depends on T006. (FR-011/012/014/015/017, SC-001/006/007)
- [X] T009 [US2] In `services/runtime/invoke.py`, map the gateway's degraded `ERROR` (no USAGE) → `Outcome.degraded` and a normalized provider `ERROR` → `Outcome.provider_error` (carry the api_key-scrubbed error detail); guarantee a non-success outcome is never recorded as success and the Run is never dropped. Depends on T008. (FR-013, P8)

**Checkpoint**: every outcome yields one truthful Run.

---

## Phase 5: User Story 3 - Work is bounded by an explicit deadline (Priority: P1)

**Goal**: enforce `ctx.deadline_s`; carry (not enforce) `max_tool_calls`.

**Independent Test**: a deadline shorter than a slow mocked stream stops the invocation ~deadline with a timeout outcome + partial Run; `max_tool_calls` is carried onto the Run without changing single-turn behavior.

### Tests for User Story 3 ⚠️

- [X] T010 [US3] RED behavioral tests in `services/tests/test_invoke_deadline.py`: (a) a mocked stream slower than `ctx.deadline_s` → invocation stops at/within a small margin of the deadline with `Outcome.timeout` and a Run recording partial usage/cost/latency naming the deadline (US3/AC1-2, FR-009, SC-003); (b) `deadline_s` already elapsed at call start → immediate `timeout`, **no** gateway call (edge case); (c) `max_tool_calls` is carried onto the `RunRecord` and does not alter single-turn behavior (US3/AC3, FR-010).

### Implementation for User Story 3

- [X] T011 [US3] In `services/runtime/invoke.py`, wrap stream consumption in an async timeout bound by `ctx.deadline_s`; on expiry stop consuming, emit best-effort partial, and record `Outcome.timeout` with partial usage/cost and the deadline as the bound hit; carry `ctx.max_tool_calls` onto the Run (no enforcement). Depends on T008. (FR-009/010, SC-003)
- [X] T012 [US3] Add a transport-level timeout pass-through (research D5): thread an optional `timeout` from the invoke deadline through `services/runtime/providers/gateway.py` → `services/runtime/providers/transport.py` into the litellm call, defaulting to `None` so M1's mocked tests and existing behavior are unaffected (closes review gap C5 at the seam). Keep `test_transport.py` + `test_gateway.py` green.

**Checkpoint**: 🎯 **MVP complete** — all P1 stories (US1–US3) functional.

---

## Phase 6: User Story 4 - The full provider stream is surfaced to the caller (Priority: P2)

**Goal**: re-surface content/reasoning/tool-call events and the terminal stop reason; a tool-call-only response is not empty/degraded (closes review gap C4).

**Independent Test**: a mocked multi-event stream exposes every event class to the caller and records `stop_reason`; a tool-call-only stream yields a non-empty `success`.

### Tests for User Story 4 ⚠️

- [X] T013 [US4] RED behavioral tests in `services/tests/test_invoke_events.py`: (a) a mocked stream emitting content + `REASONING_DELTA` + `TOOL_CALL_START/DELTA/END` + `STOP` surfaces every event class to the caller, none dropped/flattened (US4/AC1, FR-007, SC-004); (b) a tool-call-only stream (no content) → `InvokeResult` surfaces the tool-call and `Outcome.success` (non-empty), not empty/degraded (US4/AC2, SC-004, C4); (c) the terminal `stop_reason` is captured on the result and the Run (US4/AC3, FR-008).

### Implementation for User Story 4

- [X] T014 [US4] In `services/runtime/invoke.py`, accumulate and expose `reasoning`, `tool_calls`, and `stop_reason` on `InvokeResult`/`RunRecord` (re-yielding the underlying events unchanged); treat a clean stop with only tool-call output as `success` (non-empty), distinct from `degraded`. Depends on T008. (FR-007/008, C4)

**Checkpoint**: full stream fidelity; tool-using/reasoning models no longer read as empty.

---

## Phase 7: User Story 5 - Oversized/invalid inputs caught before any provider call (Priority: P2)

**Goal**: validate inputs vs the input contract and check the context budget *before* the gateway call; fail closed with zero provider cost.

**Independent Test**: invalid inputs → `invalid_input`, zero gateway interaction; over-budget inputs → `context_overflow`, zero gateway interaction; both produce a zero-cost Run.

### Tests for User Story 5 ⚠️

- [X] T015 [P] [US5] RED behavioral test in `services/tests/test_tokens.py`: `count_tokens` is monotonic non-decreasing in input size and **over-estimates** (never under-counts) versus a simple reference, so a real overflow can never slip through (research D4).
- [X] T016 [P] [US5] RED behavioral tests in `services/tests/test_invoke_input_guard.py`: inputs violating `io_contract.input_schema` → `Outcome.invalid_input` with the gateway **never called** (assert zero interaction) (US5/AC1, FR-004); inputs whose `count_tokens` exceeds `profile.limits.context_tokens` → `Outcome.context_overflow`, gateway **never called** (US5/AC2, FR-005); each pre-call rejection yields a Run with zero provider cost (US5/AC3, SC-005).

### Implementation for User Story 5

- [X] T017 [P] [US5] Implement `count_tokens(model, messages, tools=None, system=None) -> int` in `services/runtime/tokens.py` as a conservative, deterministic, **over-estimating** heuristic behind a small abstraction (exact per-provider tokenizers deferred — research D4). (FR-005)
- [X] T018 [US5] Implement `validate_input(inputs, io_contract)` in `services/runtime/io.py`, reusing `runtime.providers.validation._schema_errors` (Draft 2020-12) against `io_contract.input_schema`. Same file as T005 → sequential. (FR-004)
- [X] T019 [US5] In `services/runtime/invoke.py`, run the input-validation then context-budget guards at the **front** of the pipeline, before any gateway call; on failure record `invalid_input` / `context_overflow` with a zero-cost Run and no provider interaction. Also map snapshot→unknown/retired model (gateway raises) to `Outcome.config_error`. Depends on T017, T018, T008. (FR-004/005, SC-005; edge: config_error)

**Checkpoint**: all five stories independently functional.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [X] T020 [P] Confirm P6 purity: run `services/tests/test_runtime_purity.py` and extend it (or add an assertion) so the new `runtime/{context,run,tokens,io,invoke}.py` modules import no inbound web framework (fastapi/starlette/uvicorn). (FR-016, SC-002)
- [X] T021 Run the full unit batch `cd services && .venv/bin/python -m pytest -q -m "not integration"` and confirm no M0/M1 regression (all prior suites still green). (SC-008 — no live calls)
- [X] T022 Execute the `quickstart.md` scenarios 1–12 as the acceptance pass; confirm each maps to a green test.
- [X] T023 [P] Update `docs/implementation-plan.md` (M2 row → M2a built-state) and `docs/backend/03-runtime.md` (built-state note: plain async pipeline adopted for M2a, LangGraph deferred to the first branching feature — research D1), per the standing "update docs at DoD" rule.

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (P1)** → no deps.
- **Foundational (P2)** → after Setup; **blocks all stories**.
- **US1 (P3)** → after Foundational. The pipeline other stories extend.
- **US2 / US3 / US4 / US5 (P4–P7)** → all depend on **US1** (they extend `invoke.py` / the Run). US2 before US3/US4 (they build on the populated Run from T008). US5's `tokens.py` (T017) is independent and can be built any time after Setup.

### Within each story

- RED test(s) first and failing *behaviorally* → implementation → green (P9).
- `invoke.py` tasks touching the same file (T006 → T008 → T009 → T011 → T014 → T019) are **sequential**.
- `io.py` tasks (T005 then T018) are **sequential** (same file).

### Parallel opportunities

- **T002 ∥ T003** (context.py vs run.py).
- **T015 ∥ T016 ∥ T017** (tokens test vs guard test vs tokens impl — different files; impl T017 before guard-wire T019).
- **T023** (docs) ∥ final test runs.
- Story test-authoring for US2/US3/US4 can be drafted in parallel, but their impl serializes on `invoke.py`.

---

## Implementation Strategy

### MVP first (P1 = US1 → US2 → US3)

1. Phase 1 Setup → Phase 2 Foundational.
2. US1 (shared invoke) → **validate** happy path.
3. US2 (honest Run) → US3 (deadline). **STOP & VALIDATE** — the MVP spine: one pure invoke, one honest Run per outcome, bounded by a deadline, fully mocked.

### Incremental delivery

4. US4 (full stream fidelity — unblocks tool/reasoning visibility, C4).
5. US5 (pre-call input + budget guards — fail-closed before spend, F4).
6. Polish (purity, regression, quickstart, docs).

### Notes

- `[P]` = different files, no incomplete-dependency.
- No live/paid provider calls anywhere (SC-008) — the Phase-1 harness mocks the gateway/transport seam.
- Output is returned as-is; **no** `validate_output`/`validate_tool_call` wiring here (that is M2b).
- `max_tool_calls` is carried, not enforced (Clarifications 2026-11-04 / FR-010).
- Commit after each story checkpoint; keep `test_runtime_purity.py` green throughout.

---

## Phase 9: Convergence

*Appended by `/speckit-converge` (2026-11-04) — one `partial` gap found against the feature artifacts. Complete via `/speckit-implement` (TDD, genuine behavioral RED first per P9).*

- [X] T024 Fail closed on an empty/missing `RunContext.workspace_id`: reject at the front of the pipeline (before any gateway call) with an explicit fail-closed outcome + a Run, instead of silently emitting an untenanted Run via `ctx.workspace_id or snapshot.workspace_id`, in `services/runtime/invoke.py` — per data-model.md `RunContext.workspace_id` ("MUST be non-empty — fail closed") + Constitution P5 (partial)
