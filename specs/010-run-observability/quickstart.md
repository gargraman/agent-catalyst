# Quickstart / Validation: Run Observability (010)

**Prereqs**: compose Postgres up (5433, healthy); `cd services`; venv at `services/.venv` (now has
`prometheus-client`). Run: `.venv/bin/python -m pytest`. Gateway mocked — no live/paid calls.

## TDD order (RED shown before GREEN)

1. **Pure correlation (RED→GREEN)** — `tests/test_correlation.py`:
   - valid `traceparent` → its 32-hex trace-id; all-zero trace-id → ignored.
   - no traceparent + valid `X-Correlation-Id` → that value; malformed `X-Correlation-Id` → ignored.
   - neither → a generated 32-hex id. Precedence: traceparent beats X-Correlation-Id. RED: `api.correlation`
     absent.

2. **Pure metrics (RED→GREEN)** — `tests/test_metrics_unit.py`:
   - `record_run("success","playground")` then `get_sample_value("ac_runs_total",{status,source})` == 1.
   - `record_refusal("budget.exceeded")` increments `ac_requests_refused_total{code}`.
   - both are best-effort: monkeypatch the counter to raise → helper does NOT raise.
   - `render()` returns the Prometheus content-type. RED: `api.metrics` absent.

3. **API correlation behavioral RED** — `tests/test_api_correlation.py` (ASGI, compose DB, gateway mocked):
   - invoke with `X-Correlation-Id: my-corr-123` → the recorded run's `correlation_id == "my-corr-123"`
     AND the response carries `X-Correlation-Id: my-corr-123`. RED now = server overwrites with a uuid.
   - invoke with a valid `traceparent` → run correlation == the trace-id.
   - invoke with no header → run gets a generated id, echoed on the response.
   - invoke with malformed `X-Correlation-Id` (e.g. `"bad id!!"`) → generated id used.
   - eval-run with `X-Correlation-Id` → candidate runs carry it.

4. **API metrics behavioral RED** — `tests/test_api_prometheus.py`:
   - `GET /metrics` (no auth headers) → 200, Prometheus text.
   - after a successful invoke, `ac_runs_total{status="success",source="playground"}` increased by 1.
   - after an over-cap invoke (seed ≥ cap, feature 009) → `ac_requests_refused_total{code="budget.exceeded"}`
     increased; after an unknown-role invoke → `{code="access.denied"}` increased.
   - `/metrics` body contains no workspace id / prompt / response content. RED now = no `/metrics`, no counters.

5. **GREEN**: add `api/correlation.py`, `api/metrics.py`; wire `Depends(correlation_id)` + response header on
   invoke + eval; `record_run` in `run_sink`; `record_refusal` in `app_error_handler`; `GET /metrics` in
   `main.py`; add `prometheus-client` to `.[api]`.

## Regression / constitution checks

- Purity (P6): `.venv/bin/python -m pytest tests/test_runtime_purity.py -q`.
- Deny-by-default coverage (008): `tests/test_api_rbac_coverage.py` green (`/metrics` is non-`/api/v1`).
- Spend cap (009) unaffected: `tests/test_api_spend_cap.py` green (now also drives the refusal counter).
- Full regression: `.venv/bin/python -m pytest -q` — record the count (baseline 423 + new).

## Definition of Done

- New tests green; full regression green; purity + coverage green.
- Correlation honored end-to-end + echoed; both counters increment + scrapeable; no leakage.
- Deferred (documented): Langfuse v3 spans, ClickHouse warehouse, per-call ModelCallLog, correlation
  propagation to the outbound provider call (F13's provider-hop), deadline-rate histogram.
- Docs reconciled (M6 observability banner) at converge/DoD.
