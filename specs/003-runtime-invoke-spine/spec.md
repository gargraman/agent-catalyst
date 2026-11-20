# Feature Specification: Runtime Invoke Spine (M2a)

**Feature Branch**: `feat/backend-m0-foundation` (continuation; no dedicated branch created — no git hook configured)

**Created**: 2026-11-03

**Status**: Draft

**Input**: User description: "M2 — the runtime.invoke spine (the load-bearing invariant). Scoped by owner decision (2026-11-03) to **M2a**: build the single execution pipeline every caller shares — read a frozen version snapshot, validate inputs, run the call through the M1 provider gateway, surface the full stream, bound the work by a deadline and tool-call limit, and produce one honest, observable Run record for every invocation. **Fully mocked** (no live/paid provider calls in this feature). The AI-output trust boundary (output-schema validation + repair, tool-call enforcement) is **deferred to a separate M2b feature**; the first real end-to-end provider call is deferred to the Phase-5 walking-skeleton."

---

## Overview *(context)*

The platform's load-bearing architectural promise is that **playground, evaluation, published invoke, and channel all execute an agent through the *same* runtime path** — `runtime.invoke(snapshot, inputs, ctx)`. If those paths diverged, an evaluation would score code the live endpoint never runs, and every honesty guarantee the product sells would be hollow. M1 built the provider gateway (one normalized call to any provider, honest cost, explicit substitutions). M1 is not, by itself, runnable as an agent: nothing yet reads a *published, frozen* agent configuration, decides what to send, bounds the work, or records what happened.

This feature delivers that single spine — **M2a** — as a pure library the API, the eval worker, and (later) the channel all call. It reads an **immutable version snapshot** (never a live, editable agent row — that is how "editing an agent changes nothing live until an eval-gated re-point" is enforced in code), validates the caller's inputs, runs the call through the M1 gateway, surfaces the complete stream back to the caller, holds the work inside an explicit time budget (and carries a tool-call budget for the later agentic loop), and emits **one honest Run record for every invocation** — success, failure, timeout, or degradation alike.

This is **M2 production expansion**, authorized by the 2026-11-03 advisory-gate decision in `docs/scope.md` (viability gate held OPEN; build proceeds in parallel, publish-gate advisory). Per the owner scope decision of 2026-11-03, this feature is **M2a** and is **fully mocked** — the AI-output trust boundary is carved out to **M2b**, and the first live/paid provider call is carved out to the Phase-5 end-to-end run.

## Clarifications

### Session 2026-11-04

- Q: Should the tool-call budget (`max_tool_calls`) be actively enforced in M2a, given there is no tool-execution loop in scope? → A: No — keep `max_tool_calls` as a carried-but-not-enforced context field; the **deadline** is the only active bound in M2a. Active loop-enforcement of the tool-call budget moves to the tool-execution feature that introduces the agentic loop. (Resolves plan decision D6.)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - One shared execution path runs a frozen snapshot (Priority: P1)

Every way of running an agent — trying it in the playground, scoring it in an evaluation, calling it as a published endpoint, or reaching it through a channel — must go through **one** execution function that takes a frozen version snapshot, the caller's inputs, and a call context, and returns a normalized result. The function reads the **immutable snapshot**, never the live agent row, so that what is evaluated is exactly what is served.

**Why this priority**: This is the invariant the entire product rests on. Without a single shared `invoke`, evaluation parity is a fiction and there is no runtime to wrap with an API, a worker, or a channel. Everything else in M2+ builds on it.

**Independent Test**: Call the execution function directly with a known snapshot, valid inputs, and a context, against a mocked provider gateway; confirm it returns a normalized result assembled from the snapshot's configuration, and confirm it read only the frozen snapshot (mutating the originating agent row after the snapshot is taken changes nothing about the result).

**Acceptance Scenarios**:

1. **Given** a frozen version snapshot and valid inputs, **When** the execution function is invoked, **Then** it runs the call through the provider gateway and returns a single normalized result (content, final usage, cost, substitutions, outcome).
2. **Given** a snapshot taken from an agent version, **When** the underlying agent is later edited, **Then** a repeat invocation of the same snapshot produces the same configuration-driven behavior (the live edit has no effect on the snapshot run).
3. **Given** the execution function, **When** its implementation is inspected, **Then** it contains no caller-specific branches (no "if playground / if eval / if API") — the single path is identical for all callers.

---

### User Story 2 - Every invocation produces one honest, observable Run record (Priority: P1)

For every invocation — whether it succeeds, fails, times out, or degrades — the platform MUST produce exactly one Run record capturing what ran and what it cost: the identity of the model/profile/version/prompt that executed, token usage, independently-computed cost, latency, the explicit outcome, the tenant it ran for, and a correlation identifier. A failure or a degraded provider stream MUST still produce a Run that names the degradation; it must never be silently dropped or recorded as a success.

**Why this priority**: Observability and honest failure are constitutional promises (P8). Spend enforcement, drift economics, operator trust, and debugging all depend on a complete, truthful record of every run from day one. A run the system can't account for is worse than no run.

**Independent Test**: Invoke the function across four paths — clean success, provider failure, deadline exceeded, and degraded stream (no usage reported) — and confirm each yields exactly one Run record whose outcome field correctly distinguishes the four cases and whose cost/usage/latency/identity/correlation fields are populated (or explicitly marked absent) in each.

**Acceptance Scenarios**:

1. **Given** a successful invocation, **When** it completes, **Then** exactly one Run record is produced with model/profile/version/prompt identity, usage, our-computed cost, latency, a `success` outcome, workspace, caller/source, and a correlation id.
2. **Given** a provider failure surfaced by the gateway, **When** the invocation ends, **Then** exactly one Run record is produced with an `error` outcome and the failure reason, not a success and not a dropped record.
3. **Given** a provider stream that ends without a usage report (degraded), **When** the invocation ends, **Then** the Run is recorded as a degraded outcome (explicitly, never as silent success), with usage/cost marked absent or best-effort.
4. **Given** any invocation, **When** the Run is produced, **Then** the cost on the Run equals the cost the gateway independently computed (the spine does not recompute or re-source cost).

---

### User Story 3 - Work is bounded by an explicit deadline (Priority: P1)

Every invocation MUST run inside an explicit wall-clock deadline carried on the call context. When the deadline is reached the invocation MUST stop and exit explicitly with a timeout outcome — it must never hang indefinitely — and the partial work done so far MUST still be recorded on the Run. A maximum tool-call count is also carried on the context as a stable field, but it is **not actively enforced in this feature** (there is no tool-execution loop in M2a to bound); its active enforcement belongs to the later feature that introduces the agentic tool-execution loop (see Clarifications 2026-11-04).

**Why this priority**: Unbounded work is both a reliability failure and a cost/abuse hole; a deterministic, fail-closed deadline is a P3 control (enforced in code, not left to model behavior). It ships with the core because an unbounded spine cannot be safely exposed to any caller. The tool-call budget is carried now so the context contract stays stable when the loop later activates it.

**Independent Test**: Invoke with a deadline shorter than a (mocked) slow stream and confirm the invocation stops at (or within a small margin of) the deadline with a timeout outcome and a recorded Run; confirm `max_tool_calls` is present on the context and carried onto the Run without altering single-turn behavior.

**Acceptance Scenarios**:

1. **Given** a call context with a deadline, **When** the provider stream runs past that deadline, **Then** the invocation stops at (or within a small margin of) the deadline and records a timeout outcome.
2. **Given** a timeout exit, **When** the Run is produced, **Then** it records the partial usage/cost/latency observed up to the exit and names the deadline as the bound that was hit.
3. **Given** a call context carrying a `max_tool_calls` value, **When** a single-turn invocation runs, **Then** the value is carried through to the Run but does not alter behavior (no loop exists to bound in this feature).

---

### User Story 4 - The full provider stream is surfaced to the caller (Priority: P2)

Whatever the provider actually produces — incremental content, reasoning deltas, tool-call requests, and the terminal stop reason — MUST be surfaced to the caller through the shared event vocabulary. In particular, a model that responds by requesting a tool (and emits no plain content) MUST NOT appear to the caller as an empty or degraded response; its tool-call events and stop reason MUST be visible.

**Why this priority**: The gateway already normalizes these event types, but an execution spine that forwards only text would silently erase tool-calling and reasoning behavior — exactly the trap flagged in the architecture review (gap C4). Fidelity here is what makes M2b (tool-call enforcement) and any tool-using agent possible. It is layered on the working single-path core.

**Independent Test**: Drive the function with a mocked gateway stream that emits content, a reasoning delta, a tool-call sequence, and a terminal stop reason, and confirm every one of those event classes is observable by the caller and the terminal stop reason is recorded — none are dropped or flattened.

**Acceptance Scenarios**:

1. **Given** a mocked stream that emits content, reasoning, tool-call, and stop events, **When** the function runs, **Then** the caller observes each event class in the shared vocabulary (none silently dropped).
2. **Given** a mocked stream whose only output is a tool-call request (no plain content), **When** the function runs, **Then** the result surfaces the tool-call and a non-empty outcome — not an empty success and not a degraded error.
3. **Given** any completed stream, **When** it ends, **Then** the terminal stop reason (e.g. natural stop vs length vs tool-use vs content-filter) is captured on the result/Run.

---

### User Story 5 - Oversized inputs are caught before any provider call (Priority: P2)

Before issuing the provider call, the spine MUST validate the caller's inputs against the snapshot's input contract and MUST check that the assembled request fits within the model's context budget. Invalid inputs or a request that would overflow the model's context MUST be rejected **before** any provider call is made, with an explicit outcome — never passed through to be silently truncated or rejected (and billed) by the provider.

**Why this priority**: Failing closed before spend is a P3 control and prevents a class of silent-truncation correctness bugs (review gap F4 — no context/token accounting existed). It is P2 because the core path and its budget/observability (US1–US3) must exist first.

**Independent Test**: Invoke with inputs that violate the snapshot's input contract and confirm rejection before any gateway call (zero provider interaction); invoke with inputs whose token size exceeds the model's context budget and confirm an explicit context-overflow outcome before any gateway call.

**Acceptance Scenarios**:

1. **Given** inputs that do not satisfy the snapshot's input contract, **When** the function is invoked, **Then** it fails closed with a validation outcome and makes no provider call.
2. **Given** inputs whose counted size exceeds the model's context budget, **When** the function is invoked, **Then** it exits with an explicit context-overflow outcome and makes no provider call.
3. **Given** a rejection before the call (invalid input or context overflow), **When** the Run is produced, **Then** it records the rejection outcome with zero provider cost.

---

### Edge Cases

- **Snapshot names a retired/unknown model**: the gateway fails closed (config error); the spine records a Run with a configuration-error outcome and makes no partial charge.
- **Deadline already elapsed at call start**: the invocation exits immediately with a timeout outcome and a zero-work Run (no provider call).
- **Provider failure after the first content token**: the spine records what streamed plus the failure; it does not silently retry or double-emit (retry/fallback is explicitly out of scope — see Out of Scope).
- **Degraded stream (gateway emits its degraded ERROR with no usage)**: recorded as a degraded outcome, never a success.
- **Model requests tool calls in M2a**: the tool-call events are surfaced to the caller (US4); the `max_tool_calls` budget is **not** applied (no tool-execution loop exists to bound — see Clarifications 2026-11-04), so the requests are not executed and nothing is looped.
- **Missing correlation id on the context**: the spine mints one so every Run is still correlatable (no un-traceable runs).
- **Empty/no content but a clean stop**: recorded as a `success` with empty content (distinct from a degraded/error outcome).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST expose a single execution entry point that accepts an immutable version snapshot, caller inputs, and a call context, and returns one normalized result.
- **FR-002**: The execution path MUST read only the frozen version snapshot provided; it MUST NOT read or depend on the live, editable agent record.
- **FR-003**: The execution path MUST be free of caller-specific branching — the same path serves playground, evaluation, published invoke, and channel callers.
- **FR-004**: The system MUST validate caller inputs against the snapshot's input contract and fail closed (no provider call) when inputs are invalid.
- **FR-005**: The system MUST estimate the assembled request's size against the model's context budget and fail closed with a context-overflow outcome (no provider call) when it would not fit.
- **FR-006**: The system MUST execute the model call through the M1 provider gateway and MUST NOT call any provider directly or by any other path.
- **FR-007**: The system MUST surface every stream event class the gateway emits — incremental content, reasoning, tool-call start/delta/end, usage, and the terminal stop reason — to the caller through the shared event vocabulary, dropping none.
- **FR-008**: The system MUST capture the terminal stop reason of the stream on the result and the Run.
- **FR-009**: The system MUST enforce a wall-clock deadline carried on the context, stopping the invocation at or within a small margin of the deadline and recording a timeout outcome.
- **FR-010**: The system MUST carry a maximum tool-call count on the context as a stable field and propagate it onto the Run; active loop-enforcement of this bound is **deferred** to the later tool-execution feature (M2a has no tool-execution loop to bound — see Clarifications 2026-11-04). The deadline (FR-009) is the only actively enforced bound in this feature.
- **FR-011**: The system MUST produce exactly one Run record for every invocation regardless of outcome (success, validation failure, context overflow, configuration error, provider error, timeout, or degraded stream).
- **FR-012**: Each Run record MUST include: model/profile/version/prompt identity, token usage, the independently-computed cost carried from the gateway, latency, an explicit outcome/status, the owning workspace, the caller/source, and a correlation identifier.
- **FR-013**: The system MUST record a degraded or failed invocation with an explicit non-success outcome; it MUST NOT record such an invocation as a success and MUST NOT silently drop its Run.
- **FR-014**: The Run's cost MUST equal the cost the gateway computed; the spine MUST NOT recompute or re-source cost.
- **FR-015**: The Run record MUST carry the owning workspace so it can be stored under tenant isolation by the persisting caller (persistence itself is out of scope — see Out of Scope).
- **FR-016**: The execution library MUST remain pure — it MUST NOT import any inbound web framework or HTTP server, so the API, the eval worker, and the channel can all share it (verified by the existing runtime-purity check).
- **FR-017**: The capability substitutions produced by the gateway MUST be carried through to the result and the Run unchanged (no silent loss of the M1 substitution ledger).
- **FR-018**: Model output MUST be returned to the caller as produced; semantic validation, repair, and tool-call argument/name enforcement are explicitly NOT performed in this feature (deferred to M2b — see Out of Scope).
- **FR-019**: The provider credential MUST be sourced from configuration/environment for this feature; secure custody (vault) is out of scope.

### Key Entities *(include if feature involves data)*

- **Version Snapshot** *(existing, from M0)*: the immutable, fully-resolved agent configuration the spine executes — model/capability profile, system prompt, input/output contract, context settings, and bindings. Read, never written, by this feature.
- **Call Context**: the per-invocation envelope carrying the owning workspace, the caller/source, a correlation identifier, the wall-clock deadline, the maximum tool-call count, and the provider-credential source. Supplied by the caller.
- **Invoke Result**: the normalized return value — streamed/accumulated content, surfaced tool-call and reasoning events, terminal stop reason, final usage, our-computed cost, carried substitutions, and the explicit outcome.
- **Run Record**: the observability record of one invocation — identity (agent/version/model/profile/prompt), usage, cost, latency, outcome/status, workspace, caller/source, correlation id. Mirrors the shape the frontend already models for a run; produced here, persisted by a later caller.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of invocations — across success, validation failure, context overflow, configuration error, provider error, timeout, and degraded stream — produce exactly one Run record with a correct, distinguishing outcome.
- **SC-002**: The execution library has zero inbound web-framework/HTTP-server imports (the runtime-purity check passes).
- **SC-003**: No invocation runs past its deadline by more than a small, stated margin (the deadline is the actively enforced bound; `max_tool_calls` is carried but not enforced in this feature).
- **SC-004**: Every stream event class the gateway emits is observable by the caller in a representative multi-event stream — none dropped or flattened (in particular, a tool-call-only response is never surfaced as empty/degraded).
- **SC-005**: Invalid inputs and context-overflowing requests are rejected before any provider interaction — zero provider cost is incurred on such invocations.
- **SC-006**: For every recorded Run, the cost equals the gateway's independently-computed cost to the platform's monetary precision (no recomputation drift).
- **SC-007**: Every Run carries a non-empty correlation identifier (no un-traceable invocation).
- **SC-008**: No live or paid provider call is made anywhere in this feature's tests or code (all provider interaction is mocked).

## Assumptions

- Reuses, and does not rebuild, M0's `VersionSnapshot` and its assembly, and M1's provider gateway, cost computation, normalized event vocabulary, and the input-contract validator already present in the runtime.
- The provider credential is taken from configuration/environment for this feature; secure vault custody is a later-phase concern.
- Run **persistence** (writing the record to tenant-isolated storage) belongs to the API/worker feature (M4); this feature's deliverable is producing the complete Run record as a value and handing it to a sink the caller supplies, so the pure-library boundary holds.
- The deadline and tool-call limit have sensible platform defaults when a caller does not specify them, and the caller may tighten them.
- "Fully mocked" means every test substitutes a canned gateway/transport; the first real end-to-end provider call is the Phase-5 walking-skeleton, under the constitution's paid-call protocol.

## Out of Scope *(deferred — tracked)*

- **AI-output trust boundary → M2b (next feature)**: validating model output against the snapshot's output contract, the bounded repair loop for malformed/schema-violating output, and enforcement of tool-call names/arguments against the bound tool schemas (review gaps F1/F2/F3). This feature returns model output as-is.
- **Live/paid provider call → Phase 5**: the real end-to-end run and the on-the-wire confirmation of the Anthropic usage-shape mapping (review gap C3), under the paid-call protocol.
- **Retry / fallback / circuit-breaking**: not performed; a single attempt is made and its outcome recorded (resilience layer is a later milestone).
- **Real guardrails, retrieval execution, tool execution, budget reservation, and tracing-backend spans**: present only as typed, no-op seams the pipeline will later fill; not implemented here.
- **Run persistence + tenant-isolated storage, and the HTTP/SSE surface**: the API/worker feature (M4).
- **Secure provider-key custody (vault)**: later phase (credential comes from config/env here).

## Dependencies & Governance

- Governed by the project constitution (`.specify/memory/constitution.md`): **P6** (one pure `runtime.invoke`; immutable snapshots), **P3** (deterministic, fail-closed controls: input validation, context-budget guard, deadline; untrusted inputs), **P8** (observable, honest degradation: one Run per invocation, explicit outcome), **P7** (cost carried from our own computation, never re-sourced), **P5** (every Run carries its tenant for isolated storage downstream), and **P9** (genuine behavioral RED before implementation).
- Builds directly on M0 (`specs/001-m0-data-model`) and M1 (`specs/002-provider-gateway`).
- Authorized by the 2026-11-03 advisory-gate decision in `docs/scope.md` (production build proceeds with the viability gate held OPEN; publish-gate advisory).
