# Quickstart — validating feature 025

No live or paid provider calls anywhere in this guide: every resilience scenario runs against the fault-injecting
transport stub (`transport.stream_chunks` / `transport.embed` replaced wholesale, as in every prior feature).

## Prerequisites
```bash
cd services
./.venv/bin/python -m pytest -q --collect-only | tail -1      # baseline count before 025
make -C .. up                                                  # Postgres(5433)+Redis(6380)+LocalStack, migrate to head
```

## 1. Resilience — infra-free (fake transport, fakeredis, injected clock/sleep)
```bash
./.venv/bin/python -m pytest -q tests/test_resilience_retry.py tests/test_resilience_breaker.py \
  tests/test_resilience_bulkhead.py tests/test_transport.py tests/test_gateway.py tests/test_gateway_embed.py
```
Expected (maps to spec):
| Scenario | Evidence |
|----------|----------|
| US1-1 fail×2 then succeed | run `SUCCESS`, `retries == 2`, text delivered once, cost = reported usage only (SC-001, FR-015) |
| US1-2 non-transient | 1 transport call, typed error, `retries == 0` |
| US1-3 fail after first token | 1 call, no duplicate text, `PROVIDER_ERROR` (pinned, FR-005), no retry |
| US1-4 Retry-After 3600 s, deadline 30 s | ends ≤ 31 s of *simulated* time, no hang (SC-002) |
| US1-5 always failing | exactly `max_attempts` calls |
| US2-1..4 | breaker opens at N=5; refusal makes 0 calls and takes < 50 ms; 1 trial among K concurrent; model B unaffected (SC-003) |
| US2-5 store raises | local fallback, `breaker_degraded` observed, request still attempted |
| US3-1..3 | batch-open leaves live attempted; live-open refuses batch; in-flight batch ≤ cap (SC-004) |
| G1 | transport kwargs carry `max_retries=0`, `num_retries=0` |

## 2. Parity across the four callers
```bash
./.venv/bin/python -m pytest -q tests/test_invoke_parity.py tests/test_resilience_parity.py
```
Same fault script through playground, published (`/v1/invoke`), channel-sourced ctx, and an eval case ⇒ identical
`retries`, outcome, cost (FR-014). The eval case is classed `batch`, the others `live`.

## 3. Integrity — needs Postgres (`-m integration`)
```bash
./.venv/bin/python -m pytest -q -m integration tests/test_binding_integrity.py tests/test_migration_0015.py
```
| Scenario | Expected |
|----------|----------|
| US4-1 binding → missing / other-workspace skill, tool, mcp tool, KB | `IntegrityError` 23503; via API `404 not_found` |
| US4-2 UPDATE/DELETE a binding of a committed version | 23001 `frozen_binding:`; snapshot hash unchanged; via API `409 version.frozen` |
| late INSERT of a binding into an existing version | rejected (23001) |
| version + bindings in one txn (normal `create_version`) | succeeds (regression guard) |
| US4-3 tool binding with 0 or 2 targets | 23514 |
| US4-4 active version of another agent | 23503 (deferred, at commit); API `400 input.invalid` |
| US4-5 chunk under a KB with `embedding_dim ≠ 1024` (seeded via SQL) | 23514 `chunk_dim_mismatch:`; ingestion job fails typed |
| US4-5b `POST` KB create with `embedding_dim: 768` | `400 input.invalid` (FR-021 create guard) |
| US4-6 hard-delete a referenced skill/tool/KB | 23503 (RESTRICT); archive still 200 |
| FR-022 seed a dangling binding, run `alembic upgrade head` | upgrade aborts with rule/count/ids report; schema still at 0014 |
| round-trip | `upgrade head → downgrade 0014 → upgrade head` clean (and `downgrade 0013` drops `runs.retry_count`) |

## 4. Shared store against real Redis (opt-in, multi-instance simulation)
```bash
./.venv/bin/python -m pytest -q -m integration tests/test_breaker_store.py
```
3 `Guard` instances sharing one Redis ⇒ exactly 1 trial per cooldown window (SC-003); `docker stop` of Redis
mid-test ⇒ local fallback + `ac_breaker_degraded_total` increments (FR-009).

## 5. Full regression (SC-006) + contract regen
```bash
./.venv/bin/python -m pytest -q -m "not integration"
./.venv/bin/python -m pytest -q -m integration
make -C .. test-integration                                   # LocalStack set
(cd .. && services/.venv/bin/python scripts/emit_openapi.py)   # script lives at repo-root scripts/
(cd ../frontend && source ~/.nvm/nvm.sh; nvm use 24; npm run gen:api && npm run lint && npm run build)
```
Expected: all green; `RunOut.retries` present in `frontend/src/lib/api/schema.ts`.

## 6. Observability spot-check (local stack, no provider)
`curl -s localhost:8000/metrics | grep -E 'ac_(provider_retries|breaker_|batch_queue)'` lists the new series (zero
values are fine — no live provider is called).

## Not verified by this guide (honest)
Real provider `Retry-After` header shapes, real 429 bursts, default threshold suitability, and multi-replica
behaviour beyond simulated instances — all need the first live-provider window (owner-gated).
