# Contract: 019 OIDC console login + session + CSRF

Feature-scoped contract. Authoritative shared contract: [015 contracts/console-auth.md](../../015-m5-security-completion/contracts/console-auth.md) (login routes, session cookie, CSRF, error envelopes). This file records only the **019 realization deltas**; the login-time group→members-store sync in that shared contract is **020**, not 019.

## Routes (NEW — `api/routers/auth.py`)

```
GET  /auth/login            → 302 to the IdP authorize endpoint (PKCE S256 + state + nonce in a signed temp cookie)
GET  /auth/callback?code&state
                            → validate state/nonce, exchange code, verify id_token (iss/aud/exp, 012 JWKS),
                              issue the session cookie + csrf cookie, 302 to the app
POST /auth/logout           → clear the session + csrf cookies; 204; next protected route requires re-login (FR-020)
```

- `/auth/login` + `/auth/callback` require a configured OIDC login provider. Under the `dev` profile (no interactive IdP) `/auth/login` returns a clear "login not configured" error; tests authenticate via the existing `X-Dev-Principal` dev header (plan.md D4).

## Session cookie (set on callback)

- HS256 JWT (`pyjwt`, `AC_SESSION_SECRET`), `HttpOnly`, `Secure`, **`SameSite=Lax`** (pinned — Lax, not Strict, so the IdP-redirect return navigation keeps the cookie), lifetime **≤ 1h** (`AC_SESSION_TTL_S` ≤ 3600, FR-029).
- Validated on **every** console request by `session_principal`: signature + `iss`/`aud`/`exp`, **then** the existing deny-set check (`deny:sub:{subject}`, 018). Deny-set match or Redis-down → `401` (fail closed, FR-027).

## CSRF (state-changing console routes)

- Double-submit: a readable `csrf` cookie + a matching `X-CSRF-Token` header on POST/PATCH/PUT/DELETE console routes; missing/mismatch → `403 csrf.invalid` (FR-019). Implemented as middleware exempting safe methods, the published `/v1` caller-token plane, `/auth/login|callback`, and `/healthz|/readyz|/metrics` (plan.md D5).

## Error envelopes

- Unauthenticated protected route → `401 auth.unauthorized` (browser navigation may 302 to `/auth/login`).
- CSRF failure → `403 csrf.invalid`.
- IdP unreachable during login → visible error page / `503`, **not** fail-open (edge case).

## Acceptance (maps to FR-017..020, FR-027/028, SC-006)

1. Unauthenticated → login flow → authenticated session reaches a protected route.
2. Every request validates the session server-side + deny-set; expired/tampered/denied → `401`.
3. State-changing request without a valid CSRF token → `403`; the published `/v1` plane is exempt.
4. Logout → session ended; protected route requires re-login.
5. Session `exp` ≤ 1h; `AC_SESSION_SECRET`/cookie never leak to logs/traces/exports.
6. IdP unreachable at callback → visible failure, session **not** established (no fail-open).
