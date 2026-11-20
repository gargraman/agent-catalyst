# Phase 0 Research: 019 OIDC Console Login + Session + CSRF

Shared design lives in the umbrella: [015 research R4](../015-m5-security-completion/research.md) (OIDC login + ≤1h signed-cookie session + double-submit CSRF). This file records only the **code-grounded deltas** resolved while planning 019.

## Resolved decisions

- **D-NoMigration — the session is a stateless signed cookie, not a row.** Verified there is no session table/model on disk and none is needed: revocation is the existing Redis deny-set (018), and R4 explicitly rejects a server-side session store (it widens the Redis SPOF). So 019 adds **no** migration — unlike 018 (`0008`/`0009`).
- **D-SigningLib — reuse `pyjwt`, do not add `itsdangerous`.** The cookie is an **HS256 JWT** signed with `AC_SESSION_SECRET`. `pyjwt[crypto]` is already a direct dep (`pyproject [api]`), so the only genuinely new dependency is **`authlib`** (auth-code flow). Rationale: fewer new deps; pyjwt already does the callback `id_token` verify too. Alternative (`itsdangerous` opaque cookie) rejected — new dep and we'd still hand-roll claim encoding.
- **D-DenySetAlreadyWired — half of FR-018 exists.** `session_principal` (`deps.py:57-78`) already calls `denyset.is_denied(subject)` after `provider.verify()` and fails **closed** on a configured-Redis error (018). 019 inserts the **cookie identity path in front of** `provider.verify()` and **reuses** that deny-set block verbatim — it is not re-implemented.
- **D-SeamReuse — login reuses the 012 IdP trust config.** The authlib client verifies the callback `id_token` against the **same** `iam_issuer/iam_audience/iam_jwks_uri` the `trellix-iam` provider uses (`registry.py:38`, `jwt_provider.py`). Swapping IdP vendors stays a config change (FR-005). authlib adds discovery + PKCE + `state`/`nonce` + code→token exchange on top.
- **D-CookiePath — `session_principal` becomes dual-path (cookie OR provider).** Cookie present+valid → `Principal` from claims; else fall back to the existing bearer/dev provider path. Keeps the whole existing suite (which sends `X-Dev-Principal`/bearer) green with zero route changes. Alternative (a separate `console_principal` dep forked across ~40 routes) rejected as drift-prone. See plan.md D2.
- **D-CSRFMiddleware — one choke point, explicit exemptions.** Double-submit CSRF implemented as ASGI middleware (a new route can't forget it), exempting safe methods, the published `/v1` caller-token plane (bearer, not cookie → not CSRF-exposed), `/auth/login|callback`, and `/healthz|/readyz|/metrics`. See plan.md D5.

## Owner decisions (all ruled, 2026-11-12)

All plan decisions are now ruled (2026-11-12):
- ✅ **D2** — dual-path `session_principal` (cookie OR provider) accepted.
- ✅ **D3** — `authlib` accepted as a new dependency (hand-roll not taken).
- ✅ **D4** — no interactive login under the dev profile; tests keep using `X-Dev-Principal`.
- Traceability fix: session lifetime ≤1h is now **FR-029** in the spec (was cited as the umbrella's FR-014); `SameSite=Lax` pinned.
