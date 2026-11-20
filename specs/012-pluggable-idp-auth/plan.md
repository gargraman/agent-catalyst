# Implementation Plan: Pluggable IdP Bearer-Token Authentication Adapter

**Branch**: `012-pluggable-idp-auth` (work continues on `feat/backend-m0-foundation`) | **Date**: 2026-11-08 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/012-pluggable-idp-auth/spec.md`

## Summary

Replace the unverified `X-Dev-Principal` principal resolution in `api/deps.py` with real bearer-token
verification behind a **vendor-pluggable provider seam** (`verify → Principal`), selected by config. Ship two
concrete providers — `trellix-iam` (real JWT: JWKS signature + `iss`/`aud`/`exp`/`alg`) and `dev` (the current
header path, retained for the test suite and gated off in production). Authentication only: the AC **role** is
sourced from the existing `members` table per workspace (Clarify Q1→B), the workspace from `X-Workspace-Id` +
membership check (Q2→A). API-layer only; `runtime/`/`evals/` stay pure. No migration.

## Technical Context

**Language/Version**: Python 3.12.13
**Primary Dependencies**: FastAPI/Starlette, SQLAlchemy 2.0 async (asyncpg); **new:** `pyjwt[crypto]` (JWT+JWKS); `cryptography` (test keypair, transitively present)
**Storage**: Postgres (existing `members` table, read-only here) — **no new migration**
**Testing**: pytest + pytest-asyncio (`cd services && .venv/bin/python -m pytest`); live compose Postgres for integration
**Target Platform**: Linux server (API service)
**Project Type**: web-service (backend `services/api`)
**Performance Goals**: JWKS fetched once + cached (bounded TTL); verification is in-memory per request (no per-request network after warm cache)
**Constraints**: fail-closed on every auth error (P3); no token/secret in logs (P5/FR-011); `runtime/`+`evals/` import-pure (P6); offline + secret-free automated tests (FR-015)
**Scale/Scope**: pilot; caller/machine plane; 2 providers + registry; ~1 rewired dependency (`session_principal`) + 1 extended (`resolve_workspace`)

## Constitution Check

*GATE: must pass before Phase 0 and re-checked after Phase 1 design.*

| Principle | How this feature complies |
|---|---|
| **P1/P9 honesty** | Replaces a *simulated* authN (trusted header) with real verification. Completion report will name commands run + the opt-in live check; the dev provider is explicitly labeled unverified-by-design and disabled in prod. No mock presented as real. |
| **P3 deterministic fail-closed controls** | Identity is enforced in code (signature/iss/aud/exp/alg), not model text; every failure path → `401`, never a partial/default principal (FR-010). Provider misconfig fails closed (R5). |
| **P5 tenancy + no existence leak** | Membership read happens inside `tenant_session(ws)` (RLS), no cross-tenant scan; a non-member is refused `403`/`404` with no existence leak (FR-014). Raw token/secret never logged (FR-011). |
| **P6 pure cores** | All token/JWKS/HTTP code lives in `services/api`; `runtime/`+`evals/` untouched. The existing purity test must stay green (SC-005). |
| **P7 provider honesty** | N/A (no model cost path touched). |
| **P8 observability** | Auth failures logged with non-sensitive reason only (issuer/kid/reason) — composes with feature 010 refusal counters; a follow-up may add an `ac_auth_failures_total{reason}` counter (noted, not required this slice). |
| **Dev workflow / TDD** | Genuine behavioral REDs: forged/expired/wrong-aud/bad-sig/`alg=none` tokens are accepted today (header) → rejected after; valid signed token → correct Principal. Paid/live-call protocol: live IAM is opt-in only, no secret in CI (FR-015). |

**Gate result**: PASS (no violations; no complexity-tracking entries needed).

## Project Structure

### Documentation (this feature)
```text
specs/012-pluggable-idp-auth/
├── plan.md            # this file
├── research.md        # Phase 0 (done)
├── data-model.md      # Phase 1
├── quickstart.md      # Phase 1
├── contracts/
│   └── auth-provider.md   # the provider protocol + bearer/401 contract
└── checklists/requirements.md
```

### Source Code (repository root)
```text
services/
├── api/
│   ├── auth/                      # NEW package — the pluggable seam (API layer)
│   │   ├── __init__.py
│   │   ├── base.py                # IdPProvider Protocol: async verify(request) -> Principal; AuthError
│   │   ├── registry.py            # get_active_provider(settings): dev|trellix-iam; prod gate (R5)
│   │   ├── dev_provider.py        # DevHeaderProvider — current X-Dev-Principal behavior (R4)
│   │   └── jwt_provider.py        # JwtBearerProvider + TrellixIamProvider (PyJWT+JWKS) (R1,R2)
│   ├── deps.py                    # EDIT: session_principal -> active provider; resolve_workspace -> membership read
│   ├── settings.py               # EDIT: AC_ENV, AC_AUTH_PROVIDER, trellix issuer/aud/jwks_uri/algs/skew
│   └── membership.py             # NEW (small): role_for(session, subject) -> str|None (RLS read)
├── tests/
│   ├── test_auth_jwt_provider.py          # NEW unit: sig/iss/aud/exp/nbf/alg, local keypair (FR-015)
│   ├── test_auth_registry.py              # NEW unit: selection + prod gate (R5/FR-008)
│   ├── test_api_auth_bearer.py            # NEW integration: bearer→Principal, 401 matrix, no-leak
│   ├── test_api_auth_membership.py        # NEW integration: role from members under RLS (Q1/Q2)
│   ├── _api_helpers.py                    # EDIT: add mint_test_jwt(...) + local JWKS helper; dev_headers unchanged
│   └── (existing suites unchanged — dev provider keeps them green)
└── scripts/
    └── iam_live_check.py          # NEW opt-in: live Trellix-IAM token -> local API (reads gitignored mvp/.env)
```

**Structure decision**: a new `services/api/auth/` package isolates all vendor logic behind `base.IdPProvider`;
`deps.py` depends only on the registry, so adding Okta/Entra/Cognito later is "new file + config" with no
call-site change (FR-005, SC-003). `membership.py` is a tiny read helper reused by `resolve_workspace`.

## Phase 1 — design artifacts

- **data-model.md**: `IdPProvider` (config), `Principal` (extended with `scopes`, `issuer`), `AuthnResult`,
  JWKS cache, membership read. Entities + validation + the one state concern (JWKS refresh).
- **contracts/auth-provider.md**: the `IdPProvider` protocol signature + error type; the inbound
  `Authorization: Bearer` contract; the `401` envelope (reuses `errors.py` `AuthRequired`); the config keys.
- **quickstart.md**: offline test commands (RED→GREEN), the purity check, and the opt-in live-IAM validation.

## Phase 2 — not here

`/speckit-tasks` generates `tasks.md` (dependency-ordered, TDD). Then `/speckit-checklist` (security-focused),
`/speckit-analyze`, `/speckit-implement`, `/speckit-converge`.

## Complexity Tracking

No constitutional deviations to justify. The one pragmatic choice — `resolve_workspace`'s dual claim source
(dev header vs membership read) — is documented in research R4 and is strictly a backward-compatibility
affordance for the dev provider, inert in production.
