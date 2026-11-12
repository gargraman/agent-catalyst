# Feature Specification: M5 — Rate Limiting (F5)

**Feature Branch**: `feat/backend-m0-foundation` (feature dir `016-m5-rate-limiting`)

**Created**: 2026-11-11

**Status**: Draft · **Priority**: P1 · **Parent**: [`015-m5-security-completion`](../015-m5-security-completion/spec.md) (M5 umbrella)

**Input**: Split from the M5-completion epic (sub-feature 015a). Closes un-built **High**-severity finding **F5**.

> **Shared design** lives in the umbrella: [plan](../015-m5-security-completion/plan.md) ·
> [research R1/R2](../015-m5-security-completion/research.md) ·
> [data-model (Redis structures)](../015-m5-security-completion/data-model.md) ·
> [contract](../015-m5-security-completion/contracts/rate-limit.md). This slice **introduces the shared async
> Redis client** that 018 and 021 depend on.
> **Dependencies**: none (first slice). **Blocks**: 018 (deny-set), 021 (degraded-mode).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Published endpoints and the console can't be hammered or abused (Priority: P1)

An application team hands a caller token to a downstream service; a bug or abuse sends a burst of invoke calls.
Separately a builder triggers many expensive console operations (playground, eval, ingestion). The platform
throttles both surfaces per-identity so one caller cannot exhaust provider quota, run up spend, or starve other
tenants — and every throttled caller is told when to retry.

**Why this priority**: F5 is an un-built High-severity hole; the console triggers the most expensive ops and has
no rate limiting today (`rate_limit()` is a no-op seam at `api/deps.py:139`, wired to 9 routes).

**Independent Test**: Drive a caller token and a console route past their window thresholds; assert over-limit
requests are rejected with retry guidance while a second caller/token is unaffected.

**Acceptance Scenarios**:

1. **Given** a per-identity limit of N/window (keyed per principal in this slice), **When** N+1 invoke requests
   arrive in one window, **Then** the (N+1)th returns `429 rate.limited` with `Retry-After` + `X-RateLimit-*`,
   and no provider call is made.
2. **Given** an expensive console route (playground/eval/ingestion), **When** the per-principal limit is
   exceeded, **Then** the request is rejected before any model/ingestion work begins.
3. **Given** two distinct tokens on the same endpoint, **When** one is throttled, **Then** the other succeeds.
4. **Given** a throttled caller, **When** the window elapses, **Then** requests succeed again automatically.
5. **Given** Redis is unavailable, **When** the limiter runs, **Then** it **fails open and raises an alert**
   (availability control — must not self-inflict an outage).

### Edge Cases

- A caller alternates two tokens to evade a per-token limit — limits are per-identity; total expensive work is
  still bounded by spend caps + provider breakers (F7/F8, out of this slice).
- A burst exactly at a window boundary must not double-count (sliding window, not fixed).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Enforce a per-identity sliding-window rate limit on the published invoke path, rejecting over-limit
  requests before any provider call. **This slice keys per *principal*; per-*token* keying lands in 018** when
  the caller-token identity (`resolve_caller`) exists — the engine is built identity-agnostic so 018 re-keys it
  with no rework.
- **FR-002**: Enforce per-principal rate limits on expensive console routes (≥ playground, eval, ingestion),
  rejecting before expensive work begins.
- **FR-003**: Maintain a reviewable limits table (window + threshold per surface) with conservative defaults.
- **FR-004**: A throttled request returns `429 rate.limited` with `Retry-After` and `X-RateLimit-*` headers.
- **FR-005**: Limits are scoped per identity (one caller's throttling never affects another's).
- **FR-024a** (rate-limit portion): When Redis is unavailable, rate limiting **fails open and raises an alert**
  (security controls fail closed — that is 018/021, not here).
- **FR-028** (cross-cutting): No token/session secret leaks into logs, traces, or exports.

### Key Entities

- **Rate-limit counter** (Redis, transient): per-identity, per-surface sliding-window count. Keys
  `rl:token:{id}`, `rl:prin:{subject}:{route_class}`.
- **Shared Redis client** (new infra): async pool from the existing `AC_REDIS_URL` setting; health surfaced for
  readiness.

## Success Criteria *(mandatory)*

- **SC-001**: 100% of over-limit requests on any protected surface are rejected with retry guidance before any
  provider/ingestion work.
- **SC-002**: A single caller cannot exceed its configured share in a window; a second caller is unaffected in
  100% of tested cases.
- **SC-016a**: With Redis down, rate-limited requests are allowed and an alert is emitted (0 self-inflicted
  rejections from the limiter's own infra failure).

## Assumptions

- Thresholds are conservative defaults, tunable via the limits table against pilot traffic (the testable gate is
  "limits exist and fire before expensive work," not a specific number).
- `AC_REDIS_URL` unset in dev/test → limiter no-op-allows (parity with today's seam); integration tests are
  Redis-optional.
- Source: `docs/backend/09 §6`, finding F5 in `docs/implementation-plan.md`.
