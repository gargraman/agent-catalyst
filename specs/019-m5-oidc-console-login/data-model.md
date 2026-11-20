# Phase 1 Data Model: 019 OIDC Console Login + Session + CSRF

Authoritative shapes are the shared umbrella: [015 data-model — "Console session"](../015-m5-security-completion/data-model.md). This file records the 019-specific **codec + state** decisions only.

## No persistent entities

019 adds **no Postgres table and no migration**. The session is stateless; revocation is the existing Redis deny-set (018). This is a deliberate R4 decision (a server-side session store would widen the Redis SPOF).

## Session cookie (stateless, signed)

Per [015 data-model §"Console session"](../015-m5-security-completion/data-model.md): a signed, `HttpOnly`, `Secure`, **`SameSite=Lax`** (pinned) cookie, lifetime **≤ 1h** (`AC_SESSION_TTL_S`, hard-capped at 3600; FR-029).

**019 realization (HS256 JWT via `pyjwt`):**

| Claim | Source | Notes |
|-------|--------|-------|
| `sub` | verified `id_token` subject | becomes `Principal.subject` |
| `iss` / `aud` | session issuer/audience | validated on every request (FR-018) |
| `exp` | `now + AC_SESSION_TTL_S` | ≤ 3600 (FR-014 floor; bounds the 018 deny-set TTL) |
| `csrf` | random, bound to this session | double-submit binding (see below) |

- **Validation (every console request):** HMAC signature (`AC_SESSION_SECRET`) + `iss`/`aud`/`exp`, **then** `denyset.is_denied(sub)` (reused from 018) → deny-set match or Redis-down → `401` (fail closed, FR-027).
- **Secret handling:** `AC_SESSION_SECRET` + the cookie value are **never** logged/traced/exported (FR-028, P5). No insecure default under a prod profile.

## CSRF token (double-submit)

A second, **readable** (non-HttpOnly) `csrf` cookie carrying the session's `csrf` value, plus an `X-CSRF-Token` request header that must match it on state-changing console routes. Not persisted; derived from / bound to the session JWT. Mismatch/absent → `403 csrf.invalid` (FR-019).

## Transient login state

`state` + `nonce` + PKCE verifier for an in-flight auth-code login live in a short-lived, signed temp cookie between `/auth/login` and `/auth/callback` — not persisted server-side.

## No change

`members`, `group_members` (020), `api_tokens` (018), `workspaces` are not touched by 019. The caller-token plane (018) and the IdP provider registry (012) are **reused**, not modified beyond the `session_principal` cookie branch.
