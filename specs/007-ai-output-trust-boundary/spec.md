# Feature Specification: AI-Output Trust Boundary (M2b)

**Feature Branch**: `feat/backend-m0-foundation` (shared working branch; no per-feature branch hook configured)

**Created**: 2026-11-06

**Status**: Draft

**Input**: User description: "M2b — the AI-output trust boundary for the runtime invoke spine (constitution P3: deterministic, fail-closed controls on untrusted model output). Wire the already-built, already-unit-tested output/tool-call validators into the single shared `runtime.invoke` pipeline, add a bounded self-correction loop (one repair re-ask for malformed/schema-violating output; one feed-back re-ask for rejected tool calls), keep real tool execution and the guardrail library out of scope (P2), and surface every new state as an honest Run record and a typed API frame — never a raw 500."

## Context & Problem Statement

The runtime spine (`runtime.invoke`, feature 003/M2a) is the single execution path shared by playground, published-invoke, and every eval case. Today it **trusts whatever the model returns**:

- A json-mode agent's final message is surfaced to the caller **raw** — malformed JSON or JSON that violates the agent's declared output schema flows straight through. Callers that depend on a structured contract (eval scoring, downstream automation, the console) can receive garbage.
- Model-emitted tool calls are appended to the result and surfaced to the caller **unvalidated** — a hallucinated/unbound tool name or schema-violating arguments is presented as a legitimate, executable call.

The deterministic enforcement logic for both already exists and is unit-tested — `validate_output` (F1/F2) and `validate_tool_call` (F3) in `services/runtime/providers/validation.py` — but **nothing in the pipeline calls them**. This is a direct gap against constitution Principle 3 (deterministic, fail-closed controls on untrusted model output; model instructions are not enforcement) and Principle 8 (honest, observable failure).

This feature closes that boundary: it wires the validators into the pipeline with fail-closed behavior, adds a **bounded self-correction loop** that gives the model exactly one chance to fix a detected defect, records every outcome honestly, and surfaces the new states through the API as typed frames.

**Explicitly out of scope (deferred, by owner decision 2026-11-06):**
- The **guardrail execution engine / guardrail library** — `guardrail_config` stays data-only this feature; the input/output guardrail step and its detectors are deferred to a dedicated P2 guardrail feature.
- **Real tool execution / sandbox** — the feed-back self-correction loop re-asks the model only when a tool call is *rejected*; a **valid** tool call is still surfaced to the caller, not executed. Tool execution remains P2.

## Clarifications

### Session 2026-11-06

- Q: Should the bounded correction loop draw from `ctx.max_tool_calls` or a dedicated budget, and what is the cap? → A: A **dedicated correction budget**, independent of `max_tool_calls` (which is reserved for the deferred P2 tool-execution loop). It is a **global per-invocation cap on total correction turns, default 2** (enough for one output repair + one tool-call feed-back), caller-overridable via the invocation context. Every correction turn decrements it; exhaustion fails closed.
- Q: When the correction budget is exhausted (or the retry still fails), what outcome does the Run carry? → A: The **defect-type outcome** that remained unresolved — `output_invalid` or `tool_call_rejected` — so the caller gets a meaningful reason. The reserved `bounded_exit` is **not** used by this feature (it stays reserved for the P2 tool-execution loop).
- Q: How is correction activity made observable on the Run? → A: The Run record exposes a **`correction_turns` integer count** (0 for a clean invocation), in addition to the summed usage/cost across all turns.
- Q: In streaming mode, are the intermediate correction-turn deltas sent to the caller? → A: **No** — intermediate correction-turn content is **suppressed from the client stream** (never surfaced as provisional/invalid output), while still being **fully recorded** in the Run (usage, cost, count). Only the final validated result and a single typed terminal (`done`, or `error` on fail-closed) reach the caller. This protects SC-001/SC-006 (a caller never sees unvalidated or contradictory output on the wire).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Structured output is validated, repaired once, or fails closed (Priority: P1)

A builder publishes an agent configured for JSON output against a declared output schema (the frozen `io_contract` output slot). When that agent runs — in the playground, a published invoke, or an eval case — a caller must **never** receive malformed or schema-violating JSON as if it were valid. If the model's final message is bad JSON or violates the schema, the runtime gives the model exactly **one** bounded repair re-ask (feeding the specific validation errors back); if the repaired attempt is still invalid, the invocation **fails closed** with a typed outcome and an honest Run record.

**Why this priority**: This is the core trust-boundary gap with real, tested enforcement logic already in place. Eval scoring and downstream automation both assume a valid structured contract; without this, the shared runtime can silently hand out garbage, violating P3 and corrupting eval evidence (P4).

**Independent Test**: With the gateway mocked to return (a) valid schema-conformant JSON, (b) malformed JSON then valid JSON on the repair turn, and (c) malformed JSON on both turns, drive `runtime.invoke` on a json-mode snapshot and assert: (a) passes through unchanged with a `success` Run; (b) succeeds after exactly one repair turn, with both turns' usage/cost summed into the Run; (c) ends with the typed `output_invalid` outcome, zero valid structured output surfaced, and exactly one honest Run.

**Acceptance Scenarios**:

1. **Given** a json-mode snapshot with a declared output schema, **When** the model returns schema-conformant JSON, **Then** the structured object is surfaced to the caller and the Run outcome is `success` with no repair turn.
2. **Given** the same snapshot, **When** the model's first message is malformed JSON, **Then** the runtime issues exactly one repair re-ask that includes the parse error, and if the second message is valid JSON the invocation succeeds (outcome `success`) with usage/cost covering **both** model turns.
3. **Given** the same snapshot, **When** both the original and the repaired message fail validation, **Then** the invocation fails closed with outcome `output_invalid`, no structured output is presented as valid, and exactly one Run record is emitted.
4. **Given** a text-mode snapshot (no output schema), **When** the model returns any text, **Then** output validation passes it through unchanged (no repair turn, no behavior change from today).
5. **Given** a json-mode snapshot, **When** a repair turn would exceed the invocation deadline or the correction budget, **Then** the runtime stops and fails closed (outcome `output_invalid` or `timeout`) rather than issuing an unbounded number of re-asks.

---

### User Story 2 - Hallucinated / invalid tool calls are rejected, fed back once, or fail closed (Priority: P1)

When the model emits a tool call, the runtime must validate it against the agent's **frozen bound-tool schemas** (from the snapshot's tool bindings). An unbound/hallucinated tool name, or arguments that violate the tool's schema, must **never** be surfaced to the caller as a legitimate, executable tool call. Instead the runtime feeds the specific rejection reason back to the model for exactly **one** bounded retry; if the model's corrected attempt is still invalid, the invocation fails closed with a typed outcome and an honest Run. A **valid** tool call is surfaced to the caller unchanged (the runtime does not execute tools — execution is P2).

**Why this priority**: A hallucinated tool name or malformed args surfaced as executable is a correctness-and-safety defect (P3). The enforcement logic (`validate_tool_call`) is already built and tested; this wires it in and adds the owner-chosen feed-back self-correction.

**Independent Test**: With the gateway mocked, drive `runtime.invoke` on a snapshot that binds a known tool set and assert: (a) a valid tool call is surfaced unchanged; (b) an unbound tool name triggers exactly one feed-back re-ask listing the available tools, and a corrected valid call then succeeds; (c) bad args on both the original and the retry end with the typed `tool_call_rejected` outcome, no rejected call surfaced as executable, and exactly one honest Run.

**Acceptance Scenarios**:

1. **Given** a snapshot binding tool `search_runbook`, **When** the model calls `search_runbook` with schema-valid args, **Then** the tool call is surfaced to the caller unchanged and (absent other defects) the Run outcome is `success`. The runtime does not execute the tool.
2. **Given** the same snapshot, **When** the model calls an unbound tool `delete_everything`, **Then** the runtime does not surface it as executable and issues exactly one feed-back re-ask that names the rejection reason and the available bound tools.
3. **Given** the same snapshot, **When** the model calls `search_runbook` with schema-violating args, **Then** the runtime feeds the argument errors back for one retry; if the retry is valid the invocation proceeds, otherwise it fails closed with outcome `tool_call_rejected`.
4. **Given** any snapshot, **When** feed-back re-asks would exceed the correction budget or the invocation deadline, **Then** the runtime stops and fails closed rather than looping unbounded.
5. **Given** any invocation that triggered one or more correction turns, **When** it completes, **Then** the Run record accounts for the usage/cost of **every** model turn (original + corrections), and the correction activity is observable in the Run.

---

### User Story 3 - New failure/correction states surface as honest Runs and typed API frames (Priority: P2)

Every new state introduced by US1/US2 — `output_invalid`, `tool_call_rejected`, and any correction activity — must produce exactly **one** honest Run record (P8) and must surface through the HTTP invoke endpoint as a **typed** streaming frame (a normal `error` + `done` terminal, consistent with the M4a SSE frame contract), **never** as a raw 500 or a dropped stream. Callers (console, eval) can distinguish a clean success, a repaired success, and a fail-closed rejection from the frames and the Run alone.

**Why this priority**: The API layer (`services/api/routers/invoke.py`) re-encodes runtime events into typed SSE frames; new runtime outcomes that aren't handled would degrade to untyped 500s, breaking the frontend contract the whole platform depends on. Independently testable at the API boundary once US1/US2 produce the states.

**Independent Test**: With the gateway mocked to force each new outcome, call the HTTP invoke endpoint via the ASGI test client and assert the response is a well-formed SSE stream terminating in a typed `error` frame + `done` (no 500, no truncated stream), and that a corresponding Run row is persisted with the correct outcome.

**Acceptance Scenarios**:

1. **Given** a json-mode agent whose output can't be repaired, **When** invoked over HTTP, **Then** the SSE stream ends with a typed `error` frame carrying the `output_invalid` reason followed by `done`, and a Run row with outcome `output_invalid` is persisted.
2. **Given** an agent whose tool call stays invalid after feed-back, **When** invoked over HTTP, **Then** the stream ends with a typed `error` frame carrying `tool_call_rejected` + `done`, and the corresponding Run is persisted.
3. **Given** a repaired-success invocation, **When** invoked over HTTP, **Then** the caller receives a normal successful terminal and the Run reflects the correction turns' usage/cost.

---

### Edge Cases

- **Repair turn itself errors** (provider failure/timeout on the second call): the invocation fails closed with the appropriate existing outcome (`provider_error` / `timeout`), not `success`; still exactly one Run.
- **Deadline mid-correction**: the shared `ctx.deadline_s` bounds the *whole* invocation including correction turns; a deadline hit during a correction yields `timeout` with best-effort partial state.
- **Correction budget exhaustion**: a dedicated per-invocation cap (default 2) on total correction turns guarantees termination even if the model keeps producing defects; hitting it fails closed with the defect-type outcome (never an unbounded loop or runaway spend).
- **Mixed defects**: output needing repair *and* an invalid tool call in the same response — both are covered by the single shared correction budget so the total number of extra model turns stays bounded.
- **Empty / content-free success** (e.g., a valid tool-call-only response in text mode): unchanged from today — not treated as a defect.
- **json-mode with no declared schema**: parse-only validation (must be valid JSON, but any shape) — matches the existing `validate_output` contract.
- **Streaming vs collected callers**: both `invoke` (streaming) and `invoke_result` (collecting, used by eval) must apply identical enforcement and produce identical outcomes/Runs.

## Requirements *(mandatory)*

### Functional Requirements

**Output validation & repair (US1)**
- **FR-001**: The runtime MUST validate a json-mode invocation's final model message against the frozen `io_contract` output slot (parse + schema) before surfacing any structured result to the caller, using the existing `validate_output` contract.
- **FR-002**: On a detected output defect (`invalid_json` or `schema_violation`), the runtime MUST issue exactly one bounded repair re-ask that includes the specific validation error(s), then re-validate the repaired message.
- **FR-003**: If the repaired message is still invalid (or no repair is possible within budget/deadline), the runtime MUST fail closed with a typed outcome (`output_invalid`) and MUST NOT surface invalid output as valid structured data.
- **FR-004**: Text-mode invocations (no JSON output format) MUST pass through unchanged — no validation failure, no repair turn, no behavioral change from the current pipeline.

**Tool-call enforcement & feed-back (US2)**
- **FR-005**: The runtime MUST validate every model-emitted tool call against the snapshot's frozen bound-tool schemas using the existing `validate_tool_call` contract, before surfacing it to the caller.
- **FR-006**: A rejected tool call (`unknown_tool` or `bad_args`) MUST NOT be surfaced to the caller as a legitimate/executable tool call.
- **FR-007**: On a rejected tool call, the runtime MUST feed the rejection reason (and, for an unbound name, the available bound tool names) back to the model for exactly one bounded retry, then re-validate.
- **FR-008**: If the retried tool call is still invalid (or budget/deadline is exhausted), the runtime MUST fail closed with a typed outcome (`tool_call_rejected`).
- **FR-009**: A **valid** tool call MUST be surfaced to the caller unchanged; the runtime MUST NOT execute tools (execution remains P2).

**Bounded correction loop (US1+US2, shared)**
- **FR-010**: The runtime MUST enforce a hard per-invocation cap on the total number of correction turns (output repair + tool-call feed-back combined) so that every invocation terminates. The cap is a **dedicated correction budget**, independent of `ctx.max_tool_calls`, with a **default of 2** correction turns and caller-overridable via the invocation context; every correction turn decrements it and exhaustion fails closed.
- **FR-011**: All correction turns MUST remain within the existing invocation deadline (`ctx.deadline_s`); a deadline reached during a correction yields the `timeout` outcome.
- **FR-012**: The Run record MUST account for the token usage and cost of **every** model turn in the invocation (original + all correction turns), computed from our own capability profiles (P7), never dropped or double-counted. This accounting MUST hold even when the invocation **fails closed** — usage/cost accrued across all turns (including the final failed turn) is still reported (a usage result is emitted), never silently dropped (P7/P8).

**Honesty & observability (US3, cross-cutting)**
- **FR-013**: Every invocation — clean, repaired, or failed-closed — MUST emit exactly one honest Run record (P8), with an outcome drawn from a closed taxonomy that distinguishes success, repaired success, `output_invalid`, and `tool_call_rejected`.
- **FR-014**: The outcome taxonomy MUST be extended with the new fail-closed states (`output_invalid`, `tool_call_rejected`) while preserving the existing outcomes and their meanings. When the correction budget is exhausted or a retry still fails, the Run MUST carry the **defect-type outcome** (`output_invalid` / `tool_call_rejected`), not `bounded_exit` (which stays reserved for the deferred P2 tool-execution loop). When a single turn carries **both** an output defect and a tool-call defect, `tool_call_rejected` takes precedence. Correction activity MUST be observable on the Run via a **`correction_turns` integer count** (0 for a clean invocation).
- **FR-015**: The new states MUST surface through the HTTP invoke endpoint as typed SSE frames (a normal `error` + `done` terminal consistent with the M4a frame contract), never as a raw 500 or a dropped/truncated stream.
- **FR-015a**: In streaming mode, intermediate correction-turn content MUST be suppressed from the client stream (never surfaced as provisional/invalid output); only the final validated result and a single typed terminal reach the caller. Suppressed turns MUST still be fully recorded on the Run (usage, cost, and the `correction_turns` count).
- **FR-016**: Both the streaming entry point (`invoke`) and the collecting entry point (`invoke_result`, used by eval) MUST apply identical enforcement and produce identical outcomes and Run records for equivalent inputs.

**Boundary / purity constraints**
- **FR-017**: All new enforcement logic MUST live in the pure runtime library; `runtime/` MUST remain import-pure (no web framework or database imports) — the existing purity tests MUST stay green (P6).
- **FR-018**: The feature MUST NOT introduce guardrail execution; `guardrail_config` remains data-only and the guardrail step is explicitly deferred (documented, not silently omitted) (P1 honesty).
- **FR-019**: The new states MUST NOT persist raw prompt/response content; the Run stores identity + metrics only (incl. `correction_turns`), preserving the no-raw-content guarantee from feature 006 (SC-009) (P5).

### Key Entities *(include if feature involves data)*

- **Outcome (extended taxonomy)**: the closed set of honest invocation results. Adds `output_invalid` (structured output could not be made valid within budget) and `tool_call_rejected` (a tool call could not be made valid within budget). `bounded_exit` stays **reserved and unused** by this feature. Existing outcomes unchanged.
- **Correction turn**: one extra model round-trip spent trying to fix a detected defect (a repair re-ask or a tool-call feed-back). Bounded by a **dedicated per-invocation correction budget (default 2, ctx-overridable)**; each contributes usage/cost to the Run and increments `correction_turns`.
- **Run record (observability)**: the single honest record per invocation. Gains a **`correction_turns` integer count** (0 when clean) and continues to carry summed usage/cost across all turns.
- **Validators (existing, reused)**: `validate_output` → `OutputValid | RepairNeeded`; `validate_tool_call` → `ToolCallOk | ToolCallRejected`. Reused as-is; this feature wires them into the pipeline.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In 100% of json-mode invocations, a caller either receives schema-valid structured output or a typed fail-closed result — never malformed/schema-violating JSON presented as valid.
- **SC-002**: In 100% of invocations, no unbound or schema-violating tool call is surfaced to the caller as an executable/legitimate tool call.
- **SC-003**: A repairable output defect that the model fixes on the first re-ask results in a successful invocation after exactly one additional model turn (not zero, not more than one).
- **SC-004**: The number of extra model turns per invocation is bounded by a fixed cap in 100% of cases — no invocation issues an unbounded number of correction turns, even against an adversarial always-invalid model.
- **SC-005**: Every invocation (clean, repaired, or failed-closed) produces exactly one Run record whose summed usage/cost covers every model turn made.
- **SC-006**: No new failure state surfaces at the HTTP boundary as an untyped 500 or a truncated stream; 100% terminate in a typed frame sequence.
- **SC-007**: `runtime/` remains import-pure (purity tests green) and no live/paid provider calls occur in the test suite (gateway mocked).
- **SC-008**: The streaming and collecting entry points produce identical outcomes and Run records for equivalent inputs (verified by parallel tests).
- **SC-009**: Every Run reports an accurate `correction_turns` count (0 for a clean invocation; equal to the number of correction turns actually taken otherwise), and in streaming mode no intermediate correction-turn content reaches the caller (verified by asserting the client stream carries only the final validated result + one typed terminal).

## Assumptions

- **Scope decisions (owner-confirmed 2026-11-06):** output validation includes **one bounded repair re-ask** (not fail-closed-only); tool-call enforcement includes **one bounded feed-back re-ask / self-correction** (not reject-and-surface-only); **guardrail execution is deferred** to a later P2 feature (guardrail_config stays data-only).
- The feed-back self-correction re-asks the model **only on a rejected tool call**; it does **not** execute valid tool calls and does **not** implement the general agentic tool-execution loop (P2). A valid tool call remains terminal/surfaced as today.
- The existing validators (`validate_output`, `validate_tool_call`) are correct and sufficient and are reused unchanged; this feature is wiring + the bounded correction loop + outcome/observability extensions, not new validation logic.
- The correction cap is a **dedicated correction budget** (default 2 total correction turns per invocation, caller-overridable via the context), **independent of `ctx.max_tool_calls`** (which is reserved for the deferred P2 tool-execution loop) — resolved in Clarifications 2026-11-06.
- TDD with genuine behavioral RED shown before GREEN (P9); the gateway is mocked for all tests (no live/paid calls); the M4a SSE frame contract and the single-Run-per-invocation invariant are preserved.
- Work continues on the shared `feat/backend-m0-foundation` branch (no per-feature branch hook is configured); the feature pointer is `specs/007-ai-output-trust-boundary`.
