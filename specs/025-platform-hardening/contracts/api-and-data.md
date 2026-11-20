# Contract — API, errors, settings, metrics, persistence (feature 025)

## 1. Typed errors (existing envelope `{"error": {"code", "message", "detail"}}`)
| Trigger | HTTP | code | Class | Where reachable today |
|---------|------|------|-------|----------------------|
| breaker refused on a non-streaming invoke / pre-stream | 502 | `provider.unavailable` | existing `ProviderUnavailable` | invoke (JSON mode); in SSE it is the terminal `error` frame `type=provider_unavailable` |
| FK 23503 on a binding/chunk (missing or other-workspace target) | 404 | `not_found` | existing `NotFound` | `create_version` (retrieval bindings) — normally pre-checked; the mapping covers the race |
| `frozen_binding:` (23001) | 409 | `version.frozen` | **new** `VersionFrozen(ConflictError)` | any binding UPDATE/DELETE or late INSERT |
| `ck_tool_bindings_one_target` (23514) | 400 | `input.invalid` | existing `InputInvalid` | no API writer yet (G6) — data layer only |
| `fk_agents_active_version` (23503) | 400 | `input.invalid` | existing `InputInvalid` | agent create (`agents.py:148`), publish (`agents.py:241`), eval promote (`evals.py:150`) |
| `chunk_dim_mismatch:` (23514) | — | job error `chunk_dim_mismatch` (non-retryable) | ingestion job failure | `ingest_repo.py:79` |
| KB create with `embedding_dim ≠ 1024` | 400 | `input.invalid` | existing `InputInvalid`, raised in the router (no 422) | knowledge-base create (FR-021) |
| ingestion embedding refused by breaker | — | job error `provider_unavailable` (retryable) | ingestion job failure | `ingest_executor.py` (FR-012) |
| anything else | 500 | (existing unhandled-exception path) | re-raised unchanged | honest failure — never mapped to a 4xx guess |

Implementation: `api/db_errors.py:map_integrity_error(exc: IntegrityError) -> AppError | None` (SQLSTATE + constraint
name / message prefix via `exc.orig.sqlstate` and `diag`), applied in `tenant_db` commit/flush paths of the routes
above. Messages contain ids only, never names of other-workspace resources.

## 2. Run read model
`RunOut` gains `retries: int` (from `runs.retry_count`). FR-015 disclosure: the OpenAPI descriptions of `retries` and
`cost_usd` state that cost is computed from **reported usage only** — failed attempts with no reported usage are
unpriced (an explicit `cost_basis` field is a Nice follow-up, not built here). `outcome` may now be `provider_unavailable`;
`RunsStatsOut.by_outcome` gains that key when present. OpenAPI regenerated (`scripts/emit_openapi.py`) and the FE
typed client regenerated (`npm run gen:api`) — FE behaviour unchanged (field unused until wiring).

`run_sink` persists `retry_count = rec.retries`.

## 3. Settings (`api/settings.py`, env prefix `AC_`)
| Env | Default | Maps to |
|-----|---------|---------|
| `AC_PROVIDER_MAX_ATTEMPTS` | 3 | `ResiliencePolicy.max_attempts` |
| `AC_PROVIDER_BACKOFF_BASE_S` | 0.5 | `backoff_base_s` |
| `AC_PROVIDER_WAIT_CAP_S` | 8.0 | `per_wait_cap_s` |
| `AC_BREAKER_FAILURE_THRESHOLD` | 5 | `failure_threshold` |
| `AC_BREAKER_COOLDOWN_S` | 30.0 | `cooldown_s` |
| `AC_BATCH_CONCURRENCY` | 4 | `batch_concurrency` |
Validated `> 0` at settings load (bad config fails startup, not first request).

## 4. Metrics (`api/metrics.py`, Prometheus; no tenant labels — FR-013)
| Metric | Type | Labels |
|--------|------|--------|
| `ac_provider_retries_total` | counter | provider, model, class |
| `ac_breaker_transitions_total` | counter | provider, model, class, to |
| `ac_breaker_refusals_total` | counter | provider, model, class |
| `ac_breaker_degraded_total` | counter | — |
| `ac_batch_queue_wait_seconds` | histogram | provider, model |
Existing `ac_runs_total{outcome,source}` picks up `provider_unavailable` automatically.

## 5. Shared store (`api/breaker_store.py: RedisBreakerStore`)
Uses the existing `redis_client.get_redis()`; `None` ⇒ the API wires `LocalBreakerStore` (not degraded). Keys
`ac:cb:{provider}:{model}:{class}:{fails|open|trial}` — provider/model are registry ids (bounded, non-tenant).
Commands used: `EXISTS`, `GET`, `INCR`, `PEXPIRE`, `SET … PX`, `SET … NX PX`, `DEL`, `MULTI/EXEC` — no Lua.

## 6. Degraded-mode record
New row in `docs/backend/degraded-mode.md` per-control table: **Provider circuit breaker (025)** — Redis dep: yes —
Redis down ⇒ per-instance local breaker, never refuses because of the outage, `ac_breaker_degraded_total` alert — code
`api/breaker_store.py` + `runtime/providers/resilience.py` — test `test_breaker_store.py::test_store_outage_falls_back_local`.

## 7. Migration
`0014_runs_retry_count` (US1: `runs.retry_count`) + `0015_binding_integrity` per [data-model.md §B](../data-model.md); preflight refuses on violations (FR-022);
`downgrade()` removes every object it added (round-trip tested).
