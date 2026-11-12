# Phase 1 Data Model: 021 Redis-SPOF Degraded Mode + PgBouncer Transaction-Mode

Authoritative shapes are the shared umbrella: [015 data-model — Redis structures table](../015-m5-security-completion/data-model.md). This file records the 021-specific **state/topology** notes. **021 adds no persistent entity and no migration.**

## No new tables, no migration

The controls and their Redis structures are 016/018's, unchanged. 021 re-routes Postgres access through PgBouncer (a **connection-topology** change, not a schema change) and adds tests + a decision doc.

## Degraded-mode decision record (the FR-025 artifact — a doc, not a table)

Per-control behavior on shared-cache (Redis) unavailability, grounded in code:

| Control | Redis dependency today | Degraded behavior | Recovery | Source |
|---------|------------------------|-------------------|----------|--------|
| Deny-set (revocation) | **Yes** (016/018) | **Fail closed** → `401` (both console + caller planes) | Redis back → normal, no restart | `denyset.py:48`, `deps.py:72-77` |
| Rate limit | **Yes** (016) | **Fail open + alert** (`ac_rate_limit_degraded_total` + WARNING) | Redis back → enforcement resumes | `ratelimit.py:82`, `deps.py:246-254` |
| Idempotency | **No** (in-process `set()`) | `409` retryable (independent of Redis) | n/a (no Redis dependency today) | `deps.py:315` |
| Spend cap | **No** (Postgres ledger) | **Fail closed** on ledger error (no paid call) | Postgres back → normal | `deps.py:172-189` |
| Spend *reservation* (Redis) | **Future** | MUST fail closed when built | — | umbrella data-model (deferred) |

## Redis blast-radius map (doc)

`AC_REDIS_URL` down affects **deny-set** (→ console/caller 401, fail closed) and **rate limiting** (→ fail open + alert) only. Idempotency and spend do not depend on Redis today (above). This map + the recovery column are the FR-025 deliverable.

## Tenant GUC under pooling (existing mechanism, newly proven)

`app.workspace_id` set via `set_config(..., is_local=true)` (= `SET LOCAL`) inside the request transaction (`tenancy.py:32`, `db.py:66`) — transaction-scoped, so a PgBouncer txn-mode pooled connection cannot carry it into the next request. 021 proves this with a cross-tenant negative test under an actual pooled engine (FR-026). No code change to the mechanism.

## No change

16/018 Redis structures, `members`/`api_tokens`/`group_members`, the runtime, and the authZ read path are all untouched.
