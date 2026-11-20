# Data Model: Run Observability (010)

**No new tables, no migration, no new persisted column.** Reuses the existing `runs.correlation_id`; adds
only in-process metric counters.

## Reused: `runs.correlation_id` (existing)

- `Run.correlation_id: str` (`services/api/models.py`) — already written by `run_sink.persist_run_record`
  from `RunRecord.correlation_id`, already surfaced by the runs API (`schemas/runs.py`).
- This slice changes only the **value** it receives: today an API-generated `uuid4().hex`; after, the
  resolved inbound id (or a generated fallback).

## Correlation resolution (pure, `api/correlation.py`)

```
resolve_correlation_id(request) -> str:
  tp = request.headers.get("traceparent")
  if tp matches ^[0-9a-f]{2}-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}$  and trace-id != 32×"0":
      return trace-id            # 32-hex
  xc = request.headers.get("x-correlation-id")
  if xc matches ^[A-Za-z0-9._-]{1,128}$:
      return xc
  return uuid4().hex             # fallback (today's behavior)
```
- FastAPI dependency `correlation_id(request: Request) -> str` wraps the above; handlers `Depends(correlation_id)`.
- Resolved id → `RunContext.correlation_id` (invoke + eval) AND echoed on the `X-Correlation-Id` response header.

## Metrics (in-process, `api/metrics.py`)

Custom `CollectorRegistry`; two counters:

| Counter | Labels | Incremented | Meaning |
|---|---|---|---|
| `ac_runs_total` | `status` (run outcome), `source` (playground/eval) | once per recorded run, in `persist_run_record` (after flush) | completed-run volume by outcome → availability / failure / timeout rates |
| `ac_requests_refused_total` | `code` (`budget.exceeded`, `access.denied`) | in `app_error_handler` for those codes | admission refusals that never become a run → cap-bite (009) + denial (008) rates |

- `status` values: whatever `RunRecord.outcome` yields — success / invalid_input / context_overflow /
  provider_error / timeout / output_invalid / tool_call_rejected / degraded / config_error.
- Helpers: `record_run(status, source)`, `record_refusal(code)` — both best-effort (swallow exceptions);
  `render() -> (bytes, content_type)` = `generate_latest(REGISTRY)`, `CONTENT_TYPE_LATEST`.

## Exposure

- `GET /metrics` (main.py, non-`/api/v1`, unauthenticated) → `Response(render()[0], media_type=render()[1])`.
- Output is aggregate label sets only — no workspace id tied to content, no prompt/response, no secrets.

## Composition / ordering (unchanged controls)

- A gated request still resolves authn → membership → role (008) → budget (009) → handler. The refusal
  counter observes the 402/403 those controls raise; the run counter observes the outcome of runs that make
  it through. Correlation is resolved for the request regardless of outcome (it's a plain header read).
