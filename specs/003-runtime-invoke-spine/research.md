# Research & Decisions: Runtime Invoke Spine (M2a)

**Feature**: `003-runtime-invoke-spine` · **Date**: 2026-11-04

This resolves the open technical choices the spec deliberately left to planning, and records a
**direction check** (the `/speckit-plan` arg: "verify and check overall direction of development").
Decisions marked ⚠ **DIVERGENCE** depart from a prior design doc and are called out for owner review.

---

## D1 — Orchestration: a plain async pipeline, not LangGraph (for M2a) ⚠ DIVERGENCE from `backend/03 §2`

**Decision**: Implement the spine as a straight `async` pipeline of discrete, individually-testable
steps — `assemble → validate_input → budget_check → gateway_stream → surface_events → record_run` — in
`services/runtime/invoke.py`, with **no graph library**.

**Rationale**: M2a is a *single generate step*. There is no branching, no agentic loop, no
checkpointer, no retrieval/tool node in scope. LangGraph (the `backend/03` choice) earns its keep only
once there are multiple nodes and conditional edges; for one node it adds a dependency and scaffolding
(`graph/state.py`, `nodes.py`, `build.py`) for zero branching value. The pipeline steps are written as
**separate pure functions**, so when the tool-exec loop / RAG nodes actually land (later milestone) each
step drops into a LangGraph node body with no logic rewrite — we adopt the graph when it buys branching,
not before.

**Alternatives considered**: (a) Minimal LangGraph single-node now (per `backend/03`) — rejected: dep +
scaffolding cost with no current payoff, and it would still be re-shaped when real nodes arrive.
(b) A generic plugin/middleware chain — rejected as over-engineering for five fixed steps.

**Action**: `backend/03 §2 "Graph shape"` should be annotated that LangGraph adoption is deferred to the
first feature that introduces branching (tool-exec / RAG); M2a is a plain pipeline. (Doc update, not
blocking.)

---

## D2 — `invoke` contract: streaming-first, with the Run delivered via a sink on the context

**Decision**: Two entry points in `services/runtime/invoke.py`, both pure:
- `async def invoke(snapshot, inputs, ctx) -> AsyncIterator[NormalizedStreamEvent]` — re-surfaces the
  gateway's normalized events (content / reasoning / tool-call / usage / stop) unchanged, and at
  completion calls `ctx.record_run(run)` **exactly once** with the finished `RunRecord`.
- `async def invoke_result(snapshot, inputs, ctx) -> InvokeResult` — a thin collector over `invoke()`
  for non-streaming callers (eval): consumes the stream, returns the accumulated `InvokeResult`
  (which embeds the `RunRecord`).

**Rationale**: Playground + channel are streaming SSE callers (need the event iterator); eval + a
simple published-invoke want the collected result. One streaming primitive + one collector serves all
four without caller-specific branches (FR-003). Delivering the Run via a **sink callback** keeps the
provider event stream pure (no runtime-only "RUN" event injected into the provider vocabulary) and keeps
the runtime free of persistence (P6) — the caller supplies the sink and owns storage (M4).

**Alternatives considered**: (a) Inject a terminal `RUN` event into the stream — rejected: pollutes the
provider IR with a runtime concept. (b) Return `(stream, awaitable_run)` tuple — rejected: clumsier than
a sink + collector. (c) Generator `return`-value via `StopAsyncIteration` — rejected: not ergonomic for
SSE consumers.

---

## D3 — Run production stays I/O-free (P6): `RunRecord` is a value; `RunSink` is a protocol

**Decision**: `RunRecord` is a frozen value object the runtime *produces*. The context carries a
`record_run: Callable[[RunRecord], Awaitable[None] | None]` sink. M2a ships an in-memory sink for tests;
the DB-backed, tenant-isolated sink is **M4** (API/worker). The runtime never touches a DB/session.

**Rationale**: Keeps the P6 purity invariant intact (the purity test stays green) while still
"observability from day one" — the complete record exists at the seam; only its persistence is deferred.

---

## D4 — Context-budget guard (F4): a conservative `count_tokens` abstraction, heuristic backend now

**Decision**: Add `services/runtime/tokens.py` exposing `count_tokens(model, messages, tools, system)
-> int` behind a small abstraction, with a **conservative deterministic heuristic** backend for M2a
(character-based with an over-estimate margin, rounding up). The guard compares the estimate against
`profile.limits.context_tokens` (from the M1 profile) and fails closed *before* the gateway call.

**Rationale**: M2a is fully mocked and the guard only needs to be *safe* (reject clear overflow before
spend), not billing-exact. A heuristic keeps the runtime dependency-light and offline. The abstraction
boundary lets exact per-provider tokenizers (e.g. `tiktoken`, provider token-count endpoints) slot in at
Phase-5 live integration without touching callers. The heuristic MUST over-estimate (margin up) so it
never lets a real overflow through to be truncated by the provider.

**Alternatives considered**: (a) `tiktoken` now — deferred: adds a dep for little M2a value while mocked;
it's the documented upgrade path. (b) `litellm.token_counter` — rejected for M2a: it would import litellm
into the runtime pipeline, eroding the "transport.py is the only litellm importer" cleanliness.

---

## D5 — Deadline enforcement (C5): runtime-level async timeout now; transport pass-through noted

**Decision**: Wrap stream consumption in an async timeout bound by `ctx.deadline_s` (default 30s). On
expiry: stop consuming, emit best-effort partial, record a `timeout` outcome with partial usage/cost.
Additionally pass a transport-level `timeout` through the gateway to litellm as a cheap belt-and-
suspenders (closes review gap C5 at the seam) — a small, additive change to the M1 transport call, guarded
so M1's mocked tests are unaffected.

**Rationale**: The runtime-level timeout is the testable M2a mechanism (bounds total latency even if a
single long generation would overrun). The transport pass-through hardens the real-call path for Phase 5.

**Alternatives considered**: transport-only timeout — rejected: doesn't bound non-network stalls in the
runtime; runtime-only — acceptable but leaves C5 open at the seam, so we do both.

---

## D6 — `max_tool_calls`: carried on the context, NOT loop-enforced in M2a ⚠ SPEC RECONCILIATION

**Decision**: `ctx.max_tool_calls` (default 8) is defined and carried as a **stable contract field**, but
M2a does **not** actively loop-enforce it, because M2a has **no tool-execution loop** (tool execution is
deferred). There are zero tool-call *cycles* to bound in a single generate step; the model may *request*
tool calls, which M2a surfaces as events (D2/US4) but does not execute. The bound's active enforcement
point is the agentic tool-exec loop (a later feature), where `backend/03 §4` already places it.

**Rationale / direction note**: The spec's **FR-010 / US3 acceptance-scenario 2** ("a model that would
call tools N+1 times stops at N") **cannot be exercised in M2a** — there is no loop to exceed. Enforcing
the *deadline* (D5) is the real, testable bound this feature delivers. Carrying `max_tool_calls` now keeps
the `ctx` contract stable so the later loop doesn't change the signature.

**Recommended spec reconciliation** (surface to owner; `/speckit-plan` does not edit `spec.md`): narrow
US3/FR-010 for M2a to the **deadline** bound, and move the tool-call-cycle bound to the tool-execution
feature (keeping `max_tool_calls` as a carried-but-not-yet-enforced field). `/speckit-analyze` will flag
this as a coverage gap otherwise.

---

## D7 — Input validation + request assembly: reuse the schema helper; new runtime-level module

**Decision**: Add `validate_input(inputs, io_contract)` in a runtime-level module (`runtime/io.py`),
reusing `providers.validation._schema_errors` (Draft 2020-12). Add request assembly
`build_normalized_request(snapshot, inputs)` → `NormalizedRequest` mapping: `system_prompt → system`,
`model_config → provider/model/api_version/params/reasoning`, `tools (ToolRef.schema_snapshot) → tools`,
`io_contract.output_format == "json" → response_format`, `inputs → messages`. Fail closed on invalid
input before assembly completes (no gateway call, no spend).

**Rationale**: The output/tool validators (`validate_output`, `validate_tool_call`) already exist in
`runtime/providers/validation.py` and are tested — they are the **M2b** trust boundary and are *not*
wired here. M2a only needs the missing **input** validator + the request assembler.

**Location debt (direction note)**: `validation.py` currently lives under `runtime/providers/` but is
runtime-pipeline logic, not provider-gateway logic. Moving it is churn we defer; M2b should relocate it to
a runtime-level module when it wires the output/tool validators. Flag only, not blocking.

---

## Direction check (overall development) — the `/speckit-plan` arg

- **The invariant holds.** M2a delivers exactly the one shared, pure `runtime.invoke` the whole product
  rests on (P6). It reads the frozen snapshot, not the live row (editing-changes-nothing-live is enforced
  in code). ✅
- **The split is sound and *lower* risk than feared.** The M2b trust-boundary functions
  (`validate_output`/`validate_tool_call`) already exist and are tested — M2b is mostly *wiring + a repair
  loop*, not new logic. M2a's genuinely new surface is the pipeline, the input validator, token counting,
  the deadline, and the Run record — all deterministic and mockable. ✅
- **M1 integration points are real.** `gateway.stream(req, *, api_key, profile)` yields the normalized
  events M2a re-surfaces; `NormalizedRequest` has every field the assembler needs; profiles carry
  `limits.context_tokens` for the budget guard. ✅
- **Three things to watch (flagged above):** D1 (plain pipeline vs the doc's LangGraph), D6 (the
  `max_tool_calls` bound is vacuous until the tool-exec loop — spec needs a small reconciliation), and D7
  (validation.py location debt). None block M2a; all are recorded.
- **Downstream coherence:** M2a (spine) → M2b (wire output/tool validators + repair) → M3 (eval runs
  through the *same* invoke) → M4 (API/worker persists the Run under RLS + SSE surface) → Phase 5 (first
  live E2E, confirms the Anthropic usage shape C3). Each consumes the prior without rework. ✅
