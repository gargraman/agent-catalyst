# Research — Pluggable IdP Bearer-Token Auth Adapter (feature 012)

Phase 0 output. Resolves every unknown in Technical Context. All four spec clarifications were already
answered in `/speckit-clarify` (see `spec.md` → Clarifications 2026-11-08); this records the *technical*
decisions that follow from them.

## R1 — JWT/JWKS verification library

- **Decision**: **PyJWT** with the `crypto` extra (`pyjwt[crypto]`), added under `services/pyproject.toml`
  `[api]`. Use `jwt.decode(token, key, algorithms=[...], audience=..., issuer=..., leeway=skew)` for the
  claim + signature checks, and `jwt.PyJWKClient(jwks_uri)` for JWKS fetch/cache/`kid` selection.
- **Rationale**: smallest well-maintained dependency that covers FR-001…FR-004 + FR-017 directly; `PyJWKClient`
  already implements fetch-and-cache + `kid` lookup with an internal refresh, matching Clarify Q3→A. `x5c`
  cert chains (which Trellix IAM returns) are handled natively. Pure-Python + `cryptography` wheel; no HTTP
  framework pulled in, so it stays cleanly inside the API layer (P6).
- **Alternatives considered**: `python-jose` (less actively maintained, CVE history); `authlib` (heavier,
  full OAuth client/server we don't need); hand-rolled RSA verify (reinvents JOSE, error-prone — rejected per
  "don't build crypto you can buy").
- **Algorithm allow-list**: accept **RS256 only** by default (Trellix IAM signs RSA (`x5c`), the JOSE default
  for `x5c`), configurable per provider. `alg=none` and HS* are rejected by passing an explicit `algorithms`
  list to `jwt.decode` (never derive the algorithm from the token header) — closes the alg-confusion edge case.

## R2 — Trellix-IAM endpoints (pinned from live, unauthenticated discovery 2026-11-08)

- **Decision**: provider config for `trellix-iam`:
  - `issuer = https://preprod.iam.cloud.trellix.com/iam/v1.0`
  - `audience = mcafee`
  - `jwks_uri = https://preprod.iam.cloud.trellix.com/iam/v1.0/jwks`
  - (discovery doc: `https://preprod.iam.cloud.trellix.com/iam/v1.0/.well-known/openid-configuration`)
- **Rationale**: fetched live — the well-known discovery returned `issuer`, `jwks_uri`, `token_endpoint`,
  `validation_endpoint`; the `jwks_uri` returned `{"keys":[{"x5c":[…], …}]}` (RSA, HTTP 200). These are
  **not secrets** (public metadata/keys). The issuer/audience match the decoded live token verified earlier
  (`aud=mcafee`, `iss=…/iam/v1.0`).
- **Alternatives considered**: hard-coding static public keys (brittle across rotation — rejected, Clarify
  Q3→B rejected); per-request discovery (latency/DoS — rejected). Provider may be seeded from the
  discovery doc at startup OR configured directly with the `jwks_uri`; default is direct `jwks_uri` config
  with discovery as an optional convenience.
- **Note**: prod/other-env issuers differ (e.g. non-preprod host); all four values are **config**, never
  hard-coded in code — that is the whole point of the pluggable seam.

## R3 — Membership-based authorization read under RLS (Clarify Q1→B, Q2→A)

- **Decision**: resolve the caller's AC role from the existing `members` table **after** the target workspace
  is known, inside a `tenant_session(ws)` (RLS GUC = ws). Query `SELECT console_role FROM members WHERE
  subject = :subject` — RLS scopes it to `ws`, returning only that workspace's row for the subject. No row →
  fail-closed `AccessDenied` (403). No RLS bypass, no cross-tenant read, no new migration.
- **Rationale**: the request always names one workspace via `X-Workspace-Id` (Q2→A), so we never need a
  cross-workspace membership scan — which is exactly what RLS forbids. Reading "my own membership for the
  workspace I'm asking about" is in-tenant and RLS-legal. `members.console_role` already stores the RBAC role
  name (`viewer`/`builder`/`publisher`; `admin` where granted), so it feeds `has_permission` unchanged.
- **Alternatives considered**: load all memberships for the subject in `session_principal` (pre-workspace) —
  needs an RLS-bypass/`SECURITY DEFINER` read (deferred org-plane territory, M9); rejected as unnecessary
  scope + risk. A separate admin connection — rejected for the same reason.

## R4 — Keeping the existing test suite green (dev provider = the `X-Dev-Principal` path)

- **Decision**: the current `X-Dev-Principal` JSON-header behavior becomes the explicit **`dev` provider**
  (`DevHeaderProvider`), selected only under a non-production profile. It returns a `Principal` with
  `workspace_claims` **directly from the header** (today's behavior, verbatim) — a documented dev/test
  affordance that short-circuits both token verification and the membership read.
- **Rationale**: the ~423-test suite authenticates by sending `X-Dev-Principal` through the *real*
  `session_principal` (there is **no** `dependency_overrides[session_principal]`; see `tests/_api_helpers.py`
  `dev_headers`). Preserving this path as the `dev` provider keeps all existing RBAC/tenancy/spend tests green
  with zero edits (SC-006). `resolve_workspace` therefore keeps a dual source: **if** the principal already
  carries a role claim for the ws (dev path) use it; **else** resolve from the membership store (prod path).
  In production the `dev` provider is disabled (FR-008), so only the membership path runs.
- **Alternatives considered**: rip out the header path and reseed `members` in every test — ~hundreds of
  call-site edits + complex multi-workspace-claim cases; rejected as churn with no behavioral gain this slice.

## R5 — Deployment profile switch (FR-008)

- **Decision**: a settings flag `AC_ENV` (`dev` default | `prod`) plus `AC_AUTH_PROVIDER`
  (`dev` default | `trellix-iam`). The provider **registry** refuses to return the `dev` provider when
  `AC_ENV=prod` (fail-closed at selection/startup, clear operator message, no secret). Production MUST set
  `AC_AUTH_PROVIDER=trellix-iam` (or another real provider).
- **Rationale**: one explicit, testable switch that makes the unverified dev path unreachable in prod — the
  security property in FR-008/edge-cases. Keeping provider id and env as separate knobs lets a real provider
  be exercised in dev too.
- **Alternatives considered**: inferring "prod" from the DB URL or absence of the dev header — implicit and
  error-prone; rejected (fail-open risk).

## R6 — Local-keypair test provider for JWT verification (FR-015, no live calls / no secrets in CI)

- **Decision**: unit/integration tests for the JWT path generate an **RSA keypair locally** (via
  `cryptography`), mint test JWTs with PyJWT, and feed the public key to the verifier through an **injectable
  key resolver** (the provider accepts a key source; tests pass the local public key / a local JWKS dict
  instead of a network `PyJWKClient`). No HTTP, no Trellix IAM, no secret in the suite.
- **Rationale**: satisfies FR-015 + the constitution's no-secret-in-tests rule while still exercising real
  signature/`iss`/`aud`/`exp`/`alg` verification logic (genuine behavioral RED→GREEN). The live-IAM check is
  a separate opt-in script (reads gitignored `mvp/.env`), already prototyped this session.
- **Alternatives considered**: mocking `jwt.decode` (would test nothing); committing a fixed test key as a
  "fake secret" (still a key in the repo, muddies FR-021 intent) — rejected in favor of per-run ephemeral keys.

## Open/again-later (explicitly NOT in this slice)

- Console user-login (authorization-code/OIDC + PKCE + cookies/CSRF) — separate later slice.
- `admin` platform-wide claim origin + org-plane `SECURITY DEFINER` membership reads — M9.
- Token mint + deny-set (the *outbound* caller-token story) — next M5 slice.
- Vault custody of any provider secret — infra-blocked (not in compose), deferred.
