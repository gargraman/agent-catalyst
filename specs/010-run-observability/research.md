# Research: Run Observability — Correlation + Run-Status Metrics (010)

Phase 0 decisions. Known patterns over existing plumbing; the work is choosing honest, minimal placements.

## D1 — Correlation precedence & shape validation

- **Decision**: resolve order = W3C `traceparent` trace-id → `X-Correlation-Id` → generated `uuid4().hex`.
  - `traceparent` must match `^[0-9a-f]{2}-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}$`; use the 32-hex trace-id
    group; reject an all-zero trace-id (`00000000000000000000000000000000`, the W3C "invalid" sentinel).
  - `X-Correlation-Id` accepted only if it matches `^[A-Za-z0-9._-]{1,128}$`.
  - Anything else → treated as absent → generated id.
- **Rationale**: `traceparent` is the standard upstream distributed-tracing signal (gateways/meshes emit
  it), so it wins; a plain `X-Correlation-Id` is the simple fallback for callers without W3C tracing.
  Shape-validation keeps an untrusted inbound string from landing unchecked in the DB/logs (P3, FR-004) and
  bounds length/charset. A run ALWAYS gets an id (FR-003) — never empty.
- **Alternatives**: trust the inbound value verbatim — rejected (unbounded/unsafe); support only
  `X-Correlation-Id` — rejected (F13 explicitly names traceparent / W3C trace context).

## D2 — Run-status counter at `run_sink.persist_run_record`

- **Decision**: increment `ac_runs_total{status, source}` inside `persist_run_record` (status = the run
  outcome string, source = `rec.source`), once per recorded run.
- **Rationale**: `persist_run_record` is the single site every recorded run passes through on BOTH paths
  (invoke's stream generator + eval's candidate loop), so one increment there = exactly one per run (SC-004),
  no double counting, no path-specific duplication. It already has the outcome + source in hand.
- **Alternatives**: increment in each handler — rejected (two sites, drift risk, eval fan-out would need its
  own loop); a DB-trigger/materialized counter — rejected (migration + not scrape-native).

## D3 — Refusal counter at the error handler

- **Decision**: in `app_error_handler`, increment `ac_requests_refused_total{code}` for
  `code ∈ {budget.exceeded, access.denied}` (best-effort), before returning the envelope.
- **Rationale**: the handler is the one place EVERY typed `AppError` passes through, so cap-bite (009) and
  RBAC denial (008) rates become visible without touching either feature's code. Named "requests refused by
  a deterministic control" — honest for an admission refusal that never becomes a run (refusals create no
  Run row, so `ac_runs_total` can't see them). Scope limited to the two codes the owner approved (others
  still surface as the error envelope; the counter stays focused).
- **Alternatives**: increment inside `enforce_spend_cap` / `require_permission` — rejected (two sites; the
  403 site is generic across all routes anyway, so the handler is strictly simpler and equivalent).

## D4 — `/metrics` endpoint: unauthenticated, outside `/api/v1`

- **Decision**: `GET /metrics` in `main.py` beside `/healthz` — unauthenticated, not tenant-scoped, returns
  the Prometheus text exposition (`generate_latest` + `CONTENT_TYPE_LATEST`).
- **Rationale**: clarified posture (FR-011). Standard for an in-cluster scrape; network restriction is a
  deploy concern. Aggregate-only output (status/source/code labels) → no tenant/content exposure (SC-005).
  Being non-`/api/v1` means the feature-008 deny-by-default coverage test (which only flags `/api/v1`
  routes) is unaffected — verified no test asserts "`/healthz` is the only non-v1 route".
- **Alternatives**: authenticated scrape token — rejected (non-standard, token-management overhead for
  aggregate counters; FR-011).

## D5 — Best-effort telemetry

- **Decision**: `record_run` / `record_refusal` wrap their body in `try/except Exception: pass` (with a
  debug log). The `/metrics` render is the only place that can surface a metrics error, and it is a
  read-only ops endpoint.
- **Rationale**: FR-009 / P8 — a telemetry fault must never fail a run, its stream, or its persistence. The
  counter increment sits AFTER `session.flush()` in `persist_run_record` so even a (impossible) counter
  error can't roll back the run.
- **Test**: monkeypatch the counter to raise; assert a run still succeeds + persists.

## D6 — Custom `CollectorRegistry`

- **Decision**: a module-level `CollectorRegistry` in `api/metrics.py` (not the global default), with the
  counters registered on it; `/metrics` renders that registry.
- **Rationale**: avoids the global-default "Duplicated timeseries" hazard if the module is imported in odd
  orders under test, and lets tests read exact values via `registry.get_sample_value(name, labels)`.
- **Alternatives**: global default registry — works in prod but noisier under test and mixes in
  process/GC collectors; a custom registry keeps the surface to exactly our two counters.

## D7 — Frontend

- **Decision**: no FE contract change. The runs API already exposes `correlation_id`; `/metrics` is an ops
  endpoint. No `data.ts`/`store.ts` edit needed.
- **Rationale**: correlation + ops metrics are backend/ops concerns; nothing in the mock console consumes
  them. (If a future "copy correlation id" affordance is wanted, it's a separate FE task.)
