# Quickstart / Validation: 018 Caller-Token Plane + Deny-Set

Contract: [015 contracts/caller-token.md](../015-m5-security-completion/contracts/caller-token.md). Run from `services/` with the venv active.

## Setup

```bash
cd services && source .venv/bin/activate
alembic upgrade head          # applies 0008_api_tokens
pytest -q tests/test_caller_token_denyset.py tests/test_migration_0008.py
```

Tests use `fakeredis` (no live Redis) and the tenant test session (no live Postgres beyond the test DB) — deterministic, no live/paid calls.

## Validation scenarios (prove the feature)

1. **Mint shows secret once** — `POST /api/v1/agents/{id}/tokens` returns the full secret; a subsequent `GET` lists prefix only; the DB row holds `token_hash`+`prefix`, never the secret.
2. **Revoke is immediate** — valid token invoke `200`; `DELETE /api/v1/tokens/{id}`; next invoke `401`.
3. **In-flight cut** — start a streaming invoke, revoke mid-stream; the stream emits an `error` frame and stops within `AC_DENYSET_STREAM_RECHECK_S`.
4. **Admin force-logout** — `POST /api/v1/org/revoke-user {subject}`; the subject's **console session** and a **live caller token** are both `401` on their next request.
5. **Fail closed** — with `AC_REDIS_URL` set but Redis unreachable, an auth decision returns `401` (not allowed through).
6. **Expiry** — a token past `expires_at` → `401`.
7. **RBAC** — builder cannot mint/revoke (`403`); non-admin cannot `revoke-user` (`403`).
8. **One code path** — the `/v1` published route and `/api/v1` console route yield identical frames for the same snapshot.

## Done-when

- All TDD-mapping tests (plan.md) green; full `pytest -q` green (no regression in the 8 `session_principal` callers after the deny-set check).
- `ruff check . && mypy .` clean.
- Secret-scan over test logs confirms SC-005 (no secret leak).
- `docs/backend-integration-status.md` 018 rows flipped to BUILT (per the milestone done-when).
