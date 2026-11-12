# Phase 0 Research: 021 Redis-SPOF Degraded Mode + PgBouncer Transaction-Mode

Shared design lives in the umbrella: [015 research R5 (deny-set fail-closed) + R6 (PgBouncer txn-mode GUC)](../015-m5-security-completion/research.md) and the degraded-mode posture notes. This file records only the **code-grounded deltas** resolved while planning the 015f capstone.

## Resolved decisions

- **D-AlreadyBuilt — the headline behaviors exist; 021 validates them.** Verified on disk:
  - **Rate limit fail-open + alert (016):** `ratelimit.check` → `Decision(allowed=True, degraded=True)` on Redis error (`ratelimit.py:82`); `deps.rate_limit._rl` already calls `metrics.record_rate_limit_degraded()` (`ac_rate_limit_degraded_total`) + a throttled WARNING (`deps.py:246-254`).
  - **Deny-set fail-closed (018):** `denyset.is_denied` raises on a configured-Redis error (`denyset.py:48`); `session_principal` + `resolve_caller` map it to `401`.
  → FR-024's rate-limit and deny-set clauses need **tests**, not code (plan.md D1).
- **D-GUCMechanismExists — FR-026 is already the design (R6).** `tenancy.set_workspace_scope` uses `set_config('app.workspace_id', :ws, true)` (= `SET LOCAL`) inside `session.begin()` (`db.py:66`); its docstring already claims PgBouncer-txn-mode no-leak. → 021 adds the **PgBouncer deployment + a pooled negative test**, not a tenancy code change.
- **D-NoPgBouncer — the deployment lacks it.** `deploy/compose.yaml` wires the API straight to Postgres (`AC_APP_DATABASE_URL: ...@postgres:5432`); there is no pgbouncer service. → 021 adds one in transaction mode, app role through it, owner/migrations direct (plan.md D4).
- **D-IdempotencyInProcess — not Redis.** `deps.idempotency_guard` (`deps.py:315`) is a process-local `set()` returning `409 idempotency.in_progress`; durable Redis replay is explicitly deferred. → Redis-down does **not** weaken idempotency today; the `409` is the retryable fail-closed behavior. Documented against reality (plan.md D2).
- **D-SpendPostgres — not Redis.** `assert_within_spend_cap` (`deps.py:172`) sums the durable `runs.cost_usd` Postgres ledger and already fails closed on error (`deps.py:184-189`). The Redis spend *reservation* is future. Documented against reality (plan.md D2).

## Owner decisions (all ruled, 2026-11-12)

All plan decisions are now ruled (2026-11-12):
- ✅ **D1** — confirmed: the capstone is a validation/hardening/evidence feature (tests + docs + deploy), not a new-controls build.
- ✅ **D2** — document idempotency/spend degraded behavior against today's in-process/Postgres reality; the Redis idempotency/reservation store stays deferred (not built here).
- ✅ **D3** — a configured-but-unreachable Redis flips `/readyz` to `503` (consistent with deny-set fail-closed). `AC_REDIS_URL` unset → Redis not a readiness dependency, `/readyz` stays `200` (parity carve-out).
- Follow-through: the T014 decision record documents the D3-widened blast radius (console auth + readiness fail together on Redis loss — 021-M3).
