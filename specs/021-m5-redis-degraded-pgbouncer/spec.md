# Feature Specification: M5 — Redis-SPOF Degraded Mode + PgBouncer Transaction-Mode

**Feature Branch**: `feat/backend-m0-foundation` (feature dir `021-m5-redis-degraded-pgbouncer`)

**Created**: 2026-11-11

**Status**: Draft · **Priority**: P3 · **Parent**: [`015-m5-security-completion`](../015-m5-security-completion/spec.md) (M5 umbrella)

**Input**: Split from the M5-completion epic (sub-feature 015f). Milestone capstone.

> **Shared design**: [research R6 + degraded-mode](../015-m5-security-completion/research.md) ·
> [data-model (Redis structures table)](../015-m5-security-completion/data-model.md).
> **Dependencies**: 016 (rate limiting) and 018 (deny-set) must exist to validate their degraded behavior.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The platform degrades safely and predictably when its shared cache is down (Priority: P3)

The shared cache (Redis) backs rate limiting, the deny-set, idempotency, and spend reservations — a single point
of failure for several controls. Operators need a documented blast radius and a deliberate, tested degraded-mode
decision per control, so an outage produces known behavior rather than silent weakening of security.

**Why this priority**: Resilience/operational hardening (Constitution P8). P3 because it validates behavior of
controls delivered in 016/018 rather than adding new user-facing capability, but must land before M5 is complete.

**Independent Test**: Make the shared store unavailable, exercise each dependent control, confirm each exhibits
its documented degraded behavior; confirm tenant isolation holds under transaction-mode pooling.

**Acceptance Scenarios**:

1. **Given** the shared store is unavailable, **When** a security control is exercised, **Then** the deny-set and
   spend reservation **fail closed** and idempotency fails closed with a retryable error, while rate limiting
   **fails open and raises an alert**.
2. **Given** a documented degraded-mode decision per control, **When** reviewers read it, **Then** each control
   (rate limit, deny-set, idempotency, spend reservation) has an explicit, tested behavior and a recovery path.
3. **Given** per-request DB connections run through transaction-mode pooling, **When** a request sets its tenant
   context, **Then** that context does not leak across pooled connections and workspace isolation holds.

### Edge Cases

- Redis recovers mid-flight → controls resume normal behavior without a restart.
- A pooled connection reused across tenants must never carry a stale `app.workspace_id` GUC.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-024**: On shared-cache unavailability: deny-set + spend reservation **fail closed**; idempotency fails
  closed with a retryable error; **rate limiting fails open + alert**. Each control's degraded behavior is
  explicit and tested.
- **FR-025**: The shared-store blast radius and per-control degraded-mode decision are documented and verified by
  test.
- **FR-026**: Per-request DB connections run through transaction-mode pooling without leaking tenant context
  across pooled connections; workspace isolation holds under the pooled configuration.

### Key Entities

- **Degraded-mode decision record** (doc): per-control behavior + recovery path (deny-set/spend = fail closed;
  idempotency = fail closed retryable; rate limit = fail open + alert).
- **Tenant GUC under pooling**: `app.workspace_id` set via `SET LOCAL` inside the request transaction (txn-mode
  safe), proven non-leaking by a cross-tenant negative test.

## Success Criteria *(mandatory)*

- **SC-008**: With the shared store unavailable, deny-set + spend reservation fail closed (0 requests allowed
  through unchecked) while rate limiting fails open with an alert; tenant isolation holds under transaction-mode
  pooling (0 cross-tenant reads in negative tests).

## Assumptions

- Redis SPOF status is accepted for the P1 pilot given these explicit, tested degraded behaviors + documented
  blast radius.
- Source: `docs/backend/09L §5.c`, findings F7/F8 adjacency in `docs/implementation-plan.md`.
