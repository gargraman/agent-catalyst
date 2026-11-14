# Contract: 023 — Call-Log Line, Telemetry Setting API, Outbound Correlation, Metrics

## 1. Per-call log line (stdout, logger `ac.call_log`)

One JSON object per line, one line per model call. Consumers: the platform log pipeline. **Not** a source of truth —
the billable record is the `runs` row.

### Always present

| Key | Type | Example / rule |
|---|---|---|
| event | string | `"model_call"` |
| ts | string | RFC 3339 UTC |
| run_id, correlation_id, workspace_id, agent_id, version_id | string | ids; `agent_id/version_id = "judge"` for judge calls |
| provider_correlation_id | string | 32 hex; the id sent to the provider (§3); == correlation_id when the platform generated it |
| source | string | `playground` \| `api` \| `eval` |
| role | string | `candidate` \| `judge` |
| call_seq | int | 0-based |
| correction | bool | |
| provider, model, prompt_version | string | A2: model + prompt version |
| latency_ms | int | A2: latency |
| first_token_ms | int \| null | |
| cost_usd | string | A2: cost — `str(Decimal)`, equal to the value summed into `runs.cost_usd` |
| cost_known | bool | |
| success | bool | A2: success/fail |
| error_code | string \| null | normalized code only |
| provider_request_id | string \| null | provider's response id when exposed |
| input_chars, output_chars | int | A2: input/output — size |
| input_fp, output_fp | string | A2: input/output — keyed per-workspace fingerprint, 32 hex (empty for empty text) |

### Present only when content logging is effective (workspace opt-in or dev profile)

| Key | Type | Rule |
|---|---|---|
| input_text, output_text | string | redacted (`[REDACTED:<kind>]` substitutions), then truncated to 2,048 chars; `"[redaction-failed]"` on redactor error |

### Guarantees

- Never contains the run's provider api key or any other known credential (exact-match scrub, even with content off —
  content-off lines carry no text at all).
- Emission failure never fails/alters the run; counted in `ac_call_log_errors_total{stage}`.

## 2. Telemetry setting API

### `GET /api/v1/workspace/telemetry`

- Auth: console session / dev principal; `X-Workspace-Id`; permission `view`.
- 200 → `{"content_logging": false, "effective": false}` — `effective` reflects the dev-profile override.

### `PUT /api/v1/workspace/telemetry`

- Permission `settings.manage` (admin only); `rate_limit("default")`; CSRF applies to cookie sessions (019).
- Body: `{"content_logging": true}` (strict; unknown keys → 422, FastAPI request-validation — the repo-wide convention for body-schema errors).
- 200 → `{"content_logging": true, "effective": true}`.
- 403 `access.denied` for non-admins (before any write).
- Side effect when the value changes: audit log line
  `{"event":"telemetry.content_logging.changed","subject":"…","workspace_id":"…","old":false,"new":true,
  "correlation_id":"…","ts":"…"}`. No-op PUT (same value) → 200, no audit line.

## 3. Outbound correlation (provider requests)

- Every provider request made for a run carries `X-Correlation-Id: <provider correlation id>` — a **platform-generated**
  32-lowercase-hex id (FR-014). A caller-supplied `X-Correlation-Id` / `traceparent` value is **never** forwarded.
  - No valid inbound id → the edge-generated correlation id is reused (provider id == correlation id).
  - Valid inbound id → a fresh `uuid4().hex` is minted per inbound request (shared by every call of that request,
    incl. all eval case + judge calls).
- The transport re-checks `^[0-9a-f]{32}$` and omits the header (never errors) on mismatch — defense in depth.
- Lookup: caller id → `runs.correlation_id` / call lines (`correlation_id`) → `provider_correlation_id` → provider logs.
- Provider-native client request-id headers: **not sent** (unverified against provider docs — research D7).
- Response request id: captured from litellm's `_hidden_params["additional_headers"]` into the call line's
  `provider_request_id` (bounded `^[A-Za-z0-9._:-]{1,128}$`, else null).

| Provider | Generic header sent | Provider-native client id | Response request id captured |
|---|---|---|---|
| openai | yes (wire-verified, T002) | not sent | yes — `x-request-id` (wire-verified, T002) |
| anthropic | yes (wire-verified, T002) | not sent | yes — `request-id` (wire-verified, T002) |
| ollama (local) | yes (ignored by server) | n/a | n/a (none returned) |

## 4. Metrics (`GET /metrics`, existing unauthenticated aggregate surface)

| Name | Type | Labels | Semantics |
|---|---|---|---|
| ac_first_token_seconds | histogram | source | first streamed token latency per run (pass-through runs) |
| ac_run_latency_seconds | histogram | source | end-to-end run latency per persisted run |
| ac_deadline_exceeded_total | counter | source | +1 per run with outcome `timeout`; equals `ac_runs_total{status="timeout"}` per source |
| ac_call_log_errors_total | counter | stage | call-log emission failures (`fingerprint`\|`redact`\|`serialize`\|`write`) |

No tenant, user, agent, or content labels.
