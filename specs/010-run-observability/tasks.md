# Tasks: Run Observability — Correlation + Run-Status Metrics (010)

**Feature**: `specs/010-run-observability` | **Branch**: `feat/backend-m0-foundation` | **Date**: 2026-11-07

**Inputs**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md) (D1–D7),
[data-model.md](./data-model.md), [contracts/observability.md](./contracts/observability.md),
[quickstart.md](./quickstart.md)

**TDD is mandatory** (P9): behavioral RED before GREEN — inbound correlation currently ignored (server
overwrites with a uuid); no run-status/refusal counters exist. Tests use `httpx` ASGITransport +
`dev_headers`, gateway mocked; no live/paid calls. Change is **API-layer only** — `runtime/`+`evals/`
untouched (P6). One new dep (`prometheus-client`); **no migration** (reuses `runs.correlation_id`).

**Conventions**: `cd services && .venv/bin/python -m pytest`. `[P]` = parallelizable. Story labels on
story-phase tasks.

---

## Phase 1: Setup

- [x] T001 Baseline + dependency: run `.venv/bin/python -m pytest -q` (record count, expect 423). Add
  `prometheus-client` to the `[api]` extra in `services/pyproject.toml`; confirm it imports in the venv
  (`.venv/bin/python -c "import prometheus_client"` — already installed 0.26.0). Confirm anchors:
  `api/main.py` `/healthz` inline route, `api/errors.py` `app_error_handler`, `api/run_sink.py`
  `persist_run_record`, invoke/eval handlers passing `correlation_id` into `RunContext`. No code change.

## Phase 2: User Story 1 (P1) — Correlate a request to its run 🎯

**Goal**: an inbound correlation id is honored on invoke + eval and echoed back; a generated one is used
when absent/malformed.
**Independent test**: send invoke/eval with/without/with-malformed correlation headers; assert the run's
`correlation_id` and the `X-Correlation-Id` response header.

- [x] T002 [US1] RED: `services/tests/test_correlation.py` (pure) — `resolve_correlation_id` returns the
  32-hex `traceparent` trace-id for a valid header (and ignores an all-zero trace-id); returns a valid
  `X-Correlation-Id` (`^[A-Za-z0-9._-]{1,128}$`) when no traceparent; ignores a malformed `X-Correlation-Id`;
  falls back to a generated 32-hex id when neither present; traceparent takes precedence over
  `X-Correlation-Id`. Fails (module absent). Use a lightweight fake request exposing `.headers`.
- [x] T003 [US1] Implement `services/api/correlation.py`: `resolve_correlation_id(request) -> str` per D1
  (regex-validated traceparent trace-id → X-Correlation-Id → `uuid4().hex`) + a FastAPI dependency
  `correlation_id(request: Request) -> str`. Pure of DB; no metrics import. GREEN for T002.
- [x] T004 [US1] RED: `services/tests/test_api_correlation.py` (ASGI, compose DB, gateway mocked) — invoke
  with `X-Correlation-Id: my-corr-123` → recorded run `correlation_id == "my-corr-123"` AND response header
  `X-Correlation-Id == "my-corr-123"`; valid `traceparent` → run correlation == trace-id; no header →
  generated id, echoed; malformed `X-Correlation-Id` → generated id used; eval-run with `X-Correlation-Id`
  → candidate runs carry it. Fails now (server overwrites with uuid; eval sets none).
- [x] T005 [US1] Wire it: in `services/api/routers/invoke.py` and `evals.py`, add `corr: str =
  Depends(correlation_id)`, pass `correlation_id=corr` into the `RunContext`, and set the
  `X-Correlation-Id: corr` response header (on the `StreamingResponse` for invoke; via a `Response` for
  eval). Make T004 pass (GREEN). Run `tests/test_api_invoke_trust_boundary.py` + `test_api_evals_publish.py`
  regressions.

**Checkpoint**: correlation honored end-to-end on both paid paths.

## Phase 3: User Story 2 (P1) — See run health at a glance

**Goal**: per-outcome run counts + admission-refusal counts exposed at an unauthenticated `/metrics`.
**Independent test**: drive runs/refusals; read `/metrics`; assert the labeled counts and no leakage.

- [x] T006 [US2] RED: `services/tests/test_metrics_unit.py` (pure) — `record_run("success","playground")`
  then `REGISTRY.get_sample_value("ac_runs_total", {"status":"success","source":"playground"})` == 1.0;
  `record_refusal("budget.exceeded")` increments `ac_requests_refused_total{code=...}`; both are best-effort
  (monkeypatch the counter's `.labels`/`.inc` to raise → helper does NOT raise); `render()` returns
  `(bytes, CONTENT_TYPE_LATEST)`. Fails (module absent).
- [x] T007 [US2] Implement `services/api/metrics.py`: a module `CollectorRegistry`; `ac_runs_total`
  (`status`,`source`) + `ac_requests_refused_total` (`code`) counters; `record_run(status, source)` /
  `record_refusal(code)` (both wrapped best-effort, debug-log on failure); `render() -> (bytes, str)` via
  `generate_latest` + `CONTENT_TYPE_LATEST`. No HTTP/DB imports. GREEN for T006.
- [x] T008 [US2] RED: `services/tests/test_api_prometheus.py` (ASGI, compose DB, gateway mocked) — `GET
  /metrics` with no auth → 200 + Prometheus text; after a successful invoke,
  `ac_runs_total{status="success",source="playground"}` increased by 1 (read via the registry); after an
  over-cap invoke (seed ≥ cap per 009) → `ac_requests_refused_total{code="budget.exceeded"}` increased;
  after an unknown-role invoke → `{code="access.denied"}` increased; `/metrics` body contains no workspace
  id / prompt / response content. Fails now (no endpoint, no counters).
- [x] T009 [US2] Wire it: `record_run(outcome, rec.source)` in `services/api/run_sink.py` after the flush
  (best-effort); `record_refusal(code)` in `services/api/errors.py` `app_error_handler` for `budget.exceeded`
  / `access.denied` (best-effort, before returning the envelope); add `GET /metrics` to `services/api/main.py`
  (beside `/healthz`, unauthenticated, non-`/api/v1`) returning `render()`. Make T008 pass (GREEN).

**Checkpoint**: run-status + refusal counts scrapeable; no leakage.

## Phase 4: Polish & Cross-Cutting

- [x] T010 Purity + full regression: `.venv/bin/python -m pytest tests/test_runtime_purity.py -q` (P6 green)
  + `tests/test_api_rbac_coverage.py` (008 coverage still green — `/metrics` is non-`/api/v1`) + `.venv/bin/
  python -m pytest -q` (no regressions vs T001 baseline + new tests); record the pass count.
- [x] T011 Reconcile docs: add an M6 observability built-state banner to `docs/implementation-plan.md`
  (correlation ingestion F13 + run-status/refusal counters F12 live; Langfuse/ClickHouse/per-call-log + the
  provider-hop of correlation still deferred) and note the `/metrics` endpoint + `GET /api/v1/.../runs`
  correlation in `docs/backend-integration-status.md` / `docs/backend/09` as appropriate.

---

## Dependencies

```
Setup (T001: baseline + dep)
  ├─► US1 (T002 RED → T003 correlation.py → T004 RED → T005 wire GREEN)     ← independent of US2
  ├─► US2 (T006 RED → T007 metrics.py → T008 RED → T009 wire GREEN)         ← needs prometheus-client (T001)
  └─► Polish (T010 regression → T011 docs)
```

- **US1 and US2 are independent** (different modules/files) — could be done in parallel; sequence US1 then US2.
- US2 depends on the `prometheus-client` dep from T001.
- T010/T011 after both stories land.

## Parallel opportunities

- The pure modules `correlation.py` (T003) and `metrics.py` (T007) are different files → parallelizable.
- Within US2, the three wiring sites in T009 (run_sink, errors, main) are different files.

## Implementation strategy

- **MVP = Setup + US1 + US2** (both P1, both small). Each story is independently testable.
- **TDD per story**: pure RED → impl → API behavioral RED → wire GREEN. Never weaken a test.
- Full regression + purity at T010.

## Task count & format

- **Total: 11 tasks** — Setup 1, US1 4, US2 4, Polish 2.
- Per story: **US1 = 4**, **US2 = 4**.
