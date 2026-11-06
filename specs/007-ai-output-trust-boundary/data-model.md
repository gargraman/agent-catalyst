# Data Model — AI-Output Trust Boundary (M2b)

Pure runtime value types (P6). No DB schema change (persistence folds into the existing `runs.usage` JSONB). All deltas are additive and backward-compatible.

## Modified value types

### `Outcome` (enum) — `runtime/run.py`
Add two fail-closed members; preserve all existing members and their meanings.

| New member | Value | Meaning |
|---|---|---|
| `OUTPUT_INVALID` | `"output_invalid"` | json-mode output could not be made schema-valid within the correction budget |
| `TOOL_CALL_REJECTED` | `"tool_call_rejected"` | a model tool call could not be made valid (unbound name / bad args) within the budget |

`BOUNDED_EXIT` remains **reserved and unused** by this feature.

### `RunRecord` (frozen dataclass) — `runtime/run.py`
Add one field (defaulted ⇒ existing construction stays valid):

| Field | Type | Default | Notes |
|---|---|---|---|
| `correction_turns` | `int` | `0` | count of extra model turns spent on self-correction (0 for a clean invocation) |

`usage`/`cost_usd` semantics unchanged but now carry **summed** values across all turns (D5).

### `RunContext` (dataclass) — `runtime/context.py`
Add one field; **do not** touch `max_tool_calls` (reserved for the P2 tool loop):

| Field | Type | Default | Notes |
|---|---|---|---|
| `max_corrections` | `int` | `2` | dedicated per-invocation correction budget (FR-010); caller-overridable |

## Reused unchanged

- `validate_output(raw_text, *, output_format, output_schema) -> OutputValid | RepairNeeded` — F1/F2.
- `validate_tool_call(name, args, tool_schemas) -> ToolCallOk | ToolCallRejected` — F3.
- `OutputValid{text, structured}`, `RepairNeeded{reason, detail, errors}`, `ToolCallOk{name,args}`, `ToolCallRejected{reason, detail, available, errors}`.

## New pure helpers — `runtime/io.py`

### `tool_schemas_from_snapshot(snapshot) -> dict[str, dict]`
Build the `{tool_name: args_schema}` map `validate_tool_call` expects, from `snapshot.tools` (D4):
- for each `ToolRef`, `d = toolref.schema_snapshot`; `name = d.get("name")`; `args_schema = d.get("input_schema") or d.get("parameters") or {}`.
- include only entries where `name` is a non-empty string.

### `build_correction_messages(messages, turn, defects) -> list[dict]`
Return a **new** messages list (original + appended correction turns) using plain normalized roles (D3). One assistant message echoing the failed attempt + one user message stating the specific validation error(s); for tool rejection, include the available tool names. Pure; no provider-native tool-result messages.

## Accumulator (internal to `_pipeline`) — not a persisted type

A running total updated per turn (D5): summed token counts + `cost_usd`, accumulated `substitutions`, sticky `cost_is_estimate`, and `correction_turns`. Used to build the single final `RunRecord`.

## State transitions (one invocation, buffered validated mode)

```
          ┌────────────────────── valid (no defects) ─────────────────────┐
          │                                                                ▼
start → run turn → validate ─ defect & budget>0 → build correction msgs → run turn  (loop)
          │                   │
          │                   └ defect & budget==0 → FAIL CLOSED (output_invalid | tool_call_rejected)
          └ turn error (provider/timeout/degraded) → FAIL CLOSED (existing outcome)
```

Every path emits **exactly one** `RunRecord` (P8) with summed usage + `correction_turns`, and (in streaming mode) only the final validated events or a single typed `ERROR` reach the caller (FR-015a).

## Persistence (no migration) — `api/run_sink.py`

Map `RunRecord.correction_turns` into the stored `runs.usage` JSONB (e.g. add a `"correction_turns"` key alongside the token metrics). No new column; `runs` schema from migration 0005 is unchanged.
