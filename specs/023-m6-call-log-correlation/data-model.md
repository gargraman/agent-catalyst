# Data Model: 023 — Per-Call Model Log + Correlation + Latency SLIs

## ModelCallRecord (new, runtime value object — `runtime/run.py`)

Frozen, pure; produced by the runtime once per model turn and handed to `RunContext.record_call` (never persisted to
Postgres). Carries raw text **in-process only**; the API sink decides what leaves the process (see CallLogLine).

| Field | Type | Notes |
|---|---|---|
| run_id | str | same as the run's RunRecord |
| correlation_id | str | from `ctx.correlation_id` (edge-resolved, 010; may be caller-supplied — never sent outbound) |
| provider_correlation_id | str | platform-generated 32-hex id sent to the provider (FR-014); == correlation_id when the platform generated that |
| workspace_id | str | tenant |
| agent_id / version_id | str | from snapshot (`"judge"` for judge calls) |
| source | str | `ctx.source`: playground \| api \| eval |
| role | str | `"judge"` if `version_id == "judge"` else `"candidate"` (D10) |
| call_seq | int | 0-based turn index within the run |
| correction | bool | true for validated-mode correction turns |
| provider / model | str | resolved provider + model |
| prompt_version | str | snapshot `prompt_hash` |
| input_text / output_text | str | raw, in-process only (never serialized by default). `input_text` = the conversation messages sent in that turn, incl. repair messages on correction turns; **excludes the system prompt** (identified by `prompt_version`) — analysis U2 |
| latency_ms | int | turn wall time (partial on interruption) |
| first_token_ms | int \| None | first TEXT_DELTA of this turn (pass-through only) |
| cost_usd | Decimal | the gateway-attached turn cost; `0` when no usage |
| cost_known | bool | false when the turn ended without a USAGE report |
| success | bool | true iff the turn reached a terminal stop with usage and no error |
| error_code | str \| None | normalized error code (no raw provider payload) |
| provider_request_id | str \| None | provider's response request id when exposed (D7, SHOULD) |

**Invariant (FR-005)**: for every run, `sum(r.cost_usd for r in calls) == RunRecord.cost_usd` (Decimal-exact).

## RunRecord (existing — extended)

| Field | Change |
|---|---|
| first_token_ms | **new** `int \| None` — first TEXT_DELTA of the first turn in pass-through mode; `None` in validated mode / no text |

No other change; `cost_usd` remains the sum of per-turn costs.

## RunContext (existing — extended)

| Field | Change |
|---|---|
| record_call | **new** `CallSink \| None = None` — `Callable[[ModelCallRecord], None \| Awaitable[None]]`; invoked once per model turn inside try/except (sink failure never affects the run) |
| content_logging | **new** `bool = False` — set by the API from the effective flag (D6); carried for the sink, unused by runtime logic |
| provider_correlation_id | **new** `str \| None = None` — set by the API edge (platform-generated); when `None` the runtime reuses its own minted correlation id if it minted one, else mints a fresh `uuid4().hex` — a caller value is never copied here |

## NormalizedRequest (existing — extended)

| Field | Change |
|---|---|
| provider_correlation_id | **new** `str \| None = None` — forwarded as `X-Correlation-Id` outbound only if it matches `^[0-9a-f]{32}$` (D7, FR-014); not part of any hash |

## CallLogLine (new, serialized projection — `api/call_log.py`)

One JSON object per line on stdout (logger `ac.call_log`). Default (content logging OFF):

```text
{ event:"model_call", ts, run_id, correlation_id, provider_correlation_id, workspace_id, agent_id, version_id, source, role, call_seq,
  correction, provider, model, prompt_version, latency_ms, first_token_ms, cost_usd:"<decimal string>", cost_known,
  success, error_code, provider_request_id,
  input_chars, output_chars, input_fp, output_fp }
```

When content logging is ON for the workspace (or dev profile), additionally: `input_text`, `output_text` — each
redacted (D5), then truncated to 2,048 chars; a redaction exception → the literal `"[redaction-failed]"`.

Validation rules: no field ever contains the run's provider api key (exact-match scrub + test); `cost_usd` is
`str(Decimal)` of the record value; fingerprints are 32 hex chars or empty.

## WorkspaceSettings (existing table `workspace_settings` — extended, migration 0012)

| Column | Type | Notes |
|---|---|---|
| content_logging | boolean NOT NULL DEFAULT false | per-workspace opt-in for redacted text in call-log lines; missing row ⇒ false |

RLS: unchanged (table already WorkspaceScoped). Write path: admin-only endpoint (D6). Change audit: structured log
line `telemetry.content_logging.changed` {subject, workspace_id, old, new, correlation_id, ts}.

## Metrics (existing registry — extended, `api/metrics.py`)

| Metric | Type | Labels | Observed at |
|---|---|---|---|
| ac_first_token_seconds | Histogram | source | `record_run` when `first_token_ms` is set |
| ac_run_latency_seconds | Histogram | source | `record_run` (every persisted run) |
| ac_deadline_exceeded_total | Counter | source | `record_run` iff outcome == `timeout` |
| ac_call_log_errors_total | Counter | stage | call-log sink failures (fingerprint/redact/serialize/write) |

## State / flow

```text
edge (010) resolves correlation_id (+ supplied?) ─▶ provider_correlation_id = generated ? correlation_id : uuid4().hex
   ─▶ RunContext(correlation_id, provider_correlation_id, record_call=sink, content_logging=flag)
   _pipeline turn N:  NormalizedRequest(provider_correlation_id) ─▶ gateway ─▶ litellm(extra_headers X-Correlation-Id)
                      finally: ModelCallRecord ─▶ ctx.record_call (try/except)
                                                   └▶ api.call_log: fingerprint · [redact+truncate if flag] · JSON line · errors counter
   _finish: RunRecord(first_token_ms, cost=Σ turns) ─▶ record_run ─▶ run_sink ─▶ runs row + metrics.record_run(rec)
```
