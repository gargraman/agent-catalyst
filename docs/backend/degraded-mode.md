# Degraded-mode decision record — Redis SPOF blast radius & per-control behavior

**Feature**: [021 — M5 Redis-SPOF Degraded Mode + PgBouncer txn-mode](../../specs/021-m5-redis-degraded-pgbouncer/spec.md)
(the M5 security-completion capstone). **Status**: BUILT · **Scope**: FR-024 (per-control degraded behavior),
FR-025 (blast-radius + decision record, this doc), FR-026 (tenant isolation under txn-mode pooling).

> This is the **FR-025 artifact**: the shared cache (Redis) is a single point of failure for several controls,
> and this record makes its blast radius and each control's degraded behavior **explicit, deliberate, and
> test-verified** — so a Redis outage produces *known* behavior, not a silent weakening of security. Every claim
> below cites the code that implements it and the test that proves it. The Redis SPOF is an **accepted** risk for
> the P1 pilot *given* these documented, tested behaviors (spec Assumptions).

## TL;DR

- Two opposite error policies, on purpose: **security controls fail CLOSED**, the **availability control fails
  OPEN + alerts**. They live in separate modules (`auth/denyset.py` vs `ratelimit.py`) so the two policies can
  never be confused.
- Only **two** controls actually touch Redis today: the **deny-set** (revocation) and the **rate limiter**.
  Idempotency is in-process; the spend cap is Postgres-backed. Documenting the latter two against a not-yet-built
  Redis store would claim behavior that does not exist — so this record describes **today's reality** (021 D2).
- A **configured-but-unreachable** Redis flips **`/readyz` → 503** so the load balancer / k8s drains the
  instance (021 D3), consistent with the deny-set already fail-closing every authenticated request to `401`.

## Parity rule (applies to every Redis-backed control)

`AC_REDIS_URL` **unset** → Redis is not consulted at all → **no-op allow** (dev/test parity with the pre-016
seam). Only a Redis that is **configured and then errors** triggers the fail-closed / fail-open / 503 paths
below. This mirrors `redis_client.get_redis()` returning `None` when unset
([`api/redis_client.py:21`](../../services/api/redis_client.py)).

## Blast-radius map

```
                         AC_REDIS_URL down (configured but unreachable)
                                        │
             ┌──────────────────────────┼──────────────────────────┐
             ▼                          ▼                          ▼
        deny-set (018)            rate limiter (016)           /readyz (021)
        FAIL CLOSED → 401         FAIL OPEN + alert            503 not-ready
        (console + caller)        (ac_rate_limit_degraded)     (drains traffic)

        NOT affected by Redis today:  idempotency (in-process)   spend cap (Postgres)
```

A Redis outage takes down **authenticated console traffic** (deny-set → 401 on every request) **and**
**readiness** (→ 503) **together** — the "accepted SPOF" is honestly a hard dependency for the console plane
while Redis is configured. Rate limiting degrades to allow-all (with an alert), never an outage. Idempotency and
the spend cap are unaffected because neither depends on Redis in this slice.

## Per-control decision table

| Control | Redis dep today | Degraded behavior on Redis-down | Recovery | Code | Test |
|---|---|---|---|---|---|
| **Deny-set** (revocation, 018) | **Yes** | **Fail CLOSED → `401`**, both the console-session plane and the caller-token plane. A deny-set that can't be consulted must never silently admit a revoked subject. | Redis back → normal, **no restart** | raises at [`denyset.py:48`](../../services/api/auth/denyset.py); mapped to `AuthError`→401 at [`deps.py:79-83`](../../services/api/deps.py) (console) and [`token_auth.py:80-82`](../../services/api/publish/token_auth.py) (caller) | `test_redis_degraded_mode.py::test_denyset_fails_closed_both_planes` |
| **Rate limiter** (016) | **Yes** | **Fail OPEN + alert.** `check()` returns `Decision(allowed=True, degraded=True)`; the dep increments `ac_rate_limit_degraded_total` (the alert signal) + a throttled WARNING. An availability control must never self-inflict an outage. | Redis back → enforcement resumes, **no restart** | [`ratelimit.py:83`](../../services/api/ratelimit.py); alert at [`deps.py:253-258`](../../services/api/deps.py); counter at [`metrics.py:73`](../../services/api/metrics.py) | `::test_ratelimit_fails_open_and_alerts` |
| **Idempotency** (D8) | **No** (in-process `set()`) | **`409 idempotency.in_progress`** on a concurrent retry — a *retryable* fail-closed, **independent of Redis**. Durable replay of a completed key (a Redis store) is explicitly **deferred**. | n/a (no Redis dependency today) | [`deps.py:321-336`](../../services/api/deps.py) | `::test_idempotency_fails_closed_retryable` |
| **Spend cap** (009) | **No** (Postgres ledger) | **Fail CLOSED** on any ledger/compute error: refuse with a controlled `500 {reason: spend_indeterminate}` **before any paid call** — and it does *not* falsely claim `budget.exceeded`. | Postgres back → normal | [`deps.py:185-195`](../../services/api/deps.py) | `::test_spend_cap_fails_closed` |
| **Spend *reservation*** (Redis) | **Future** | When built, MUST fail closed (no reservation → no paid call). | — | (deferred — [umbrella data-model](../../specs/015-m5-security-completion/data-model.md)) | — |
| **`/readyz`** (021 D3) | **Yes** (when configured) | **503 not-ready** `{database: ok, redis: down}` when a *configured* Redis is unreachable, so the LB/k8s gate drains the instance instead of sending users into a wall of 401s. Unset `AC_REDIS_URL` → Redis not a readiness dep → stays **200** (parity carve-out). | Redis back → 200 | [`main.py` `/readyz`](../../services/api/main.py) via [`redis_client.is_healthy()`](../../services/api/redis_client.py) | `::test_readyz_503_when_configured_redis_down`, `::test_readyz_200_when_redis_unset_carveout` |
| **Provider circuit breaker** (025) | **Yes** (when configured) | **Fail to LOCAL + alert.** A shared-store error makes that decision on the per-process `LocalBreakerStore` (still protective, just not cross-replica) and increments `ac_breaker_degraded_total`. Unset `AC_REDIS_URL` → local store by design, **not** degraded (parity carve-out). Never fails open to unbounded hammering, never fails closed on live traffic. | Redis back → shared state resumes on the next call, **no restart** | [`Guard._store_call`](../../services/runtime/providers/resilience.py) (fallback) · [`RedisBreakerStore`](../../services/api/breaker_store.py) · wiring [`resilience_wiring.py`](../../services/api/resilience_wiring.py) · counter [`metrics.py`](../../services/api/metrics.py) | `test_breaker_store.py::test_store_outage_falls_back_local`, `::test_real_redis_closed_port_falls_back_local`, `::test_wiring_without_redis_uses_local_store_and_is_not_degraded` |

> Alerting note (025): an alert **rule** on `ac_breaker_degraded_total` (and `ac_rate_limit_degraded_total`) is deferred
> to the first production deploy — the repo has no alerting stack yet (constitution §8). The counters exist and are scraped.

**Recovery is restart-free for every control**: the Redis client is a lazy process-wide pool
([`redis_client.py`](../../services/api/redis_client.py)) and each control re-reads it per request, so a cache
that comes back mid-run resumes normal behavior on the next request with no process bounce
(`::test_controls_resume_after_redis_recovers`).

## Tenant isolation under transaction-mode pooling (FR-026)

PgBouncer in **transaction mode** returns a server connection to the pool at the end of each transaction, so a
pooled connection is shared across tenants' requests. The tenant scope is set with
`set_config('app.workspace_id', :ws, is_local=true)` — i.e. **`SET LOCAL`** — **inside** the request transaction
([`tenancy.py:32-36`](../../services/api/tenancy.py), wrapped by `db.tenant_session`'s `session.begin()`).
`SET LOCAL` is transaction-scoped, so it is discarded at COMMIT/ROLLBACK and **cannot leak one request's tenant
into the next pooled request** — proven against a real `pool_size=1` engine **and** through the live PgBouncer
txn-mode pooler by `tests/test_pgbouncer_txn_isolation.py` (0 cross-tenant reads; empty GUC at the next request's
entry). The deployment routes **only the app role (`ac_app`)** through PgBouncer; the migration/owner role (`ac`,
BYPASSRLS) connects **direct** to Postgres (`deploy/compose.yaml`, 021 D4 — owner/pooled server state must never
mix).

## Operator playbook

- **Alert**: `ac_rate_limit_degraded_total` increasing ⇒ Redis is unreachable and the limiter is failing open.
  Expect, concurrently: authenticated console requests returning `401` (deny-set fail-closed) and `/readyz` at
  `503` (the instance will be drained). Treat as a **Redis availability incident**, not an app bug.
- **Confirm**: `GET /readyz` → `503 {checks: {database: ok, redis: down}}` isolates the fault to Redis (DB fine).
- **Recover**: restore Redis. All controls resume on their next request — **no app restart required**. `/readyz`
  returns to `200` and the LB re-adds the instance.
- **Accepted risk**: the Redis SPOF is accepted for the P1 pilot *because* these behaviors are explicit and
  tested. A follow-on (post-pilot) can add Redis HA / a durable reservation store (deferred, 021 D2).
