# Feature Specification: M5 — Caller-Token Plane + Immediate Revocation (Deny-Set)

**Feature Branch**: `feat/backend-m0-foundation` (feature dir `018-m5-caller-token-denyset`)

**Created**: 2026-11-11

**Status**: Draft · **Priority**: P1 · **Parent**: [`015-m5-security-completion`](../015-m5-security-completion/spec.md) (M5 umbrella)

**Input**: Split from the M5-completion epic (sub-feature 015c).

> **Shared design**: [research R5](../015-m5-security-completion/research.md) ·
> [data-model (`api_tokens`, deny-set)](../015-m5-security-completion/data-model.md) ·
> [contract](../015-m5-security-completion/contracts/caller-token.md).
> **Dependencies**: 016 (shared Redis client). **Blocks**: 019 (OIDC login reuses the deny-set check).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Publishers mint revocable caller tokens; revocation takes effect immediately (Priority: P1)

A publisher mints a caller token so an external service can invoke a published agent; the secret is shown exactly
once. Later a token is leaked or a user is offboarded during an incident; an admin cuts that access *now* —
killing active console sessions and caller tokens on their next call — not on the next IdP login.

**Why this priority**: The published-invoke auth plane is a prerequisite for safely exposing invoke; immediate
revocation (the Redis deny-set) is the designed answer to a leaked token / disabled user and must fail closed.

**Independent Test**: Mint a token (capture the one-time secret), invoke, revoke, confirm next invoke rejected;
add a subject to the deny-set and confirm an active session and a live token are both rejected next request, and
an in-flight stream is cut.

**Acceptance Scenarios**:

1. **Given** a publisher mints a token, **When** mint succeeds, **Then** the full secret is returned exactly
   once; only a non-reversible hash + display prefix persist; the secret never appears in logs/traces/exports.
2. **Given** a valid token, **When** revoked, **Then** its next invoke is rejected as unauthorized.
3. **Given** an admin `revoke-user`/`force-logout` for a subject, **When** that subject's active session makes a
   request **or** a caller token makes a call, **Then** both are rejected immediately (not on next login), and
   any **in-flight streaming invoke is cut**, for ≥ the max access-token lifetime.
4. **Given** the deny-set store is unavailable, **When** an auth decision runs, **Then** the request **fails
   closed** (rejected), not allowed through unchecked.
5. **Given** a token with optional expiry, **When** used after expiry, **Then** it is rejected.

### Edge Cases

- Deny-set TTL must be ≥ max access-token/session lifetime (≥ 1h) so a revoked subject can't outlive the set.
- A token revoked mid-stream → the stream is cut (not allowed to complete).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-010**: Publishers/admins can mint caller tokens; secret shown exactly once; only a non-reversible hash +
  prefix persisted.
- **FR-011**: Token secrets never appear in logs, traces, or exports.
- **FR-012**: Revoking a token rejects subsequent use immediately (durable `revoked_at`, checked every call); an
  in-flight streaming invoke is **cut** on detected revocation.
- **FR-013**: An admin action immediately revokes a subject's access, rejecting active console sessions and live
  caller tokens on their next request via a transient deny-set on the per-request auth path.
- **FR-014**: Max console session/access-token lifetime ≤ 1h; deny-set entry TTL ≥ that (≥ 1h).
- **FR-015**: Tokens support optional expiry; use after expiry is rejected.
- **FR-016**: Mint/revoke requires publisher+; subject revoke/force-logout requires admin.
- **FR-024b** (deny-set portion): deny-set fails **closed** when Redis is unavailable.
- **FR-028** (cross-cutting): No token/session secret leaks into logs, traces, or exports.

### Key Entities

- **Caller token** (`api_tokens`, Postgres/RLS): hash + prefix, workspace+agent bound, optional expiry, revoked
  marker. See data-model.
- **Deny-set entry** (Redis, transient): keyed by subject and token-id, TTL ≥ 1h; presence rejects immediately,
  including cutting an in-flight stream.

## Success Criteria *(mandatory)*

- **SC-004**: A revoked token or deny-set-listed subject is rejected on the very next request (0 successful
  requests after revocation), without waiting for the next IdP login.
- **SC-005**: A one-time-shown token secret never appears in any log/trace/export (verified by secret scan).

## Assumptions

- `api_tokens` table per `docs/backend/01 §4.7`; create `0007_api_tokens` migration if not already present.
- Source: `docs/backend/09 §2.1, §6`.
