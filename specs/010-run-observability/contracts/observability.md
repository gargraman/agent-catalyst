# Contract: Run Observability — Correlation + Metrics (010)

## 1. Correlation (invoke + eval-run)

**Request headers (optional, in precedence order):**
- `traceparent: 00-<32-hex trace-id>-<16-hex span-id>-<2-hex flags>` — the 32-hex trace-id is used
  (unless all-zero).
- `X-Correlation-Id: <token>` — used when no valid traceparent; must match `^[A-Za-z0-9._-]{1,128}$`.

**Behavior:**
- The resolved id becomes the run's `correlation_id` (visible later via `GET /api/v1/runs[/:id]`).
- When neither header is present/valid, the server generates one (unchanged from today).
- A malformed inbound value is ignored (never stored unchecked).

**Response header (invoke + eval):**
- `X-Correlation-Id: <resolved id>` — always present, so the caller can record it.

Applies identically to `POST /api/v1/agents/{id}/invoke` (SSE) and `POST /api/v1/agents/{id}/eval-runs`.

## 2. Metrics endpoint

`GET /metrics` — **unauthenticated**, not under `/api/v1`, Prometheus text exposition
(`Content-Type: text/plain; version=1.0.0; charset=utf-8`).

Counters:

```
# TYPE ac_runs_total counter
ac_runs_total{status="success",source="playground"}  <n>
ac_runs_total{status="timeout",source="playground"}  <n>
ac_runs_total{status="success",source="eval"}        <n>
# TYPE ac_requests_refused_total counter
ac_requests_refused_total{code="budget.exceeded"}    <n>
ac_requests_refused_total{code="access.denied"}      <n>
```

- `ac_runs_total{status,source}`: +1 per recorded run (status = run outcome; source = playground|eval).
- `ac_requests_refused_total{code}`: +1 per admission refusal with code `budget.exceeded` (009) or
  `access.denied` (008) — refusals that never become a run.
- Output contains **no** tenant identifiers tied to content, **no** prompt/response content, **no** secrets.

## 3. Best-effort guarantee

A failure in the metrics path never changes run behavior: the run still streams and is still persisted;
only the (missing) counter increment is lost. The `/metrics` read is the sole place a metrics error can
surface, and it is a read-only ops endpoint.

## 4. Frontend

No contract change. `correlation_id` is already exposed by the runs API; `/metrics` is an ops endpoint not
consumed by the console.
