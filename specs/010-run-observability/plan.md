# Implementation Plan: Run Observability — Correlation + Run-Status Metrics

**Branch**: `feat/backend-m0-foundation` (slice 010) | **Date**: 2026-11-07 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/010-run-observability/spec.md`

## Summary

Two infra-light observability halves, both at the API boundary (runtime stays pure):

1. **Correlation ingestion (F13):** a `correlation_id` dependency resolves an inbound id from the request
   — W3C `traceparent` trace-id (preferred) or `X-Correlation-Id`, shape-validated, else a generated id —
   and both paid handlers (invoke, eval-run) pass it into `RunContext.correlation_id` (today they discard
   the inbound value and always generate). The resolved id is echoed back on an `X-Correlation-Id` response
   header. Downstream (`RunContext → RunRecord → run_sink → runs API`) already carries it; no new storage.
2. **Run-status metrics (F12):** a small `api/metrics.py` (prometheus_client, custom registry) with
   `ac_runs_total{status,source}` (incremented once per recorded run, in `run_sink`) and
   `ac_requests_refused_total{code}` (incremented in the error handler for `budget.exceeded` + `access.denied`
   — refusals that never become a run). Exposed at an unauthenticated `GET /metrics` (outside `/api/v1`,
   beside `/healthz`). Both increments are best-effort (a metrics error can never fail a run).

## Technical Context

**Language/Version**: Python 3.12.13
**Primary Dependencies**: FastAPI/Starlette, SQLAlchemy 2.0 async — existing. **New: `prometheus-client`**
(added to `.[api]`; installed 0.26.0 in the venv — the one in-scope M6 dep).
**Storage**: none new. Reuses `runs.correlation_id` (existing). Metrics are in-process (per-replica).
**Testing**: pytest + pytest-asyncio, httpx ASGITransport; gateway mocked. Counters read via
`registry.get_sample_value(...)`. Run: `cd services && .venv/bin/python -m pytest`.
**Target Platform**: Linux server (FastAPI).
**Project Type**: web service (`services/`); no frontend contract change (runs API already exposes
`correlation_id`).
**Performance Goals**: one header parse + one counter `.inc()` per request — negligible.
**Constraints**: API-layer only — `runtime/`+`evals/` keep zero HTTP/metrics imports (P6). Best-effort
telemetry (P8/FR-009). No tenant/content leakage in metrics or logs (P5/SC-009). No migration.
**Scale/Scope**: internal pilot; process-local counters scraped by Prometheus (cross-replica aggregation
is the scraper's job). Langfuse/ClickHouse/per-call ModelCallLog deferred (infra-blocked).

## Constitution Check

| Principle | Gate | Status |
|---|---|---|
| **P1/P9 honesty** | deferrals documented; genuine RED; FE n/a | PASS — Langfuse/ClickHouse/per-call-log deferred (infra); RED = inbound correlation ignored + no counter. |
| **P3 deterministic** | shape-validate untrusted inbound id, fail-safe to generated | PASS — malformed inbound id ignored (FR-004). |
| **P5 tenancy / no-leak** | metrics + logs carry no tenant/content | PASS — counters are aggregate labels (status/source/code) only; SC-005 test asserts no tenant/content. |
| **P6 runtime purity** | runtime/evals untouched | PASS — all code in `api/`; runtime already emits correlation_id+outcome; purity test stays green. |
| **P8 observability** | correlation + run-status; best-effort | PASS — the two infra-light halves; telemetry best-effort (FR-009). |
| **Paid-call protocol** | no new paid calls | PASS — gateway mocked. |

**No violations.** One expected M6 dependency (`prometheus-client`). No migration.

## Project Structure

```text
services/
├── pyproject.toml               # EDIT — add prometheus-client to [api]
├── api/
│   ├── metrics.py               # NEW — registry + ac_runs_total{status,source} + ac_requests_refused_total{code} + record_run()/record_refusal()/render() (all best-effort)
│   ├── correlation.py           # NEW — resolve_correlation_id(request)->str (traceparent trace-id / X-Correlation-Id, shape-validated, else uuid4) + correlation_id FastAPI dep
│   ├── main.py                  # EDIT — GET /metrics (unauth, non-/api/v1, beside /healthz)
│   ├── errors.py                # EDIT — app_error_handler records a refusal for budget.exceeded / access.denied (best-effort)
│   ├── run_sink.py              # EDIT — record_run(outcome, source) after persist (best-effort)
│   └── routers/
│       ├── invoke.py            # EDIT — Depends(correlation_id) → ctx.correlation_id; echo X-Correlation-Id on the StreamingResponse
│       └── evals.py             # EDIT — Depends(correlation_id) → ctx.correlation_id; echo X-Correlation-Id on the response
└── tests/
    ├── test_correlation.py          # NEW — pure: parse/precedence/validation/fallback
    ├── test_metrics_unit.py         # NEW — pure: record_* best-effort (never raise), render() content-type
    ├── test_api_correlation.py      # NEW — invoke+eval: inbound honored, generated fallback, malformed ignored, echoed header
    └── test_api_prometheus.py          # NEW — /metrics unauth + text; run-status increments; refusal increments; no tenant/content
```

**Structure Decision**: Two tiny pure modules (`metrics.py`, `correlation.py`) + thin wiring at the
boundary points a run already flows through (handlers for correlation in; `run_sink` for the run counter;
`app_error_handler` for the refusal counter — the one place every typed refusal passes). This keeps each
increment at a single DRY site and the runtime untouched.

## Key design decisions (detail in research.md)

1. **Correlation precedence & validation** — `traceparent` trace-id (32-hex, non-zero) > `X-Correlation-Id`
   (`^[A-Za-z0-9._-]{1,128}$`) > generated `uuid4().hex`. Malformed → treated as absent (P3, FR-004).
2. **Run counter at `run_sink`** — `persist_run_record` is the single site every recorded run (both paths)
   passes through, so `ac_runs_total{status=outcome, source}` increments exactly once per run there.
3. **Refusal counter at the error handler** — `app_error_handler` sees every typed `AppError`; increment
   `ac_requests_refused_total{code}` for `budget.exceeded`/`access.denied`. Single DRY site; honestly named
   (requests refused by a deterministic control), covers 009 + 008 without touching their code.
4. **`/metrics` unauthenticated, non-`/api/v1`** — beside `/healthz` in `main.py`; aggregate-only, so the
   008 deny-by-default coverage test (which only flags `/api/v1` routes) is unaffected.
5. **Best-effort** — `record_run`/`record_refusal` wrap their body in try/except (swallow) so telemetry can
   never fail a run, its stream, or its persistence (FR-009).
6. **Custom `CollectorRegistry`** — avoids global-default duplicate-registration hazards and lets tests read
   exact values via `get_sample_value`.

## Phase 0 / 1 outputs

- `research.md` — the six decisions + alternatives (why error-handler for refusals, why custom registry,
  traceparent format).
- `data-model.md` — the (reused) `correlation_id` field + the two counters' names/labels + the parse rules.
- `contracts/observability.md` — correlation header in/out contract, `/metrics` exposition, counter names.
- `quickstart.md` — RED→GREEN order + validation matrix + purity/coverage checks.
