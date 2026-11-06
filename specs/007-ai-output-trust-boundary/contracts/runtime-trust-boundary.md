# Contract — Runtime Trust Boundary (M2b)

This feature changes the **behavioral contract** of `runtime.invoke` / `invoke_result` and confirms the **SSE frame mapping** at the API. It does **not** add HTTP endpoints or change request/response schemas — the M4a SSE frame vocabulary is unchanged.

## 1. `runtime.invoke(snapshot, inputs, ctx)` / `invoke_result(...)` — behavioral contract

### Mode selection
- **Pass-through** (unchanged from M2a): `snapshot.io_contract.output_format != "json"` AND `not snapshot.tools` → events stream live; no validation; no correction.
- **Validated** (new): `output_format == "json"` OR `snapshot.tools` non-empty → turns buffered; output/tool-call validation applied; bounded correction loop; only final validated events emitted.

### Guarantees (validated mode)
1. A caller never receives malformed or schema-violating JSON presented as a valid structured result (FR-001/003, SC-001).
2. A rejected tool call (unbound name or schema-violating args) is never surfaced as a legitimate/executable tool call (FR-006, SC-002).
3. On a detected defect with budget remaining, exactly one correction turn is issued per defect resolution attempt; `ctx.max_corrections` (default 2) caps total correction turns; exhaustion fails closed (FR-002/007/010, SC-003/004).
4. The whole invocation (original + corrections) is bounded by `ctx.deadline_s`; a deadline during correction yields `timeout` (FR-011).
5. A **valid** tool call is surfaced unchanged; tools are **not executed** (FR-009).
6. Exactly one `RunRecord` per invocation, with usage/cost summed across all turns and an accurate `correction_turns` count (FR-012/013, SC-005/009).
7. `invoke` (streaming) and `invoke_result` (collecting) produce identical outcomes and Run records for equivalent inputs (FR-016, SC-008).
8. Intermediate correction-turn content is suppressed from the streamed events; only the final validated events (or one typed `ERROR`) are yielded (FR-015a).

### Outcomes
`success` (incl. repaired success), existing error outcomes (`provider_error`, `timeout`, `degraded`, `invalid_input`, `context_overflow`, `config_error`), plus new: **`output_invalid`**, **`tool_call_rejected`**. `bounded_exit` unused.

## 2. Emitted events (validated mode)

| Situation | Events yielded (in order) |
|---|---|
| Valid output (json) | `TEXT_DELTA`(validated text) → `USAGE`(summed) → `STOP` |
| Valid tool call(s) | `TOOL_CALL_END`(each valid call) [+ `TEXT_DELTA` if any] → `USAGE` → `STOP` |
| Repaired success | same as valid; intermediate (invalid) turn **not** emitted; `correction_turns ≥ 1` |
| Fail-closed (`output_invalid` / `tool_call_rejected`) | `ERROR`{error:{type:<outcome>, message}} → `USAGE`(summed) |
| Provider/timeout/degraded during any turn | existing behavior (`ERROR` and/or outcome), one Run |

No new `EventType` values are introduced. `USAGE` is emitted even on fail-closed so spend is reported (P7/P8).

## 3. SSE frame mapping at the API (`api/routers/invoke.py` + `api/sse.py`) — UNCHANGED

The existing encoder already covers the new outcomes:

| Runtime event / state | SSE frame |
|---|---|
| `EventType.ERROR` (new outcomes included) | `event: error` `{code: <outcome>, message}` |
| final `TEXT_DELTA` | `event: text_delta` `{text}` |
| valid `TOOL_CALL_END` | `event: tool_call_end` `{id,name,args}` |
| `USAGE` | `event: usage` `{input_tokens, output_tokens, cost_usd, cost_is_estimate}` |
| terminal (always) | `event: done` `{run_id, status: <outcome>.value}` |

A fail-closed invocation therefore terminates as `event: error` + `event: done` (status `output_invalid`/`tool_call_rejected`) — **never a raw 500 or a truncated stream** (FR-015, SC-006). No change to `sse.py` or the router is expected; US3 verifies this with a test.

## 4. Persistence contract (`api/run_sink.py`)

`RunRecord.correction_turns` is persisted into the existing `runs.usage` JSONB (no migration). All other persistence unchanged; no raw prompt/response content stored (SC-009 / feature 006 SC-009 preserved).
