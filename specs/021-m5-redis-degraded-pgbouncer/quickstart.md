# Quickstart / Validation: 021 Redis-SPOF Degraded Mode + PgBouncer Transaction-Mode

Shared design: [015 research R5/R6](../015-m5-security-completion/research.md) + [data-model Redis table](../015-m5-security-completion/data-model.md). Run from `services/` with the venv active.

## Setup

```bash
cd services && source .venv/bin/activate
# no alembic step — 021 adds no migration
pytest -q tests/test_redis_degraded_mode.py          # degraded-mode matrix (fakeredis forced down)
# PgBouncer txn-mode isolation (integration — needs the pooled deploy stack):
docker compose -f ../deploy/compose.yaml up -d postgres pgbouncer
pytest -q tests/test_pgbouncer_txn_isolation.py
```

The degraded-mode matrix uses `fakeredis` made to raise (or a dead `AC_REDIS_URL`) — deterministic, no live infra. The pooled no-leak test needs PgBouncer in transaction mode (added to `deploy/compose.yaml`).

## Validation scenarios (prove the feature)

1. **Deny-set fails closed** — Redis down → console **and** caller-token auth return `401` (0 requests through unchecked).
2. **Rate limit fails open + alert** — Redis down → request allowed, `ac_rate_limit_degraded_total` increments, a WARNING is logged.
3. **Spend cap fails closed** — a forced ledger error → the spend gate refuses before any paid call.
4. **Idempotency fails closed retryable** — an in-flight retry of the same key → `409` (independent of Redis).
5. **Recovery** — Redis comes back mid-run → controls resume normal behavior without a restart.
5b. **Readiness** — a configured-but-unreachable Redis → `/readyz` returns `503 {redis: down}` (LB/k8s drains the instance); `AC_REDIS_URL` unset → `/readyz` stays `200` (parity carve-out).
6. **Decision record** — the degraded-mode doc documents each control's behavior + recovery path, and each claim ties to the code (FR-025).
7. **No GUC leak under pooling** — two tenants reusing one pooled physical connection → 0 cross-tenant reads; no stale `app.workspace_id` (FR-026).

## Done-when

- The degraded-mode matrix + the PgBouncer isolation test green; full `pytest -q` green (016/018 unchanged).
- `ruff check . && mypy .` clean.
- The degraded-mode decision record merged under `docs/backend/`, cited by the milestone done-when (FR-025).
- `deploy/compose.yaml` runs the app role through PgBouncer (txn mode); owner/migration role stays direct.
- `docs/backend-integration-status.md` 021 rows flipped to BUILT; **M5 milestone closed** (capstone).
