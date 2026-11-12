# Contract: OIDC console login + session + CSRF + group sync (015d / 015e)

New console-auth routes (via the `api/auth/` provider registry) plus the login-time group→members-store sync.

## OIDC authorization-code login

```
GET /auth/login            → 302 to IdP authorize endpoint (state + PKCE + nonce set)
GET /auth/callback?code&state
                           → validates state/nonce, exchanges code, verifies id_token (iss/aud/exp, JWKS),
                             establishes session cookie, 302 to the app
POST /auth/logout          → clears the session; 204; next protected route requires re-login (FR-020)
```

## Session cookie (set on callback)

- Signed, `HttpOnly`, `Secure`, `SameSite=Lax` (or stricter), lifetime **≤ 1h** (FR-014 floor source).
- Validated on **every** console request: signature + iss/aud/exp, **then** deny-set check
  (`deny:sub:{subject}`) before the handler runs (FR-018). Deny-set match or Redis-down → `401` (fail closed).

## CSRF (state-changing console routes)

- Double-submit: a CSRF cookie + matching `X-CSRF-Token` header required on POST/PATCH/PUT/DELETE console
  routes; missing/mismatch → `403 csrf.invalid` (FR-019). Safe (GET/HEAD) routes exempt.

## Login-time group → members-store sync (015e)

On successful callback (and on a scheduled refresh where the adapter supports `members_of`):
1. Read the user's native IdP groups via the active adapter (`groups_for(subject)`).
2. Resolve them through `workspaces.idp_binding` (name/GUID/attr → role + teams) per workspace.
3. **Upsert the user's `members.console_role` + `members.teams`** for each mapped workspace (the authority).
   No mapping match → no role/team written (fail-closed default, FR-022).
4. Upsert the `group_members` projection (`source='login_seen'`) for notification fan-out only (FR-023).

> No live per-request group→role path exists; `require_permission`/access-policy keep reading the members store
> (unchanged). This reconciles US5 with 012/013 and the claim-less Trellix-IAM token (research R-note).

## Error envelopes

- Unauthenticated protected route → `401 auth.unauthorized` (or 302 to login for browser nav).
- CSRF failure → `403 csrf.invalid`.
- IdP unreachable during login → user-visible retan error page / `503`, **not** fail-open (edge case).

## Acceptance (maps to FR-017..023, SC-006/007)

1. Unauthenticated → login flow → authenticated session reaches a protected route.
2. Every request validates session server-side + deny-set; expired/denied → `401`.
3. State-changing request without CSRF token → `403`.
4. Logout → session ended; protected route requires re-login.
5. Login with mapped IdP groups → members-store role/team updated to match (name/GUID/attr cases).
6. Login with no mapping → no role/team granted.
7. `group_members` projection updated on login; scheduled refresh updates it where `members_of` is supported.
