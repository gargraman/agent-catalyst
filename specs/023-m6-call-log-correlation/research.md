# Research: Per-Call Model Log + End-to-End Correlation + Latency SLIs (023)

All Technical Context unknowns are resolved below. Format: Decision / Rationale / Alternatives considered.

## D1 — Emission seam: a per-call sink on `RunContext` (runtime stays pure)

- **Decision**: Add an optional `record_call: CallSink | None` to `runtime.context.RunContext`, a sibling of the
  existing `record_run` sink. The runtime builds a frozen, pure `ModelCallRecord` value object per model turn and
  hands it to `ctx.record_call`. The API layer supplies the sink (`api/call_log.py`), which fingerprints, optionally
  redacts, serializes, writes, and counts. The runtime never imports logging handlers, metrics, or settings.
- **Rationale**: Mirrors the established `record_run` pattern; keeps P6 runtime purity (the purity test bans
  fastapi/starlette/sqlalchemy/api/boto3, and the runtime has never owned I/O). All four callers already pass a
  `RunContext` into the one shared spine (`_pipeline`), and the judge reuses the eval ctx, so one seam covers
  playground, published, eval case, and judge (FR-004) with zero caller-specific emission.
- **Alternatives**: (a) log directly from `runtime/obs.py` with stdlib logging (the design doc's sketch) — rejected:
  couples the pure library to sink/redaction-policy/settings and makes the per-workspace opt-in (Q1=A) impossible
  without passing tenant settings into the runtime; (b) emit from each router — rejected: four emission sites, shape
  drift, violates FR-004.

## D2 — Emission point and failure paths

- **Decision**: Emit one `ModelCallRecord` per gateway turn inside `_pipeline`, in both pass-through (streaming) and
  validated/correction modes, using a `try/finally` around each turn so an interrupted turn (deadline cut, provider
  error, client disconnect → generator close) still emits with its partial latency/cost and `success=false`.
  `ctx.record_call` is invoked inside its own `try/except` in the runtime, so a sink exception can never alter the run
  (FR-009). Pre-call rejections (invalid input, context overflow, config error, budget/authz refusals upstream) make
  no provider call and therefore emit no per-call record (spec edge case).
- **Rationale**: A turn is exactly one provider request, which is the A2 unit ("every model call"). The finally block
  is the only place that sees disconnects (the existing `invoke()` already closes the pipeline in a finally — review F4).
- **Alternatives**: emit once per run with a list of calls — rejected (not "one line per call"; a crash mid-run loses
  all calls).

## D3 — Single cost number (FR-005)

- **Decision**: `ModelCallRecord.cost_usd` is the turn's `usage.cost_usd` Decimal exactly as the gateway attached it
  (already quantized by `compute_cost/quantize_usd`); the run's cost is the existing `_sum_usage` Decimal sum of the
  same values. Serialized as a string (`str(Decimal)`) in the log line. A turn that produced no USAGE (degraded /
  interrupted before usage) records cost `0` with `cost_known=false`, matching how the run treats it.
- **Rationale**: One computation point, no sink re-derivation; Decimal addition of quantized values is exact, so
  `sum(per-call) == run.cost_usd` holds by construction — asserted by an invariant test across single-turn,
  correction-turn, and failed-turn runs.
- **Alternatives**: recompute cost in the sink from tokens — rejected (P7 "never re-derive"; drift risk).

## D4 — Content fingerprint (FR-002c)

- **Decision**: `fp = HMAC-SHA256(k_ws, text)` truncated to 32 hex chars, where
  `k_ws = HMAC-SHA256(AC_TELEMETRY_FINGERPRINT_KEY, workspace_id)`. The root key is a new `SecretStr` setting,
  **required under `AC_ENV=prod`** (settings validator fails closed at load, same pattern as the session secret) with a
  fixed dev default otherwise. Empty text → empty fingerprint.
- **Rationale**: Keyed → short/guessable prompts can't be brute-forced from the log (a plain hash of "yes" is
  trivially reversible). Per-workspace derivation → identical text in two tenants yields different fingerprints, so
  the log can't be used to correlate content across tenants (P5), while matching *within* a workspace still works.
- **Alternatives**: plain SHA-256 — rejected (dictionary-reversible); random salt per line — rejected (defeats
  "same prompt as before" matching); per-workspace stored keys — rejected (key management for no added value now).

## D5 — Shared redaction primitive (FR-006/007/008/010)

- **Decision**: New pure module `runtime/guardrails/redact.py` exposing `redact(text, *, known_secrets=()) -> str`,
  the location the guardrails LLD already names, so the future output guardrail imports the same function.
  Order: (1) exact-match scrub of every known credential for the run (the resolved provider `api_key` is passed by the
  sink — the strongest FR-008 guarantee, independent of pattern coverage); (2) credential-shape patterns: `sk-…`,
  `sk-ant-…`, AWS `AKIA/ASIA…` ids, GitHub `gh[pousr]_…`, Slack `xox[abprs]-…`, JWTs (three base64url segments),
  PEM private-key blocks, `Bearer <token>`; (3) PII patterns: email, phone (E.164 / common national forms), payment
  card numbers (Luhn-checked), US SSN shape. Each hit → `[REDACTED:<kind>]`. Truncation to 2,048 chars happens
  **after** redaction (so a secret split by the cut can't leak). Any exception → caller writes the fixed marker
  `[redaction-failed]` (fail closed, FR-007).
- **Rationale**: No redaction primitive exists (guardrails unbuilt). Exact-match on the run's own key closes the
  highest-severity leak deterministically; patterns cover the rest. Minimal, testable, extensible.
- **Alternatives**: a third-party PII library (e.g. Presidio) — rejected for this slice (heavy dependency, NLP
  models, non-deterministic); entropy-based detection — deferred (false-positive heavy; candidate for guardrails).
- **Honest limit**: pattern redaction is not complete PII detection (names, addresses are not caught). This is why
  Q1=A keeps text **off by default**; the opt-in carries this documented residual risk.

## D6 — Content-logging opt-in: storage, admin surface, audit (FR-002a/b)

- **Decision**: New column `workspace_settings.content_logging boolean NOT NULL DEFAULT false` (migration **0012**);
  a missing settings row reads as `false` (settings rows are created lazily — existing 006 behavior). New endpoints
  `GET /api/v1/workspace/telemetry` (`view`) and `PUT /api/v1/workspace/telemetry` (`settings.manage` = admin-only per
  `rbac_permissions.json`; `rate_limit("default")`), body `{content_logging: bool}`. Every PUT that changes the value
  emits a structured **audit log line** (`event=telemetry.content_logging.changed`, subject, workspace, old→new,
  timestamp, correlation id). Effective flag per run = `workspace.content_logging OR (AC_ENV == "dev" AND
  AC_TELEMETRY_LOG_CONTENT_IN_DEV)` (dev default `true`; tests set it explicitly). **Exactly `dev`** (analysis S1): a
  shared non-prod profile such as staging may hold real data and must require the audited opt-in like prod.
- **Rationale**: A dedicated endpoint keeps the permission and audit hook narrow (the general settings PATCH requires
  ETag choreography and carries unrelated fields). Structured audit line matches the project's current audit
  practice (018 token revocation; durable `audit_logs` table is deferred project-wide — not introduced here).
- **Alternatives**: store inside `limits` JSONB — rejected (semantic abuse, no audit seam); env-only global switch —
  rejected (not per-workspace, can't be changed by a workspace admin).

## D7 — Outbound correlation (FR-012/014)

- **Decision (revised after analysis U1)**: only a **platform-generated** id goes to providers. The edge resolves the
  correlation id as in 010 and also whether it was caller-supplied; the provider correlation id is the edge-generated
  id when nothing valid was supplied, else a fresh `uuid4().hex` per inbound request. It rides
  `RunContext.provider_correlation_id → NormalizedRequest.provider_correlation_id` (not hashed) →
  `stream_via_transport(extra_headers={"X-Correlation-Id": id})` (litellm forwards `extra_headers` to OpenAI-compatible
  and Anthropic HTTP clients). The transport accepts only `^[0-9a-f]{32}$` and drops (never errors) anything else.
  Both ids are on every call line, so one lookup still spans request → run → calls → provider.
- **Why not forward the caller id (original design)**: a caller-chosen value is untrusted free text (128 chars) that
  could carry tenant/user data to a third party, violating FR-014 and P5 external-processing minimization. Shape
  validation bounds it but cannot prove it carries no information; generation can.
- **Uncertainty (explicit)**: Providers generally *ignore* unknown request headers, so a generic header is safe but
  may not appear in the provider's own logs. Some providers document a client-supplied request-id header (I believe
  OpenAI accepts `X-Client-Request-Id`; not verified here). **Verify against current provider docs in a spike task
  before relying on it**; if confirmed, send it too for that provider. Additionally (SHOULD), capture the provider's
  **response** request id (e.g. `request-id` / `x-request-id`) into the call record as `provider_request_id` when
  litellm exposes response headers — feasibility also verified in the spike; absent → field empty, not an error.
- **Spike result (T002, 2026-11-14, litellm 1.106.6, local fake HTTP server — no provider/paid call)**:
  - `extra_headers={"X-Correlation-Id": …}` passed to `litellm.acompletion` **is sent on the wire** for both
    `openai/…` (`POST /v1/chat/completions`) and `anthropic/…` (`POST /v1/messages`).
  - The provider **response** request id is exposed on every streamed chunk at
    `chunk._hidden_params["additional_headers"]`: `llm_provider-x-request-id` (OpenAI `x-request-id`) and
    `llm_provider-request-id` (Anthropic `request-id`). The transport reads it (first non-empty wins, preference
    `llm_provider-x-request-id` → `llm_provider-request-id`), bounds it to `^[A-Za-z0-9._:-]{1,128}$`, and carries
    it on the terminal USAGE event as `provider_request_id`.
  - Provider-native *client* request-id headers (e.g. OpenAI `X-Client-Request-Id`) were **not** verified against
    current provider docs and are **not sent** in this slice — the generic header plus the captured response id
    already make each provider request identifiable from our records (FR-012 best-effort). Revisit with docs.
  - Limitation: real provider behavior (whether they log/echo an unknown header) cannot be proven without a live
    call; the spike proves only that our side sends it.
- **Rationale**: Satisfies SC-003 ("identify its outbound provider requests") in the strongest available way:
  our id outbound where supported, and the provider's id inbound into our record regardless.
- **Alternatives**: W3C `traceparent` outbound — deferred to the Langfuse/OTel slice (no tracer exists to own span ids).

## D8 — SLI metrics (FR-015..018)

- **Decision**: Extend `api/metrics.py` (existing custom registry):
  `ac_first_token_seconds{source}` (Histogram; buckets 0.25,0.5,1,1.5,2,3,5,8,13,30),
  `ac_run_latency_seconds{source}` (Histogram; buckets 0.5,1,2,5,10,15,20,25,30,45),
  `ac_deadline_exceeded_total{source}` (Counter),
  `ac_call_log_errors_total{stage}` (Counter; stage ∈ fingerprint|redact|serialize|write).
  Observed in the existing single site `metrics.record_run(...)` (called once per persisted run from `run_sink`),
  now passed the `RunRecord`: deadline counter increments iff `outcome == timeout` (the deadline outcome), so it can
  never diverge from `ac_runs_total{status="timeout"}`. `RunRecord` gains `first_token_ms: int | None` (set in
  pass-through mode at the first TEXT_DELTA of the first turn; `None` in validated mode, which has no streamed first
  token). Labels: `source` only (low cardinality, no tenant — FR-018).
- **Rationale**: Reuses the one exactly-once site 010 established; histogram buckets bracket the 2 s first-token SLO
  and the 30 s deadline.
- **Note**: judge runs are not persisted (eval router filters them, F1), so they are not in run metrics — unchanged and
  correct (a judge call is a scoring detail); they **are** in the per-call log (D10).

## D9 — Log sink mechanics (FR-019)

- **Decision**: A dedicated stdlib logger `ac.call_log` with its own `StreamHandler(sys.stdout)`, a raw-message
  formatter (the message is the JSON object), level INFO, `propagate=False`, configured once on import of
  `api/call_log.py`. One `json.dumps(..., separators=(",", ":"), ensure_ascii=False)` line per call.
- **Rationale**: Uvicorn's default logging config doesn't guarantee INFO for app loggers; an explicit handler makes
  emission deterministic and keeps the line free of prefixes so the log pipeline parses it as JSON.
- **Alternatives**: structlog — rejected (new dependency for one line type).

## D10 — Caller/role labeling

- **Decision**: `ModelCallRecord.source` = `ctx.source` (playground | api | eval); `role` = `"judge"` when the
  snapshot is the judge (`version_id == "judge"`, the same discriminator the eval router uses), else `"candidate"`.
  Plus `call_seq` (0-based turn index within the run) and `correction` (bool, turn > 0 in validated mode).
- **Rationale**: Distinguishes judge calls (spec FR-003 lists judge) without changing the shared eval ctx or the
  router's persistence filter.

## D11 — Performance (SC-007 ≤ 5% median overhead)

- **Decision**: Per call: one HMAC over input and output (µs/KB), one JSON dump, one logger write. Redaction runs only
  when content logging is on. Verified by a micro-benchmark test against the stub provider (median of N runs with vs.
  without sink) with a generous CI tolerance; the hard number is checked in quickstart, not as a flaky CI gate.

## D12 — Docs reconciliation (Q1 = A)

- **Decision**: In this slice, update `docs/backend/10-observability-monitoring.md` §1 and
  `docs/backend/lld/10-observability-monitoring.lld.md` §3/§5 (metadata-by-default; opt-in text; sink seam), and add a
  `docs/scope.md` decision-log row interpreting A2 as "identifiable by default, readable on opt-in".
