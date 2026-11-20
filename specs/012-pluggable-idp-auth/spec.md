# Feature Specification: Pluggable IdP Bearer-Token Authentication Adapter

**Feature Branch**: `feat/backend-m0-foundation` (feature dir `012-pluggable-idp-auth`)

**Created**: 2026-11-08

**Status**: Draft

**Input**: User description: "M5 slice — Pluggable IdP bearer-token authentication adapter (IdP-agnostic authN). Replace the unverified `X-Dev-Principal` dev header with real bearer-token verification behind a vendor-pluggable seam (Trellix IAM first; Okta/Entra/Cognito/generic-OIDC droppable via config). Caller/machine plane only. Fail-closed. API-layer only; runtime/evals stay pure. Verified against a local keypair + JWKS in tests — no live IAM calls, no secrets in the suite."

## Clarifications

### Session 2026-11-08

- Q: What governs authorization when the IdP token has no AC workspace/role claim? → A: **Option B** — the IdP provides authN only (verified identity + scopes); Agent-Catalyst's own **membership store** (feature 006 `members`) governs role/permissions per workspace. `xdr.*` scopes are carried on the Principal but are NOT mapped onto AC permissions.
- Q: How is the target workspace determined for a token with no workspace claim? → A: **Option A** — the caller sends an explicit `X-Workspace-Id`; `resolve_workspace` verifies the token subject has a membership row for that workspace and reads the role from it. No membership → fail-closed `403`/`404`, no existence leak.
- Q: How are provider signing keys (JWKS) sourced and cached? → A: **Option A** — fetch JWKS from the provider's issuer endpoint and cache with a bounded TTL; on an unknown `kid`, refresh once then reject if still absent.
- Q: How many concrete vendor providers does this slice build? → A: **Option A** — only the Trellix-IAM provider (verified real target) and the dev/test provider (local keypair). The registry/interface is ready for others; no other vendors are stubbed this slice.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A caller's bearer token is cryptographically verified before any access (Priority: P1)

A programmatic caller (a machine/service client issued credentials by the enterprise's IdP) sends a request to a workspace-scoped API endpoint carrying its IdP-issued bearer token in the `Authorization` header. The platform verifies the token's signature, issuer, audience, and expiry against the configured provider **before** resolving any workspace, loading any resource, or running any permission check. Only a token that passes every check yields a usable Principal; anything else is rejected with `401` and no resource is touched.

**Why this priority**: This is the security floor for the pilot. Today the principal is taken from an **unverified** `X-Dev-Principal` JSON header — any caller can claim any identity/role. Until tokens are actually verified, every other control (RBAC, tenancy, spend cap) sits on a forgeable foundation. This story alone turns "trust the header" into "trust a cryptographically verified token" and is a viable, demonstrable MVP.

**Independent Test**: Issue a JWT signed by a locally-generated keypair whose public key is served at a local JWKS endpoint configured as the active provider; a correctly-signed, in-date, correct-`aud`/`iss` token resolves to the expected Principal and the request proceeds; a token with a tampered signature, wrong `aud`, wrong `iss`, or past `exp` is rejected with `401` and never reaches resource load.

**Acceptance Scenarios**:

1. **Given** the active provider is configured with issuer I and audience A and a JWKS containing key K, **When** a caller sends a JWT signed by K with `iss=I`, `aud=A`, and `exp` in the future, **Then** the request is authenticated and a Principal carrying the token's subject and scopes is produced.
2. **Given** the same provider config, **When** a caller sends a JWT whose signature does not verify against any JWKS key, **Then** the request is rejected with `401` (auth error envelope) and no workspace/resource is resolved.
3. **Given** the same provider config, **When** a caller sends a JWT with `aud` ≠ A, or `iss` ≠ I, or `exp` in the past (beyond allowed skew), or `nbf`/`iat` in the future (beyond allowed skew), **Then** the request is rejected with `401`.
4. **Given** a request with no `Authorization` bearer token (and no valid dev-provider header in a non-production profile), **When** it reaches a protected endpoint, **Then** it is rejected with `401`.
5. **Given** a malformed token (not a JWT / wrong segment count / undecodable), **When** it is presented, **Then** it is rejected with `401` and the raw token value never appears in logs or the response.

---

### User Story 2 - The enterprise's IdP is selected and configured without code changes (Priority: P1)

An operator deploying the platform for a specific enterprise selects which IdP provider is active and supplies its configuration (issuer, audience, JWKS source, allowed clock skew) through settings/config. Switching from one vendor's provider to another, or pointing at a different issuer/audience, requires only configuration — no change to API call sites, the RBAC layer, or the tenancy layer.

**Why this priority**: The owner-stated load-bearing requirement. Different enterprises run different IdPs (Trellix IAM, Okta, Entra, Cognito, generic OIDC). If token-processing logic is welded into the request path, every new customer is a code fork. The pluggable seam is what makes the platform deployable per-enterprise. It is P1 because the Trellix-IAM provider (US3) must be built *through* this seam, not beside it — the abstraction and its first implementation are one deliverable.

**Independent Test**: With the dev/test provider active via config, protected requests verify locally-signed tokens; changing config to a second provider definition (same contract, different issuer/audience/JWKS) changes which tokens are accepted — with **zero** changes to any router or dependency call site.

**Acceptance Scenarios**:

1. **Given** two provider definitions A and B registered under the common contract, **When** config names A as active, **Then** tokens valid for A authenticate and tokens valid only for B are rejected — and vice-versa when config names B — with no code change between the two runs.
2. **Given** config names a provider that is not registered, or omits required provider config, **When** the application starts (or first authenticates), **Then** it fails closed (startup error or `401`/`503` — never silently accepts), with a clear operator-facing message and no secret in it.
3. **Given** a request path (router + permission dependency), **When** the active provider is swapped, **Then** the router and `require_permission` code are byte-for-byte unchanged (the seam is the only thing that varies).

---

### User Story 3 - A real Trellix-IAM token is accepted as the first concrete provider (Priority: P2)

With the Trellix-IAM provider active, a bearer token issued by Trellix IAM (preprod/QA) — an OAuth2 client-credentials JWT carrying `iss=https://preprod.iam.cloud.trellix.com/iam/v1.0`, `aud=mcafee`, `exp`, and a space-separated `scope` of `xdr.*` grants, with `sub == client_id` — verifies to a Principal and is accepted by the platform.

**Why this priority**: This proves the abstraction against the real target IdP, not just a synthetic one. It is P2 (not P1) because, per the constitution's paid-call/credential protocol, it **cannot** run in the automated suite (no real secret in tests, no live IAM dependency in CI). It is validated by a manual/opt-in integration step and documented, while US1/US2 carry the automated behavioral guarantees.

**Independent Test**: A manual/opt-in script reads client credentials from gitignored `mvp/.env`, obtains a live Trellix-IAM token, and submits it to a locally-running API configured with the Trellix-IAM provider; the request authenticates (subject/scopes present on the resulting Principal). This step is explicitly excluded from the default automated test run.

**Acceptance Scenarios**:

1. **Given** the Trellix-IAM provider is active and pointed at the preprod issuer/audience, **When** a live Trellix-IAM client-credentials token is presented, **Then** it verifies and the Principal carries `subject = sub` and the token's `xdr.*` scopes.
2. **Given** the automated test suite runs with no network and no secrets, **When** the suite executes, **Then** it exercises verification entirely through the local dev/test provider (local keypair + local JWKS) and makes **no** call to Trellix IAM.

---

### Edge Cases

- **JWKS key rotation / `kid` miss**: the token's `kid` is not in the currently-cached JWKS → refresh the JWKS at most once; if the `kid` is still absent, reject fail-closed `401` (FR-017).
- **JWKS source unreachable**: the provider cannot fetch keys (network/endpoint down). The platform must fail closed (reject with `401`/`503`), never fall back to accepting unverified tokens. Operational readiness signalling (`/readyz`) is out of scope here but noted.
- **Clock skew at the boundary**: `exp`/`nbf`/`iat` within the configured skew window are tolerated; outside it they are rejected. The skew bound is configurable and bounded.
- **Algorithm confusion**: a token presented with `alg=none`, or a symmetric `alg` (e.g. HS*) where an asymmetric key is expected, MUST be rejected — only the provider's expected/allow-listed algorithms are accepted.
- **Token with no AC workspace/role claim** (the real Trellix-IAM case): authN still succeeds (identity + scopes resolved); the target workspace comes from `X-Workspace-Id` and the AC role from the membership store for that workspace (FR-013/FR-014) — a verified token with no membership in the asserted workspace is rejected fail-closed.
- **Dev-provider in production profile**: the unverified dev-header path MUST be unavailable when the deployment profile is production; presenting `X-Dev-Principal` in production has no effect (ignored; request still requires a real token).
- **Both a bearer token and a dev header present** (non-production): the real bearer token takes precedence; the dev path is only consulted when explicitly the active provider.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST verify an inbound bearer token's cryptographic signature against the active provider's signing keys before producing a Principal. A token whose signature does not verify MUST be rejected with `401`.
- **FR-002**: The system MUST validate the token's `iss` (issuer) and `aud` (audience) against the active provider's configured values, and reject (`401`) on mismatch.
- **FR-003**: The system MUST validate temporal claims (`exp`, and when present `nbf`/`iat`) within a configurable, bounded clock-skew tolerance, and reject (`401`) expired or not-yet-valid tokens.
- **FR-004**: The system MUST only accept tokens signed with the provider's expected/allow-listed signing algorithm(s); `alg=none` and unexpected algorithm families MUST be rejected.
- **FR-005**: The system MUST expose a single vendor-agnostic verification contract (conceptually `verify(raw_bearer) → Principal`, raising a typed auth error on failure) that every call site uses, so that adding or switching an IdP vendor requires no change to call sites, the RBAC layer, or the tenancy layer.
- **FR-006**: The system MUST select the active IdP provider and its configuration (issuer, audience, JWKS/key source, allowed algorithms, clock-skew bound) from settings/configuration, not hard-coded per call site.
- **FR-007**: The system MUST provide a Trellix-IAM provider implementation as the first concrete provider, validating tokens from issuer `https://preprod.iam.cloud.trellix.com/iam/v1.0` with audience `mcafee` and extracting the space-separated `scope` claim into the Principal's scopes.
- **FR-008**: The system MUST provide a dev/test provider (locally-signed tokens, local key source) usable for automated tests and local development, and this provider MUST NOT be usable under a production deployment profile.
- **FR-009**: The system MUST replace the current unverified `X-Dev-Principal` principal resolution in the request path with resolution via the active provider; the unverified-header behavior MUST survive only as the explicitly-selected dev/test provider, gated off in production (FR-008).
- **FR-010**: On any authentication failure (missing/malformed/expired/wrong-aud/wrong-iss/bad-signature/unverifiable/unconfigured-provider), the system MUST fail closed — reject the request, never produce a partial or default-trusted Principal, and never proceed to resource resolution.
- **FR-011**: The system MUST NOT log, trace, echo, or persist the raw bearer token or any provider secret (client secret, private key). Diagnostic output MUST be limited to non-sensitive, non-reversible information (e.g., failure reason, issuer, `kid`), consistent with the no-secret-leakage principle.
- **FR-012**: Authentication (this feature) MUST compose with, and not alter, the existing authorization layer: successful authN yields identity + scopes; workspace resolution and permission enforcement (feature 008 RBAC + RLS tenancy) continue to govern access. A verified token is necessary but not sufficient for access.
- **FR-013**: The IdP provides **authentication only**. The normalized Principal MUST carry at minimum: subject, the token's scopes, and the issuer/provider identity. Agent-Catalyst **authorization** (the role used by `require_permission`) MUST be sourced from the Agent-Catalyst **membership store** (feature 006 `members`) for the resolved workspace — NOT from the token. IdP scopes (e.g. `xdr.*`) are retained on the Principal for audit/future use but MUST NOT be mapped onto AC permissions in this slice. [Resolved — Clarify 2026-11-08, Option B.]
- **FR-014**: The target workspace MUST be taken from an explicit `X-Workspace-Id` request value; `resolve_workspace` MUST verify the token subject has a membership row for that workspace and derive the AC role from it. A subject with no membership for the asserted workspace MUST be rejected fail-closed (`403`/`404`, no existence leak, P5). A caller MUST NOT be able to act on a workspace it holds no membership in. [Resolved — Clarify 2026-11-08, Option A.]
- **FR-015**: The automated test suite MUST verify all authN behaviors using only locally-generated keys/tokens and a local key source, making **no** network call to any live IdP and embedding **no** real secret. Validation against live Trellix IAM MUST be a manual/opt-in step reading credentials from gitignored configuration.
- **FR-016**: The pure cores (`runtime/`, `evals/`) MUST remain free of any IdP/HTTP/token-verification imports; all token-processing lives in the API layer (constitution P6). [A purity check MUST continue to pass.]
- **FR-017**: Each provider's signing keys MUST be fetched from its configured issuer JWKS endpoint and cached with a bounded TTL. On a token whose `kid` is not in the cached key set, the system MUST refresh the JWKS at most once and then reject (`401`) if the `kid` is still absent. A JWKS fetch failure MUST NOT cause any token to be accepted unverified (fail-closed). [Resolved — Clarify 2026-11-08, Option A.]
- **FR-018**: This slice MUST implement exactly two concrete providers — `trellix-iam` (the verified real target) and `dev` (local-keypair test/dev) — behind the common contract (FR-005). No other vendor provider is built or stubbed in this slice; the registry MUST remain open so a new vendor can be added as configuration plus one provider implementation without call-site changes. [Resolved — Clarify 2026-11-08, Option A.]

### Key Entities *(include if feature involves data)*

- **IdP Provider (vendor plugin)**: a named, configurable implementation of the common verification contract. Attributes: provider id/kind, issuer, audience, signing-key source, allowed algorithms, clock-skew bound. Examples: `trellix-iam` (first concrete), `dev` (local test), future `okta`/`entra`/`cognito`/`oidc`.
- **Principal**: the normalized, verified identity the rest of the system consumes. Attributes (minimum): subject, scopes, issuer/provider identity. The AC **role** is NOT carried from the token — it is resolved per request from the membership store for the asserted workspace (FR-013/FR-014). Replaces the shape previously parsed from the unverified dev header.
- **Membership (existing — feature 006 `members`)**: the workspace-scoped roster binding a subject to an AC role. This slice reads it as the authorization source for a verified caller; it is not created or migrated here.
- **Signing Key Set (JWKS)**: the set of public keys (by `kid`) used to verify token signatures for a provider; sourced per FR-017.
- **Bearer Token**: the inbound credential (an IdP-issued JWT for real providers; a locally-signed JWT for the dev/test provider). Treated as a secret in transit (FR-011).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of authentication-failure cases in the behavioral suite (bad signature, wrong `aud`, wrong `iss`, expired, not-yet-valid, malformed, missing, `alg=none`, unexpected `alg`, unconfigured provider) result in a `401` with no resource resolution and no token/secret in output.
- **SC-002**: A validly-signed, in-date, correct-`aud`/`iss` token authenticates successfully and yields a Principal carrying the token's subject and scopes, in 100% of valid-token tests.
- **SC-003**: Switching the active provider between two definitions changes which tokens are accepted with **zero** edits to any router or permission/tenancy call site (demonstrated by diff review + passing tests under each config).
- **SC-004**: The entire automated test suite for this feature runs with no network access and no real secret present, and makes zero calls to any live IdP (verified by the suite passing offline).
- **SC-005**: The pure-core purity check passes — no IdP/HTTP/token imports in `runtime/` or `evals/`.
- **SC-006**: Authorization behavior from feature 008 (RBAC) and tenancy (RLS) is unchanged for an authenticated caller — the existing per-tier allow/deny and negative cross-tenant tests remain green once principals are sourced from a verified token.
- **SC-007**: A documented manual/opt-in check obtains a live Trellix-IAM token and authenticates it against a locally-running instance, confirming the real-vendor path (recorded as evidence, not part of CI).

## Assumptions

- **Caller/machine plane only for this slice.** The verified tokens are OAuth2 client-credentials bearer tokens (`sub == client_id`, no interactive user identity). The console user-login (authorization-code/OIDC, PKCE, cookies/CSRF) flow is a separate later slice; the abstraction must not preclude it but it is out of scope here.
- **No new database migration.** This slice is authN at the API edge; it does not add persisted tables. The authZ source (Clarify Q1→B) reuses the **existing** `members` table (feature 006), read-only here — no new storage.
- **A JWT/JWKS verification library will be added** as an API-layer dependency (in-scope, constitution-compatible — kept out of the pure cores).
- **Trellix IAM specifics are fixed from verified evidence**: endpoint `https://preprod.iam.cloud.trellix.com/iam/v1.0/token`, issued JWT `iss=.../iam/v1.0`, `aud=mcafee`, `scope` is a space-separated list of `xdr.*` grants, token lifetime ~600s, both HTTP Basic and form-body client auth accepted. Credentials live in gitignored `mvp/.env` (values are quote-wrapped with trailing whitespace — must be trimmed).
- **Composition, not replacement.** Feature 008 RBAC (`require_permission`) and RLS tenancy remain the authorization mechanism; this feature only changes how the Principal is *obtained* (verified token vs trusted header).
- **Frontend**: no console-login contract change in this slice. Any FE note is contract-level only and not build-gated here.
- **Deployment profile** (production vs dev/test) is expressible in configuration and is the switch that disables the dev/test provider (FR-008).
