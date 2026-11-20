# Feature Specification: M5 — OIDC Console Login + Session + CSRF

**Feature Branch**: `feat/backend-m0-foundation` (feature dir `019-m5-oidc-console-login`)

**Created**: 2026-11-11

**Status**: Draft · **Priority**: P2 · **Parent**: [`015-m5-security-completion`](../015-m5-security-completion/spec.md) (M5 umbrella)

**Input**: Split from the M5-completion epic (sub-feature 015d).

> **Shared design**: [research R4](../015-m5-security-completion/research.md) ·
> [contract](../015-m5-security-completion/contracts/console-auth.md).
> **Dependencies**: 018 (per-request auth path checks the deny-set). **Blocks**: 020 (group sync runs at login).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Console users sign in through the organization's identity provider (Priority: P2)

A builder opens the console, is redirected to the org IdP, signs in, and returns to an authenticated session.
Every console request validates the session; state-changing requests are protected against CSRF.

**Why this priority**: Real console login (OIDC authorization-code + session) replaces the mocked identity and is
the gate to using the console for real. P2 because bearer authN (012) and server authZ (008/013) already exist.

**Independent Test**: Complete an auth-code login against a stub IdP, confirm an authenticated session on a
protected route, confirm a state-changing request without a CSRF token is rejected, confirm logout ends it.

**Acceptance Scenarios**:

1. **Given** an unauthenticated user hits a protected console route, **When** processed, **Then** they go through
   the IdP authorization-code login and return to an authenticated session.
2. **Given** an authenticated session, **When** any console request is made, **Then** the session is validated
   server-side (iss/aud/exp) **and** the deny-set is checked before the request proceeds.
3. **Given** an authenticated session, **When** a state-changing request arrives without a valid anti-CSRF token,
   **Then** it is rejected (`403 csrf.invalid`).
4. **Given** an authenticated user, **When** they log out, **Then** the session ends and a protected request
   requires re-login.

### Edge Cases

- IdP briefly unreachable during login → user-visible failure/retry, **not** fail-open.
- Session lifetime ≤ 1h; expiry → re-login (and bounds the 018 deny-set TTL floor).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-017**: Authenticate console users via an OIDC authorization-code flow and establish a server-validated
  session.
- **FR-018**: Every console request validates the session/token server-side (iss/aud/exp) and checks the deny-set
  (018) before proceeding.
- **FR-019**: State-changing console requests require a valid anti-CSRF token (double-submit).
- **FR-020**: Users can log out, ending the session.
- **FR-029**: The established session has a bounded lifetime of **≤ 1 hour**; on expiry the user must re-authenticate.
  (This bound also sets the floor for the 018 deny-set TTL — FR-014 in the umbrella.)
- **FR-027** (cross-cutting): Enforced server-side; fails closed (deny-set match or Redis down → 401).
- **FR-028** (cross-cutting): No session secret leaks into logs, traces, or exports.

### Key Entities

- **Console session**: signed, HttpOnly/Secure/SameSite cookie, lifetime ≤ 1h, carrying the authenticated
  subject; stateless (revocation via the 018 deny-set); CSRF double-submit binding.

## Success Criteria *(mandatory)*

- **SC-006**: Console users complete an IdP authorization-code login and reach an authenticated session; every
  protected route rejects unauthenticated / CSRF-missing state-changing requests in 100% of tested cases.

## Assumptions

- Generic-OIDC baseline + the already-built dev/trellix-iam providers via the `api/auth/` registry; `authlib` for
  the auth-code flow (research R4).
- Frontend login touchpoints replace `MOCK_PROFILE` where the seam allows; full FE wiring is M12.
- Source: `docs/backend/09 §3`, `docs/backend/18`.
