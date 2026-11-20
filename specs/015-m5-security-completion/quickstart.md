# Quickstart / Validation Guide: M5 Security Completion

Per-sub-feature validation. This is a **run/validation guide**, not implementation — bodies, migrations, and
full test suites belong in `tasks.md` + implementation. All checks are deterministic; **no live provider
calls**. Run from `services/`.

## Prerequisites

```bash
cd services
# Python env already set up (.venv). Postgres + Redis available for integration tests:
#   AC_DATABASE_URL / AC_APP_DATABASE_URL  → test Postgres (RLS)
#   AC_REDIS_URL                           → test Redis (limiter + deny-set). Unset → limiter no-ops (dev parity)
#   AC_ENV=dev  AC_AUTH_PROVIDER=dev       → dev auth for tests (prod disables the dev header)
```

Focused test command pattern (per Constitution P9 — name the command in each task):

```bash
.venv/bin/pytest tests/<file>::<test> -q      # Red → Green per behavior
.venv/bin/pytest -q                            # full regression before DoD
.venv/bin/ruff check . && .venv/bin/mypy .     # lint/type gates (match repo config)
```

## 015a — Rate limiting (F5)

```bash
.venv/bin/pytest tests/test_rate_limit.py -q
```
Expected: N+1 requests in a window → `429` + `Retry-After`/`X-RateLimit-*`, no provider call; second token
unaffected; window reset re-allows; **Redis down → allowed + alert (fail open)**.

## 015b — SSRF / egress guard (F19)

```bash
.venv/bin/pytest tests/test_egress_ssrf.py -q
```
Expected: egress to private/loopback/link-local/metadata IP → refused, 0 bytes sent; allowed domain →
internal-redirect → refused; hostname resolving to private IP (rebind) → refused (validated on connected IP);
refusal logged without secrets.

## 015c — Caller token + deny-set

```bash
.venv/bin/pytest tests/test_caller_token_denyset.py -q
```
Expected: mint returns secret once (only hash+prefix persist; secret absent from logs — secret-scan assertion);
revoke → next invoke `401`; `revoke-user` → active session + token `401` next request **and in-flight stream
cut**; Redis down → `401` (fail closed); expired token → `401`.

## 015d — OIDC login + session + CSRF

```bash
.venv/bin/pytest tests/test_oidc_login_session_csrf.py -q
```
Expected (against a stub IdP): auth-code login → authenticated session on a protected route; per-request
iss/aud/exp + deny-set validation; state-changing route without CSRF token → `403`; logout → re-login required.

## 015e — Group → members-store sync + projection

```bash
.venv/bin/pytest tests/test_group_sync_projection.py -q
```
Expected: login with mapped groups → `members.console_role`/`teams` updated (name/GUID/attr cases); no mapping
→ no role/team; `group_members` projection upserted (`login_seen`); scheduled refresh updates it where
`members_of` is supported; **no live per-request group→role path exists**.

## 015f — Redis-SPOF degraded-mode + PgBouncer txn-mode

```bash
.venv/bin/pytest tests/test_redis_degraded_pgbouncer.py -q
```
Expected: with Redis down — deny-set + spend reservation **fail closed**, idempotency fails closed (retryable),
rate limiting **fails open + alert**; blast-radius doc present; under PgBouncer transaction mode the tenant GUC
does not leak across pooled connections (cross-tenant negative test = 0 rows).

## Milestone done-when (all sub-features)

- All six focused suites green + full `pytest` regression green.
- Secret-scan: no token/session secret in logs/traces/exports (SC-005).
- Cross-tenant negatives green under pooled config (SC-008/009).
- `docs/backend-integration-status.md` reconciled: M5 rows flipped from DEFERRED/❌ to BUILT with feature refs
  (per the "update docs after feature" rule); `docs/implementation-plan.md` M5 section marked complete.
