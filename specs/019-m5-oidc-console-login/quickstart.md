# Quickstart / Validation: 019 OIDC Console Login + Session + CSRF

Contract: [console-login.md](./contracts/console-login.md) (+ shared [015 contracts/console-auth.md](../015-m5-security-completion/contracts/console-auth.md)). Run from `services/` with the venv active.

## Setup

```bash
cd services && source .venv/bin/activate
pip install -e '.[api]'        # pulls the new authlib (+ httpx) dependency
# no alembic step — 019 adds no migration (stateless session cookie)
pytest -q tests/test_console_login_session_csrf.py tests/test_session_cookie.py
```

Tests use a **stub OIDC IdP** (in-process discovery doc + JWKS + token endpoint) and `fakeredis` (no live Redis) — deterministic, **no live/paid IdP call**.

## Validation scenarios (prove the feature)

1. **Auth-code login** — unauthenticated protected route → `/auth/login` redirect; stub-OIDC callback → session cookie set → the protected route returns `200`.
2. **Per-request validation** — a tampered or expired session cookie → `401` on the next request.
3. **Deny-set reuse** — a valid cookie whose subject is on the deny-set (018 admin revoke) → `401` (fail closed; reuses the existing `session_principal` block).
4. **Fail closed** — with `AC_REDIS_URL` set but Redis unreachable, a cookie request → `401` (not allowed through).
5. **CSRF** — a POST without a matching `X-CSRF-Token`/`csrf` cookie → `403 csrf.invalid`; a GET is exempt; a published `/v1` caller-token POST is exempt.
6. **Logout** — `POST /auth/logout` clears the cookie; the next protected route requires re-login.
7. **Lifetime** — the issued session `exp` is ≤ 3600s from issue.
8. **No secret leak** — `AC_SESSION_SECRET` and the cookie value never appear in logs/traces/responses (capture + scan).
9. **IdP unreachable** — a callback with the IdP down → visible `503`, session **not** established.

## Done-when

- All TDD-mapping tests (plan.md) green; full `pytest -q` green (the existing bearer/dev-header suite unchanged — D2 regression).
- `ruff check . && mypy .` clean.
- Secret-scan over test logs confirms FR-028 (no session-secret/cookie leak).
- `docs/backend-integration-status.md` 019 rows flipped to BUILT (per the milestone done-when).
