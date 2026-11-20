---
description: "Task list — 019 M5 OIDC Console Login + Session + CSRF"
---

# Tasks: M5 — OIDC Console Login + Session + CSRF

**Input**: Design documents from `/specs/019-m5-oidc-console-login/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/console-login.md · **Depends on 018** (deny-set already wired into `session_principal`, built)
**Tests**: REQUIRED — Constitution P9 mandates test-first (Red→Green→Refactor). All paths under `services/`.

**Single user story (US1).** MVP = US1 = the whole feature (auth-code login → signed session cookie → per-request validation + deny-set → CSRF → logout).
**Owner rulings (2026-11-12):** **D2 accepted** (dual-path `session_principal`: cookie OR provider), **D3 accepted** (add `authlib` +httpx, no hand-roll), **D4 confirmed** (no interactive login under the `dev` profile; tests use `X-Dev-Principal`). Session lifetime ≤1h is now **FR-029** (was cited as the umbrella's FR-014). `SameSite=Lax` pinned. No migration (stateless cookie, R4).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no incomplete deps)
- Paths are repo-relative; run commands from `services/`.

---

## Phase 1: Setup

- [x] T001 [P] Add `authlib` (and its transitive `httpx`) to the `[api]` extra in `services/pyproject.toml` (D3). Do not add `itsdangerous` — the session is signed with the already-present `pyjwt`.
- [x] T002 [P] Add session/OIDC settings to `services/api/settings.py`: `AC_SESSION_SECRET` (required when a login provider is active; no insecure default under `AC_ENV=prod`), `AC_SESSION_TTL_S` (default 3600, hard-capped ≤ 3600), `AC_OIDC_DISCOVERY_URL` (or reuse the existing `iam_*` issuer/jwks/audience), and session-cookie attributes (`Secure` on by default; relaxable only under dev). (`AC_REDIS_URL`/`AC_DENYSET_TTL_S` already present from 016/018 — do not re-add.)

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ Must complete before US1 can go Green. Scaffolding only — no migration (stateless session cookie).**

- [x] T003 [P] Create `services/api/auth/session.py` **stub**: `issue_session(subject, iss, aud) -> (cookie_value, csrf_token)` and `read_session(request) -> SessionClaims | None` signatures + `SessionClaims` shape (`sub`, `iss`, `aud`, `exp`, `csrf`); docstring states HS256-via-`pyjwt`, `exp ≤ AC_SESSION_TTL_S`, and the never-log-the-secret rule (FR-028). Bodies raise `NotImplementedError`.
- [x] T004 [P] Create `services/api/auth/oidc_login.py` **stub**: `begin_login()` and `complete_login(request) -> subject` signatures; docstring states PKCE S256 + `state` + `nonce`, id_token verified against the 012 `iam_issuer/iam_audience/iam_jwks_uri` trust config. Scaffold only.
- [x] T005 [P] Create `services/api/csrf.py` **stub**: a double-submit CSRF ASGI middleware class + the exemption allow-list (safe methods; `/v1/*` published plane; `/auth/login|callback`; `/healthz|/readyz|/metrics`). Scaffold the `__call__` passthrough; no enforcement yet (D5).
- [x] T006 [P] Create `services/api/routers/auth.py` **stub**: `router` with `GET /auth/login`, `GET /auth/callback`, `POST /auth/logout` returning `501`/placeholder; confirm the `auth.unauthorized` (401) and a `csrf.invalid` (403) error class exist in `services/api/errors.py` or add them.

**Checkpoint**: modules import cleanly; `auth.router` registers; no behavior yet.

---

## Phase 3: User Story 1 — Console users sign in through the org IdP (P2) 🎯 MVP

**Goal**: An unauthenticated user is redirected to the IdP (auth-code + PKCE), returns to a signed ≤1h session cookie validated server-side (sig + iss/aud/exp) and checked against the 018 deny-set on every request; state-changing requests require a matching CSRF token; logout ends the session. Fail-closed on deny-set match / Redis down; IdP-unreachable at login fails visibly (not fail-open).

**Independent Test**: stub-OIDC login → session cookie → protected route 200; tampered/expired cookie → 401; revoked subject → 401; POST without CSRF → 403; logout → re-login required.

### Tests for User Story 1 (write FIRST, confirm they FAIL on behavior) ⚠️

- [x] T007 [US1] Add a **stub OIDC IdP** fixture (in-process discovery doc + JWKS + token endpoint) + `fakeredis` + tenant-session scaffold in `services/tests/test_console_login_session_csrf.py` (import real app/deps; no behavior assertions) so Red tests fail on behavior, not import.
- [x] T008 [P] [US1] Red `test_session_cookie` codec tests in `services/tests/test_session_cookie.py`: `issue_session`→`read_session` round-trips claims; a tampered signature → None/reject; an expired `exp` → reject; `exp` ≤ 3600s from issue (FR-029).
- [x] T009 [US1] Red `test_authcode_login_establishes_session`: unauth protected route → `/auth/login` redirect; stub-OIDC `/auth/callback` → session cookie set → the protected route returns 200 (FR-017, AS1).
- [x] T010 [US1] Red `test_session_validated_each_request`: a tampered or expired session cookie → 401 on the next request (FR-018, AS2).
- [x] T011 [US1] Red `test_denied_subject_rejected_despite_valid_cookie`: a valid cookie whose subject is on the 018 deny-set → 401 (reuses the existing `session_principal` block, FR-018).
- [x] T012 [US1] Red `test_session_denyset_redis_down_fails_closed`: `AC_REDIS_URL` set but Redis raising during a cookie request → 401 (fail closed, FR-027). Also assert `AC_REDIS_URL` **unset** → no-op allow (dev parity).
- [x] T013 [US1] Red `test_csrf_required_on_state_change`: a POST without a matching `X-CSRF-Token`/`csrf` cookie → `403 csrf.invalid`; a GET is exempt (FR-019, AS3).
- [x] T014 [US1] Red `test_published_plane_exempt_from_csrf`: a published `/v1` caller-token POST (bearer plane, 018) is NOT CSRF-gated (FR-019 exemption, D5).
- [x] T015 [US1] Red `test_logout_ends_session`: `POST /auth/logout` clears the cookie; the next protected route → 401/redirect (FR-020, AS4).
- [x] T016 [US1] Red `test_session_secret_never_logged`: capture logs across login+callback+request; assert `AC_SESSION_SECRET` and the cookie value never appear in logs/trace/any response (FR-028, P5).
- [x] T017 [US1] Red `test_idp_unreachable_fails_visibly`: a callback with the IdP unreachable → visible `503`, session **not** established (no fail-open; edge case).

### Implementation for US1 (make the above Green, dependency order)

- [x] T018 [US1] Implement `services/api/auth/session.py`: `issue_session` signs an HS256 JWT (`AC_SESSION_SECRET`, `exp = now + AC_SESSION_TTL_S` capped ≤ 3600) + a bound CSRF token; `read_session` verifies signature + `iss`/`aud`/`exp` → `SessionClaims` (None on absent/invalid). Never log the secret/cookie. Depends on T002.
- [x] T019 [US1] Implement `services/api/auth/oidc_login.py` with authlib: `begin_login` → authorize URL with PKCE S256 + `state` + `nonce` stashed in a short signed temp cookie; `complete_login` validates `state`/`nonce`, exchanges `code`, verifies `id_token` against the 012 issuer/JWKS, returns the subject; IdP-unreachable → raise → router maps to 503. Depends on T001, T002.
- [x] T020 [US1] Implement `services/api/routers/auth.py`: `GET /auth/login` (302 to IdP via `begin_login`), `GET /auth/callback` (`complete_login` → `issue_session` → set session + csrf cookies → 302 to app), `POST /auth/logout` (clear cookies, 204). Under the `dev` profile `/auth/login` returns a clear "login not configured" error (D4). Depends on T018, T019.
- [x] T021 [US1] Add the cookie-first path to `session_principal` in `services/api/deps.py` (D2): if `read_session(request)` returns valid claims → build `Principal` from them; else fall back to the existing `provider.verify()`. The existing deny-set block (`deps.py:72-77`) runs for **both** paths unchanged. Depends on T018.
- [x] T022 [US1] Implement double-submit enforcement in `services/api/csrf.py`: on POST/PATCH/PUT/DELETE non-exempt routes, require the `csrf` cookie to match the `X-CSRF-Token` header → else `403 csrf.invalid`. Depends on T018 (csrf token binding).
- [x] T023 [US1] Wire it up in `services/api/main.py`: register the CSRF middleware (with the D5 exemption list) and `auth.router` in `create_app()`. Depends on T020, T022.

### Refactor (keep all US1 tests green)

- [x] T024 [US1] Extract cookie-attribute construction + the claims→`Principal` adapter into small pure helpers; confirm no secret in any log/cookie-debug; confirm `services/runtime/` untouched (P6).

**Checkpoint**: `pytest tests/test_console_login_session_csrf.py tests/test_session_cookie.py` green.

---

## Phase 4: Polish & Cross-Cutting

- [x] T025 Run full regression `pytest -q` — the existing bearer/dev-header suite stays green after the `session_principal` dual-path change (D2 regression).
- [x] T026 [P] `ruff check . && mypy .` clean (defer to CI if tools absent locally — note it honestly). **DEFERRED TO CI: `ruff`/`mypy` are not installed in `services/.venv`; not run locally. New files compile cleanly (`py_compile`) and the full pytest suite is green.**
- [x] T027 [P] Secret-scan: assert `AC_SESSION_SECRET` + the session cookie value never appear in logs, traces, or any envelope (P5/FR-028).
- [x] T028 Run `quickstart.md` validation (incl. the stub-OIDC login walkthrough). **Covered by the automated suite: `test_console_login_session_csrf.py` exercises the stub-OIDC login→callback→session→CSRF→logout walkthrough end-to-end offline; the quickstart's manual curl steps were not run by hand.**
- [x] T029 At DoD: update tracker memory `m5-security-split` (019 ✅) and reconcile the console-login/session/CSRF rows in `docs/backend-integration-status.md` + `docs/implementation-plan.md` (per update-docs-after-feature). Note that full FE login wiring remains M12.

---

## Dependencies & Execution Order

- **Setup (T001–T002)** → **Foundational (T003–T006)** → **US1 (T007–T024)** → **Polish (T025–T029)**.
- Within US1: **tests (T007–T017) before implementation (T018–T023)**; T018 → {T019, T021, T022}; T019 → T020; T023 after T020+T022; refactor T024 last.
- T002 (settings) blocks T018/T019. T018 (session codec) blocks T020/T021/T022.

### Parallel Opportunities

- T001 ∥ T002 (Setup). T003 ∥ T004 ∥ T005 ∥ T006 (Foundational, different files).
- T008 (separate codec file) ∥ the T009–T017 Red tests; T009–T017 share one file → write sequentially after the T007 scaffold.
- T026 ∥ T027 in Polish.

---

## Implementation Strategy

Single-story MVP, strictly test-first (Red before Green, P9). The session is a **stateless** signed cookie (R4 — no store, no migration); revocation reuses the 018 deny-set already wired into `session_principal` (D2 adds only the cookie branch). The whole flow is exercised offline with a **stub OIDC IdP** + `fakeredis` + the tenant test session — **no live IdP/paid call**. `authlib` is the one new dependency (D3, accepted); `services/runtime/` untouched (P6).
