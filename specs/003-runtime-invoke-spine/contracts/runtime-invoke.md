# Contract: `runtime.invoke` (M2a)

**Feature**: `003-runtime-invoke-spine` · **Date**: 2026-11-04

The internal library contract every caller (playground, eval, published invoke, channel) uses. This is a
**library contract**, not an HTTP contract — the HTTP/SSE surface is M4. Types: see
[data-model.md](../data-model.md).

---

## Entry points (`services/runtime/invoke.py`)

### `async def invoke(snapshot, inputs, ctx) -> AsyncIterator[NormalizedStreamEvent]`

- **Inputs**: a frozen `VersionSnapshot`, caller `inputs`, a `RunContext`.
- **Yields**: the M1 normalized event stream **unchanged and complete** — `TEXT_DELTA`,
  `REASONING_DELTA`, `TOOL_CALL_START/DELTA/END`, `USAGE`, `STOP`, `ERROR`. No event class is dropped or
  flattened (FR-007). A tool-call-only response still yields its tool-call events (never an empty success
  — FR-007/US4).
- **Side effect**: calls `ctx.record_run(run)` **exactly once** at completion with the finished
  `RunRecord`, for **every** outcome (FR-011).
- **Purity**: no web-framework/HTTP/DB import; reads only the passed snapshot (FR-002/FR-016).

### `async def invoke_result(snapshot, inputs, ctx) -> InvokeResult`

- Thin collector over `invoke()` for non-streaming callers (eval). Consumes the stream, returns the
  accumulated `InvokeResult` (which embeds the same `RunRecord` handed to the sink).

---

## Pipeline order (fail-closed before spend)

```
1. assemble guard   validate_input(inputs, io_contract)      ─ fail → outcome=invalid_input,   NO gateway call
2. budget guard     count_tokens(...) ≤ limits.context_tokens ─ fail → outcome=context_overflow, NO gateway call
3. build request    build_normalized_request(snapshot, inputs)
4. deadline wrap    enforce ctx.deadline_s around steps 5–6
5. execute          gateway.stream(req, api_key=ctx.api_key, profile=<from snapshot/resolve>)
6. surface          re-yield each event; accumulate text/tool_calls/reasoning/usage/stop/error
7. record           build RunRecord(outcome, usage, cost carried from gateway, latency, identity, …)
                    → ctx.record_run(run)  (exactly once)
```

Steps 1–2 run **before** any provider interaction; a rejection there incurs zero provider cost
(FR-004/FR-005/SC-005).

---

## Outcome mapping (what the caller observes)

| Situation | Yielded stream | `RunRecord.outcome` | Provider call |
|---|---|---|---|
| clean completion | content…, `USAGE`, `STOP` | `success` | yes |
| empty-but-clean | `STOP` (no content) | `success` (empty text) | yes |
| invalid inputs | `ERROR` (validation) then end | `invalid_input` | no |
| context overflow | `ERROR` (overflow) then end | `context_overflow` | no |
| unknown/retired model | `ERROR` (config) then end | `config_error` | no |
| provider failure | gateway `ERROR` | `provider_error` | attempted |
| deadline reached | partial…, `ERROR` (timeout) | `timeout` | attempted (partial usage/cost) |
| degraded (no usage) | content…, gateway degraded `ERROR` | `degraded` | yes |

**Invariants**
- Exactly **one** `RunRecord` per call, always via the sink, regardless of outcome (FR-011).
- `RunRecord.cost_usd` is the gateway's computed cost, **never** recomputed/re-sourced (FR-014/P7).
- A failed/degraded run is **never** recorded as `success` and **never** dropped (FR-013/P8).
- `RunRecord.correlation_id` is always non-empty (minted if the ctx omitted it — SC-007).
- `RunRecord.substitutions` equals the gateway's ledger, unchanged (FR-017).

---

## Explicitly NOT in this contract (deferred)

- **Output-schema validation + repair** (`validate_output`) and **tool-call enforcement**
  (`validate_tool_call`) — **M2b**. M2a returns model output as-is (FR-018); tool-call requests are
  surfaced, not validated or executed.
- **Active `max_tool_calls` loop enforcement** — deferred with the tool-execution loop (research D6);
  the field is carried on `ctx` but no loop exists to bound in M2a.
- **Retry / fallback / circuit-breaking**, **retrieval**, **tool execution**, **memory/checkpointer**,
  **budget reservation**, **tracing-backend spans** — later milestones (typed seams only).
- **Run persistence + tenant-isolated storage** and the **HTTP/SSE** surface — M4.
- **Any live/paid provider call** — Phase 5 (M2a is fully mocked, SC-008).
