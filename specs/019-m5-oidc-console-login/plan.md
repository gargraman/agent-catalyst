# Implementation Plan: M5 — OIDC Console Login + Session + CSRF

**Branch**: `feat/backend-m0-foundation` (feature dir `019-m5-oidc-console-login`) | **Date**: 2026-11-12 | **Spec**: [spec.md](./spec.md)

**Input**: `specs/019-m5-oidc-console-login/spec.md` · **Parent umbrella**: [`015-m5-security-completion`](../015-m5-security-completion/) (shared design — research R4, contracts/console-auth.md) · **Depends on**: 018 (per-request auth already checks the deny-set) · **Blocks**: 020 (the group→members-store sync runs on the `/auth/callback` hook this feature adds)

## Summary

Stand up **real console login** — an OIDC authorization-code flow that establishes a **server-validated, stateless session cookie** — plus **CSRF** protection on state-changing console routes. Today console auth is header/bearer only (`trellix-iam` JWT or the `X-Dev-Principal` dev header) resolved by `session_principal` (`deps.py:57`); there is **no browser login, no session cookie, and no CSRF** anywhere in the repo. Three load-bearing pieces: (1) `/auth/login` → `/auth/callback` → `/auth/logout` routes driving the auth-code exchange (via **authlib**, a new dependency) against the same issuer/JWKS config the `trellix-iam` provider already uses; (2) a **signed, HttpOnly/Secure/SameSite session cookie** (HS256 JWT via the already-present `pyjwt`, lifetime ≤ 1h) that `session_principal` validates on **every** console request — signature + `iss`/`aud`/`exp`, then the **existing** deny-set check (018) — before the handler runs; (3) **double-submit CSRF** on POST/PATCH/PUT/DELETE console routes (`403 csrf.invalid` on miss).

**Key reuse / deltas found in code (grounding the shared 015 design against what's actually on disk):**
- **The deny-set half of FR-018 is already built.** `session_principal` (`deps.py:57-78`) already calls `denyset.is_denied(principal.subject, None)` after `provider.verify()` and fails **closed** (401) on a configured-Redis error. 019 adds the **session-cookie** identity path *in front of* that provider call; the deny-set check is reused unchanged — not re-implemented.
- **No new session store, no new signing library.** The session is a **stateless signed cookie**, not a Redis/DB row (015 data-model §"Console session"; research R4 rejects a server-side store — it widens the Redis SPOF and the deny-set already gives immediate revocation). Signed as an **HS256 JWT with `pyjwt`**, which is **already a direct dep** (`pyproject [api]`), so no `itsdangerous`. Only **`authlib`** is a genuinely new dependency (auth-code + discovery + PKCE), and it pulls `httpx`.
- **The IdP seam (012) is reused, not forked.** `get_active_provider` (`registry.py:31`) + the `iam_issuer/iam_audience/iam_jwks_uri/iam_allowed_algs` settings already exist and already verify a real token (`jwt_provider.py`). authlib's OIDC client is configured from the **same** issuer/JWKS; the callback's `id_token` verification reuses that trust config. Swapping IdP vendors stays a config change (FR-005).
- **No migration.** The session is a cookie; nothing persists. (Contrast 018, which added `0008`/`0009`.)
- **Frontend already has the seam.** The dev-auth bridge (`frontend/src/lib/api/client.ts:24` `devAuthHeaders`) is explicitly a placeholder ("Real OIDC replaces this later (P1)"). 019 is the backend half; full FE login wiring is **M12** (spec Assumptions) — this plan does not touch the console UI beyond leaving the cookie contract in place.

## Technical Context

**Language/Version**: Python 3.12 (`services/`)
**Primary Dependencies**: FastAPI, Starlette, Pydantic v2; **`authlib` (NEW)** for the OIDC auth-code client (discovery, PKCE, state/nonce, token exchange) + its transitive `httpx`; **`pyjwt[crypto]` (existing)** for both the callback `id_token` verify (reusing 012's JWKS resolver) and the session-cookie HS256 sign/verify; `redis` (async) for the deny-set (reused from 016/018). No new DB driver.
**Storage**: **None added.** Session is a stateless signed cookie; revocation via the existing Redis deny-set (018). No session table, no migration.
**Testing**: pytest async + `fakeredis`; deterministic security TDD only (no model/prompt behavior → no eval layer, per the 015 plan). A stub OIDC IdP (local fixture: discovery doc + JWKS + token endpoint served in-process, or authlib's test seams) keeps the suite offline — **no live IdP call** (Constitution: paid/live calls are not ordinary tests).
**Project Type**: Web service (`services/api`)
**Performance Goals**: Per-request console auth adds one HMAC verify (cookie) + the already-present pipelined deny-set `EXISTS` (018). Login/callback are rare, interactive, and may touch the IdP (discovery/JWKS cached).
**Constraints**: session lifetime **≤ 1h** (FR-014 floor; bounds the 018 deny-set TTL); session validation + deny-set **fail closed** (FR-027 — deny-set match or Redis down → 401); the session secret **never** leaks to logs/traces/exports (FR-028, P5); IdP-unreachable during login → user-visible failure, **not** fail-open (edge case); `services/runtime/` untouched (P6).
**Scale/Scope**: internal pilot; 3 new `/auth/*` routes + a session-cookie codec + a CSRF guard + the `session_principal` cookie path. Browser console is one tenant deployment.

## Decisions requiring review (the load-bearing choices)

> These are the calls where the spec/shared-design leave room, or where fidelity to it has a real cost. Flagged for critical review before tasks/code.

**D1 — Session cookie is a stateless HS256 JWT (pyjwt), not a server-side session store, not `itsdangerous`.**
Mint a signed cookie carrying `{sub, iss, aud, exp (≤1h), csrf}` using `pyjwt` (already a dep) with a new `AC_SESSION_SECRET`. Validated per request: HMAC + `iss`/`aud`/`exp`, then the existing deny-set check. *Rationale:* research R4 rejects a server-side store (widens the Redis SPOF; the deny-set already gives immediate revocation); pyjwt is already present so no new signing lib. *Alternative rejected:* `itsdangerous`-signed opaque cookie (new dep, and we'd still need claim encoding) or a Redis/DB session table (R4-rejected). *Confirm:* the ≤1h lifetime + deny-set revocation is the accepted trade for statelessness.

**D2 — The cookie plugs into `session_principal` as a path *in front of* the IdP provider; existing bearer/dev tests stay green. — ✅ RULED (owner, 2026-11-12): accept the dual-path.**
`session_principal` becomes: if a valid session cookie is present → build `Principal` from its claims; **else** fall back to `get_active_provider().verify()` (the current bearer/dev path). The deny-set check then runs for **both** paths (unchanged). *Rationale:* browsers get the cookie path; programmatic/API callers and the whole existing test suite (which sends `X-Dev-Principal`/bearer) keep working with zero changes. *Alternative rejected:* a separate `console_principal` dependency wired onto every console route — forks ~40 route signatures and risks drift between the two.

**D3 — authlib is a new dependency; the login flow reuses the 012 issuer/JWKS trust config. — ✅ RULED (owner, 2026-11-12): accept `authlib`.**
Add `authlib` to `pyproject [api]` for the auth-code client (discovery, PKCE S256, `state`+`nonce`, code→token exchange). The returned `id_token` is verified against the **same** `iam_issuer/iam_audience/iam_jwks_uri` the `trellix-iam` provider uses (`registry.py:38`). *Rationale:* hand-rolling OIDC code exchange reinvents 012 and is error-prone (R4). *Alternative rejected:* build the exchange on raw `httpx` + `pyjwt` (the hand-roll fallback — **not taken**; owner accepted the dependency). One new runtime dependency (+ transitive `httpx`).

**D4 — Login routes require a configured OIDC provider; under `AC_ENV`=dev the flow is disabled and tests use the existing dev header. — ✅ RULED (owner, 2026-11-12): confirmed.**
`/auth/login|callback` only function when an OIDC login provider is configured (the pilot's `trellix-iam` issuer, or a `generic-oidc`). Under the `dev` profile there is no interactive IdP, so the dev-header path (`DevHeaderProvider`, `dev_provider.py`) remains the test identity source and `/auth/login` returns a clear "not configured" error rather than pretending. *Rationale:* mirrors `registry.py`'s fail-closed "dev disabled under prod" posture, inverted — real login needs a real IdP. (A stub-OIDC dev login for manual console testing is out of scope for this slice.)

**D5 — CSRF via middleware over the state-changing console surface, with explicit exemptions.**
Double-submit: a readable (non-HttpOnly) `csrf` cookie bound to the session + a matching `X-CSRF-Token` header, required on POST/PATCH/PUT/DELETE **console** routes; mismatch/absent → `403 csrf.invalid`. Implement as **ASGI middleware** (one choke point — a new route can't forget it) that **exempts**: safe methods (GET/HEAD/OPTIONS), the published caller-token plane (`/v1/...`, 018 — bearer, not cookie, so not CSRF-exposed), `/auth/login|callback`, and `/healthz|/readyz|/metrics`. *Alternative rejected:* a per-route `Depends(require_csrf)` — easy to omit on a new route (the exact failure CSRF guards against). *Confirm:* middleware + an explicit exemption allow-list vs. per-route dependency.

## Constitution Check

*GATE: pre-Phase 0 + re-checked post-Phase 1.*

| Principle | Status | Notes |
|-----------|--------|-------|
| P1 outcome | ✅ | Real IdP login replaces the mocked identity; measurable SC-006 (login reaches a protected route; unauthenticated/CSRF-missing rejected 100%). No LLM. |
| P2 scope/phases | ✅ | Bounded slice of the owner-approved M5 split (015d). Full FE login wiring explicitly deferred to M12 (spec). |
| **P3 deterministic fail-closed** | ✅ | Session validation + deny-set are server-side, deterministic, **fail closed** (401) on deny-set match or Redis down; IdP-unreachable at login fails to a visible error, never fail-open. Model instructions enforce nothing here. |
| P4 evidence | ✅ (N/A judge) | No model/prompt behavior; deterministic TDD. |
| **P5 data/tenant** | ✅ | `AC_SESSION_SECRET` + the session JWT never logged/traced/exported (secret-scan test, FR-028); cookie is `HttpOnly`/`Secure`/`SameSite`; workspace still resolved server-side by `resolve_workspace` (unchanged). |
| P6 reproducible | ✅ | API-layer only; `services/runtime/` untouched; no snapshot/invoke contract change. |
| P7 providers/cost | ✅ | No provider/cost contract change. authlib is transport/flow only; cost profiles untouched. |
| P8 operate/visible | ✅ | Login failure + revocation (401) are explicit; deny-set-down → fail-closed surfaces as 401s; the Redis-SPOF blast radius is the explicit subject of **021**. |
| P9 test-first | ✅ | Red→Green→Refactor; stub-OIDC + `fakeredis`, offline; no live IdP call in the suite. |

**Gate: PASS** — one new dependency (authlib, D3) recorded in Complexity Tracking; no principle deviation.

## Design

### Components

- **`api/auth/session.py`** (NEW): the session-cookie codec. `issue_session(subject, iss, aud) -> (cookie_value, csrf_token)` signs an HS256 JWT (`AC_SESSION_SECRET`, `exp = now + AC_SESSION_TTL_S ≤ 3600`) and a bound CSRF token; `read_session(request) -> SessionClaims | None` verifies signature + `iss`/`aud`/`exp` and returns claims (None if absent/invalid — caller decides 401). Never logs the secret or the cookie value (P5).
- **`api/auth/oidc_login.py`** (NEW): the authlib auth-code client. `begin_login()` → authorize URL with PKCE S256 + `state` + `nonce` (stashed in a short, signed temp cookie); `complete_login(request)` validates `state`/`nonce`, exchanges `code`, verifies the `id_token` against the 012 issuer/JWKS trust config, returns the subject. Configured from `settings.iam_*` (reused) + discovery URL.
- **`api/routers/auth.py`** (NEW): `GET /auth/login` (302 to IdP), `GET /auth/callback` (validate → `issue_session` → set cookies → 302 to app), `POST /auth/logout` (clear cookies; 204). Error envelopes per the contract (unreachable IdP → `503`, not fail-open).
- **`api/deps.py`** CHANGE: `session_principal` gains the **cookie-first** path (D2) in front of `provider.verify()`; the existing deny-set block (`deps.py:72-77`) is reused verbatim for both paths. No change to `resolve_workspace`/`tenant_db`/`require_permission`.
- **`api/csrf.py`** + **`api/main.py`** CHANGE (NEW middleware): double-submit CSRF over state-changing console routes with the D5 exemption list; registered in `create_app()`.
- **`api/settings.py`** CHANGE: `AC_SESSION_SECRET` (required when a login provider is active; no insecure default under prod), `AC_SESSION_TTL_S` (default 3600, hard-capped ≤ 3600), `AC_OIDC_DISCOVERY_URL` (or reuse `iam_*`), cookie attributes (`Secure` on by default; relaxable only under dev). `AC_REDIS_URL`/`AC_DENYSET_TTL_S` already present.
- **`pyproject.toml`** CHANGE: add `authlib` (and its `httpx`) to the `[api]` extra (D3).

### Pipeline position (a console request, post-019)

`CSRF middleware` (403 before routing on a bad state-changing request) → `session_principal` (**cookie** → claims, else provider.verify; then deny-set → 401) → `resolve_workspace` (403) → `require_permission` (403) → `rate_limit` (429) → handler. The only insertions are the CSRF middleware and the cookie branch; everything downstream is unchanged.

## TDD mapping (acceptance → Red test → command)

Target file: `tests/test_console_login_session_csrf.py` (+ a focused `tests/test_session_cookie.py` for the codec).

| Acceptance / FR | Red test | Command |
|-----------------|----------|---------|
| FR-017, AS1 | unauth protected route → login redirect; stub-OIDC callback → session cookie → protected route 200 | `::test_authcode_login_establishes_session` |
| FR-018, AS2 | every request validates sig+iss/aud/exp; tampered/expired cookie → 401 | `::test_session_validated_each_request` |
| FR-018 (deny-set reuse) | revoked subject (deny-set) with a valid cookie → 401 (reuses 018 path) | `::test_denied_subject_rejected_despite_valid_cookie` |
| FR-027 (fail closed) | configured Redis down during a cookie request → 401, not allowed | `::test_session_denyset_redis_down_fails_closed` |
| FR-019, AS3 | POST without `X-CSRF-Token`/cookie mismatch → `403 csrf.invalid`; GET exempt | `::test_csrf_required_on_state_change` |
| FR-019 (plane exempt) | published `/v1` caller-token POST is NOT CSRF-gated (bearer plane) | `::test_published_plane_exempt_from_csrf` |
| FR-020, AS4 | logout clears cookie; next protected route → 401/redirect | `::test_logout_ends_session` |
| FR-014 | session `exp` ≤ 3600s from issue | `::test_session_ttl_at_most_one_hour` |
| FR-028/P5 | `AC_SESSION_SECRET` + cookie value never in logs/trace/response (capture + scan) | `::test_session_secret_never_logged` |
| Edge | IdP unreachable at callback → visible 503, session NOT established (no fail-open) | `::test_idp_unreachable_fails_visibly` |
| D2 regression | existing bearer/dev-header routes unchanged (full suite green) | `pytest -q` |

Lint/type: `ruff check . && mypy .`. Stub OIDC + `fakeredis` only — no live IdP/paid call.

## Project Structure

```text
services/
├── api/
│   ├── auth/session.py          # NEW — HS256 session-cookie codec (issue/read), CSRF token binding
│   ├── auth/oidc_login.py       # NEW — authlib auth-code client (PKCE/state/nonce, id_token verify via 012 JWKS)
│   ├── routers/auth.py          # NEW — /auth/login, /auth/callback, /auth/logout
│   ├── csrf.py                  # NEW — double-submit CSRF ASGI middleware + exemption allow-list
│   ├── deps.py                  # CHANGE — session_principal cookie-first path (reuses the 018 deny-set block)
│   ├── main.py                  # CHANGE — register CSRF middleware + auth.router
│   └── settings.py              # CHANGE — AC_SESSION_SECRET, AC_SESSION_TTL_S, AC_OIDC_DISCOVERY_URL, cookie attrs
├── pyproject.toml               # CHANGE — add authlib (+ httpx) to [api]
└── tests/
    ├── test_console_login_session_csrf.py   # NEW (stub OIDC + fakeredis + tenant session)
    └── test_session_cookie.py               # NEW (codec: sign/verify, exp, tamper)
```

**Structure Decision**: fill the existing auth seam, don't fork. The cookie path lives *inside* `session_principal` (D2) so no console route changes; the deny-set (018) and IdP trust config (012) are reused, not re-implemented. No migration — the session is stateless by design (R4). `services/runtime/` untouched (P6).

## Complexity Tracking

| Deviation | Why needed | Simpler alternative rejected |
|-----------|-----------|------------------------------|
| New runtime dependency `authlib` (+ `httpx`) (D3) | OIDC auth-code with PKCE/state/nonce/discovery is security-critical and error-prone to hand-roll; R4 selected authlib | Hand-rolled `httpx`+`pyjwt` exchange — more code, more ways to get PKCE/nonce/`state` wrong; available as the fallback if the owner rejects the dep |
| `session_principal` dual-path (cookie OR provider) (D2) | Browsers need a cookie; the existing suite + programmatic callers need the bearer/dev path to keep working | A separate `console_principal` dep forked across ~40 routes — higher drift risk and churn than one branch in the shared resolver |
| CSRF as middleware, not per-route dep (D5) | A new state-changing route must not be able to silently skip CSRF | Per-route `Depends(require_csrf)` — the omission-on-a-new-route failure is exactly the risk CSRF defends against |
