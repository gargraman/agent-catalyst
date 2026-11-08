---
description: "Task list for feature 012 — Pluggable IdP Bearer-Token Authentication Adapter"
---

# Tasks: Pluggable IdP Bearer-Token Authentication Adapter

**Input**: Design documents from `specs/012-pluggable-idp-auth/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/auth-provider.md, quickstart.md
**Tests**: REQUIRED — the spec mandates TDD with genuine behavioral REDs (FR-015; constitution P9).
**Organization**: by user story (US1/US2 = P1, US3 = P2). Run from `services/` via `.venv/bin/python -m pytest`.

## Format: `[ID] [P?] [Story] Description`
- **[P]**: parallelizable (different files, no incomplete-task dependency)
- Paths are repo-relative and exact.

---

## Phase 1: Setup (shared infrastructure)

- [X] T001 Add `pyjwt[crypto]` to `services/pyproject.toml` under `[project.optional-dependencies] api` and install into `services/.venv` (research R1; verify `import jwt` + `from jwt import PyJWKClient`).
- [X] T002 [P] Create the auth package skeleton `services/api/auth/__init__.py` (empty exports stub) per plan Project Structure.
- [X] T003 [P] Add auth settings to `services/api/settings.py`: `env` (`AC_ENV`, default `dev`), `auth_provider` (`AC_AUTH_PROVIDER`, default `dev`), `iam_issuer` (`AC_IAM_ISSUER`, default `https://preprod.iam.cloud.trellix.com/iam/v1.0`), `iam_audience` (`AC_IAM_AUDIENCE`, default `mcafee`), `iam_jwks_uri` (`AC_IAM_JWKS_URI`, default `https://preprod.iam.cloud.trellix.com/iam/v1.0/jwks`), `iam_allowed_algs` (`AC_IAM_ALGS`, default `RS256`, csv), `iam_clock_skew_s` (`AC_IAM_CLOCK_SKEW_S`, default `60`, bounded ≤120) — per data-model Settings table. All defaults non-secret.
- [X] T004 [P] Add test helpers to `services/tests/_api_helpers.py`: `make_rsa_keypair()`, `local_jwks(pub)` (JWKS dict with `kid`), and `mint_test_jwt(*, kid, key, sub, aud="mcafee", iss=<test-iss>, scope="xdr.alr.rw", exp_delta=600, alg="RS256", **overrides)` for locally-signed tokens — no network, no secret (FR-015). Leave `dev_headers` unchanged.

---

## Phase 2: Foundational (the pluggable seam — BLOCKS all stories; must keep the existing suite green)

**Goal**: stand up the provider abstraction + registry and route `session_principal` through it with the `dev`
provider as default, so existing behavior is preserved byte-for-byte.

- [X] T005 Define the seam in `services/api/auth/base.py`: `AuthError` (maps to 401), `AuthnResult` dataclass (`subject`, `scopes: tuple[str,...]`, `issuer: str`), and the `IdPProvider` Protocol with `id: str` and `async def verify(request) -> Principal` (contract §2; MUST raise `AuthError` on failure, never a partial Principal, never log the token).
- [X] T006 Implement `DevHeaderProvider` in `services/api/auth/dev_provider.py` — verbatim current `X-Dev-Principal` JSON behavior → `Principal{subject, workspace_claims}` (research R4). `id = "dev"`.
- [X] T007 Implement `get_active_provider(settings)` in `services/api/auth/registry.py` — return the provider named by `auth_provider`; **fail closed** (raise a clear, secret-free error) when it names `dev` while `env == "prod"`, or names an unknown/misconfigured provider (research R5 / FR-008). Single selection point.
- [X] T008 Extend the `Principal` dataclass in `services/api/deps.py` with `scopes: tuple[str,...] = ()` and `issuer: str | None = None` (data-model) — additive, defaults keep existing construction valid.
- [X] T009 Rewire `session_principal` in `services/api/deps.py` to delegate to `get_active_provider(get_settings()).verify(request)` (inject `Request`); default `dev` provider reproduces today's behavior. Preserve the `AuthRequired`→401 mapping.
- [X] T010 Regression gate: run `cd services && .venv/bin/python -m pytest -q` — confirm the existing ~423 tests stay green (behavior-preserving seam; SC-006). Do not proceed until green.

---

## Phase 3: User Story 1 — Bearer token cryptographically verified before access (P1) 🎯 MVP

**Goal**: a real JWT is verified (sig/iss/aud/exp/nbf/alg/kid) before any resource; valid token + membership →
access; everything else → fail-closed 401/403.
**Independent test**: local-keypair signed token resolves to a Principal and (with a seeded member) accesses a
route; forged/expired/wrong-aud/`alg=none`/non-member variants are rejected.

### Tests (write first — RED)

- [X] T011 [P] [US1] Unit tests `services/tests/test_auth_jwt_provider.py` (local keypair, no network): valid RS256 token → `Principal{subject, scopes, issuer}`; tampered signature → `AuthError`; `aud` mismatch; `iss` mismatch; `exp` past-skew; `nbf`/`iat` future-skew; `alg=none`; HS256-where-RS256; unknown `kid` after one refresh; missing/malformed `Authorization`; and assert the raw token value never appears in the raised error (contract §5 items 1–11).
- [X] T012 [P] [US1] Integration tests `services/tests/test_api_auth_bearer.py` (ASGI client, `AC_AUTH_PROVIDER=trellix-iam` with injected local JWKS): the full 401 matrix at the API edge returns the `auth.required` envelope with a non-sensitive reason, and no token/secret appears in captured logs; a valid token is accepted far enough to reach workspace resolution.
- [X] T013 [P] [US1] Integration tests `services/tests/test_api_auth_membership.py` (live Postgres): valid token + seeded `members` row (subject, ws, role) → role resolved and `require_permission` allows/denies per role; **no** membership for the asserted ws → `403` before resource load (no existence leak); a subject with membership in ws-A acting on ws-B → `403` (contract §5 items 14–16).

### Implementation (GREEN)

- [X] T014 [US1] Implement `JwtBearerProvider` in `services/api/auth/jwt_provider.py` — extract `Authorization: Bearer`; `jwt.decode(token, key, algorithms=<explicit allow-list>, audience=<cfg>, issuer=<cfg>, leeway=<skew>)`; **key source is injectable** (`PyJWKClient(jwks_uri)` in prod; a passed-in local key/JWKS in tests — FR-015); unknown `kid` → one refresh then reject; map every failure (missing/malformed/bad-sig/iss/aud/exp/nbf/alg/kid/JWKS-unavailable) to `AuthError`; never log the token (FR-001–FR-004, FR-010, FR-011, FR-017). Parse space-separated `scope` into `scopes`. Returns `Principal{subject, scopes, issuer, workspace_claims={}}`.
- [X] T015 [US1] Implement `services/api/membership.py` — `async def role_for(session, subject) -> str | None` running `SELECT console_role FROM members WHERE subject = :subject` inside the caller's `tenant_session(ws)` (RLS scopes to ws); returns the role or `None` (research R3).
- [X] T016 [US1] Extend `resolve_workspace` in `services/api/deps.py` — after selecting `ws`: if `principal.workspace_claims` already has a role for `ws` (dev path) use it; else open `tenant_session(ws)`, call `membership.role_for(session, principal.subject)`, `None` → `AccessDenied` (403), else set `principal.workspace_claims[ws] = role`. `require_permission` stays unchanged (research R3/R4; FR-013/FR-014).
- [X] T017 [US1] Run US1 suites + `tests/test_runtime_purity.py` + full regression → green; fix until all pass.

**Checkpoint**: US1 delivers the security floor — real verification + membership authZ. Independently shippable.

---

## Phase 4: User Story 2 — IdP selected/configured without code changes (P1)

**Goal**: switching the active provider or its config changes which tokens are accepted with zero call-site edits; dev provider is unusable in prod.
**Independent test**: run the suite under `AC_AUTH_PROVIDER=dev` vs `=trellix-iam` (local JWKS) — acceptance changes, routers/`require_permission` unchanged; `dev`+`prod` fails closed.

### Tests (write first — RED)

- [X] T018 [P] [US2] Unit tests `services/tests/test_auth_registry.py` — a provider swap (two definitions behind the common contract) changes which tokens verify with no code change; `AC_AUTH_PROVIDER=dev` + `AC_ENV=prod` fails closed; unknown/misconfigured provider fails closed; error messages carry no secret (contract §5 items 12–13; FR-006/FR-008).

### Implementation (GREEN)

- [X] T019 [US2] Harden `services/api/auth/registry.py` config validation + prod-gate to satisfy T018 (ensure `get_active_provider` is the sole selection site; validate required per-provider config present).
- [X] T020 [US2] Confirm & document zero call-site change: diff-review that no router or `require_permission` was edited for provider selection; note the single seam in `specs/012-pluggable-idp-auth/quickstart.md` step 3 (SC-003).

---

## Phase 5: User Story 3 — Real Trellix-IAM token accepted (P2)

**Goal**: the Trellix-IAM provider verifies a real preprod token; proven by an opt-in manual check (never in CI).
**Independent test**: run `scripts/iam_live_check.py mvp/.env` against a locally-running API with a seeded membership → token verifies, request authenticates.

- [X] T021 [US3] Wire the `trellix-iam` provider in `services/api/auth/jwt_provider.py` + `registry.py` from the settings defaults (issuer/audience/jwks_uri/RS256; research R2) — `id = "trellix-iam"`, uses `PyJWKClient(iam_jwks_uri)`.
- [X] T022 [US3] Add `scripts/iam_live_check.py` — read gitignored `mvp/.env` (strip quotes/whitespace), obtain a client-credentials token, submit it to a locally-running API (`AC_AUTH_PROVIDER=trellix-iam`) with a seeded membership; print subject/scopes + authN result. **Excluded from pytest** (FR-015/SC-007); never prints the token/secret.
- [X] T023 [US3] Record the manual live-check procedure + evidence in the completion notes (quickstart step 5 already drafted); confirm it is not collected by the default test run.

---

## Phase 6: Polish & cross-cutting

- [X] T024 [P] Confirm purity: `services/tests/test_runtime_purity.py` + `test_evals_purity.py` green — `runtime/`+`evals/` import no auth/jwt/http modules (SC-005).
- [X] T025 [P] Update docs per [[update-docs-after-feature]]: `docs/backend-integration-status.md` + the M5 row in `docs/implementation-plan.md` (feature 012 built — pluggable IdP authN; dev provider gated; membership authZ; still-deferred: console login, token mint/deny-set, Vault).
- [X] T026 Full regression `cd services && .venv/bin/python -m pytest -q` green; record before/after test counts in the completion report.

---

## Dependencies & execution order

- **Setup (T001–T004)** → **Foundational (T005–T010)** → **US1 (T011–T017)** → **US2 (T018–T020)** → **US3 (T021–T023)** → **Polish (T024–T026)**.
- Foundational is the blocking seam for every story; T010 is a hard regression gate.
- US2 mostly tests/hardens the registry built in Foundational; US3 depends on US1's `JwtBearerProvider`.
- Within a phase, `[P]` tasks (distinct files) may run together; tasks editing `services/api/deps.py` (T008, T009, T016) are sequential.

## Parallel opportunities

- Setup: T002, T003, T004 in parallel (distinct files).
- US1 tests: T011, T012, T013 in parallel (distinct test files) — all written before T014–T016.
- Polish: T024, T025 in parallel.

## MVP scope

**Foundational + US1 (T001–T017)** = the security floor: real bearer verification + membership-based authZ,
replacing the trusted header. US2 formalizes the config-swap guarantee; US3 proves the real-vendor path.

## Phase 7: Convergence

- [X] T027 Reconcile the `AuthnResult` entity with its usage per data-model.md (partial): it is defined in `services/api/auth/base.py` and exported in `api/auth/__init__.py` but never constructed — `JwtBearerProvider.verify` builds a `Principal` directly. Either (a) have `verify` produce an `AuthnResult` that `deps`/the provider maps into a `Principal` (matching the data-model), or (b) remove `AuthnResult` from `base.py` + `__init__.py` and note in data-model.md that the provider returns a `Principal` directly. Keep tests green.
