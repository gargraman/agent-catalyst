# Data Model: Runtime Invoke Spine (M2a)

**Feature**: `003-runtime-invoke-spine` · **Date**: 2026-11-04

All types are **pure value objects** (frozen dataclasses) or protocols — no ORM, no DB, no HTTP (P6).
Existing types (`VersionSnapshot`, `NormalizedRequest`, `NormalizedStreamEvent`, `NormalizedUsage`) are
reused unchanged from M0/M1.

---

## RunContext (new — `runtime/context.py`)

The per-invocation envelope the caller supplies. Carried through the whole pipeline.

| Field | Type | Notes |
|---|---|---|
| `workspace_id` | `str` | tenant the run belongs to; copied onto the `RunRecord` (P5). MUST be non-empty — fail closed. |
| `source` | `str` | caller/source: `playground` \| `api` \| `eval` \| `channel` \| `tool_test` \| `subagent` (mirrors the frontend `Run.source`). |
| `caller` | `str \| None` | principal/service identity, when known (for the `RunRecord`). |
| `correlation_id` | `str` | trace/correlation id. If absent, the runtime **mints one** (no un-traceable run — FR-012/SC-007). |
| `deadline_s` | `float` | wall-clock budget; default **30.0**. Enforced at the runtime (research D5). |
| `max_tool_calls` | `int` | default **8**. Carried as a stable field; **not loop-enforced in M2a** (research D6). |
| `api_key` | `str` | provider credential, from config/env (vault deferred). Passed to the gateway; scrubbed from any error text by M1. |
| `record_run` | `RunSink` | sink invoked exactly once at completion with the finished `RunRecord` (research D3). |

### RunSink (protocol)

`Callable[[RunRecord], Awaitable[None] | None]` — the runtime calls it once per invocation. M2a ships an
in-memory test sink; the DB-backed, RLS-scoped sink is M4. The runtime never persists directly.

---

## Outcome (new — `runtime/run.py`)

Closed enum of invocation outcomes — the honest-status taxonomy (P8). Exactly one applies per run.

| Value | Meaning | Provider call made? |
|---|---|---|
| `success` | stream completed with a terminal stop (content may be empty) | yes |
| `invalid_input` | inputs failed the snapshot input contract | **no** |
| `context_overflow` | assembled request exceeds the model context budget | **no** |
| `config_error` | snapshot names an unknown/retired model (gateway fails closed) | no (or none) |
| `provider_error` | gateway surfaced a normalized provider failure | attempted |
| `timeout` | deadline reached before terminal stop | attempted (partial) |
| `bounded_exit` | a carried bound stopped the run (reserved; tool-call loop — later) | attempted |
| `degraded` | stream ended with no usage report (gateway degraded ERROR) | yes |

---

## RunRecord (new — `runtime/run.py`)

The complete observability record produced for **every** invocation. Superset of the frontend `Run`
display type; the M4 sink maps it to the stored/returned shape.

| Field | Type | Source | Notes |
|---|---|---|---|
| `run_id` | `str` | minted | unique per invocation. |
| `correlation_id` | `str` | ctx | always present. |
| `workspace_id` | `str` | ctx/snapshot | for RLS storage downstream (P5). |
| `agent_id` / `version_id` / `version_label` | `str` | snapshot | identity. |
| `model` / `provider` | `str` | snapshot.model_config | identity. |
| `profile_hash` | `str` | snapshot.profile_snapshot | pricing/capability identity (drift). |
| `prompt_hash` | `str` | snapshot.prompt_hash | behavior identity. |
| `snapshot_hash` | `str` | snapshot | full content identity. |
| `source` / `caller` | `str` / `str\|None` | ctx | who invoked. |
| `usage` | `NormalizedUsage \| None` | gateway USAGE | absent on pre-call rejections; best-effort on timeout/degraded. |
| `cost_usd` | `Decimal \| None` | gateway | **carried from the gateway, never recomputed** (FR-014/P7); `None` when no usage. |
| `cost_is_estimate` | `bool` | gateway | provisional-profile flag carried through. |
| `substitutions` | `tuple[Substitution, ...]` | gateway | M1 ledger carried through unchanged (FR-017). |
| `latency_ms` | `int` | measured | wall-clock of the invocation. |
| `outcome` | `Outcome` | pipeline | the honest status (above). |
| `stop_reason` | `str \| None` | gateway STOP | terminal reason (natural/length/tool_use/content_filter…). |
| `error` | `dict \| None` | gateway ERROR | normalized, api_key-scrubbed failure detail (P8). |

---

## InvokeResult (new — `runtime/run.py`)

Returned by `invoke_result()` (the collector) for non-streaming callers.

| Field | Type | Notes |
|---|---|---|
| `text` | `str` | accumulated content (may be empty). |
| `tool_calls` | `tuple[dict, ...]` | surfaced tool-call requests (not executed — M2a). |
| `reasoning` | `str \| None` | accumulated reasoning deltas, if any. |
| `stop_reason` | `str \| None` | terminal stop. |
| `run` | `RunRecord` | the full record (same instance handed to the sink). |

---

## NormalizedRequest assembly (new — `runtime/io.py build_normalized_request`)

Mapping from the immutable snapshot + caller inputs to the M1 `NormalizedRequest`:

| NormalizedRequest field | Source |
|---|---|
| `provider`, `model`, `api_version` | `snapshot.model_config` |
| `system` | `snapshot.system_prompt` |
| `messages` | caller `inputs` (the turn(s)/variables, post-validation) |
| `params` | `snapshot.model_config` params (temperature, max_tokens, top_p…) |
| `reasoning` | `snapshot.model_config` reasoning (effort/budget), if present |
| `tools` | `snapshot.tools[*].schema_snapshot` |
| `tool_choice` | `snapshot.model_config` tool_choice, if present |
| `response_format` | derived from `snapshot.io_contract.output_format == "json"` (+ output_schema) |
| `cache_hints` | `snapshot` context/cache config, if present |

**Validation rules**
- `validate_input(inputs, io_contract)`: inputs MUST satisfy `io_contract.input_schema` (Draft 2020-12,
  reusing `providers.validation._schema_errors`); on failure → `invalid_input`, **no** gateway call.
- `budget_check`: `count_tokens(...) ≤ profile.limits.context_tokens`; on failure → `context_overflow`,
  **no** gateway call. Estimate MUST over-count (margin) so a real overflow is never let through (D4).

---

## Reused unchanged (M0/M1)

- `VersionSnapshot` (+ `ToolRef`/`RetrievalRef`/`SkillRef`) — `runtime/snapshot.py`. Read-only here.
- `NormalizedRequest`, `NormalizedStreamEvent`, `EventType`, `NormalizedUsage`, `Substitution` —
  `runtime/providers/ir.py` / `profiles.py`.
- `gateway.stream(req, *, api_key, profile)` — the single execution call (`runtime/providers/gateway.py`).
- `resolve_profile`, `compute_cost`/`quantize_usd` — consumed via the gateway (not re-called for cost).
