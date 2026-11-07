# Feature Specification: Durable Monthly Workspace Spend Cap

**Feature Branch**: `feat/backend-m0-foundation` (slice 009; no per-feature branch)

**Created**: 2026-11-07

**Status**: Draft

**Input**: User description: "M6 slice — Durable monthly workspace spend cap (constitution finding F7). Enforce a per-workspace month-to-date USD spend ceiling in code (deterministic, fail-closed) before any paid LLM invocation; reject over-cap calls with 402 budget.exceeded before any provider call or SSE stream; durable source of truth is the Postgres run-cost ledger (Redis eviction must not un-cap); API-layer only; frontend contract kept in sync."

## Clarifications

### Session 2026-11-07

- Q: When month-to-date spend cannot be determined, refuse or allow the invocation? → A: Fail-closed —
  refuse (deny on inability to compute spend; aligns with P3 fail-closed and F7's never-silently-un-cap
  intent).
- Q: Should a budget-refused invocation produce a durable Run record? → A: No Run row — treat it as a
  request-admission denial (like an authorization refusal); surface it via the `402` error plus a
  structured log/metric. P8's "one Run per invocation" is read as "per invocation that actually ran".

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Over-cap workspace is blocked before spending (Priority: P1)

A workspace has reached (or exceeded) its configured monthly spend limit for the current
calendar month. A member tries to run an agent — from the playground, a published invoke, or
an eval run. Instead of making another paid model call, the platform refuses the request up
front and tells the caller the budget is exhausted, naming the cap and the amount already spent.
No further money is spent on that workspace this month through normal admission.

**Why this priority**: This is the entire point of the feature and the F7 safety requirement — an
enterprise pilot that makes real paid model calls must not be able to run up an unbounded bill.
Without this, a runaway loop, a misconfigured automation, or simple heavy use silently burns the
budget. This is the one slice that delivers the value; everything else supports it.

**Independent Test**: Seed a workspace whose recorded run-cost for the current calendar month is
at/over its monthly cap, then call the invoke endpoint and the eval-run endpoint. Both must be
refused with the budget-exceeded error *before* any model/provider call and *before* any streamed
output begins. Verifiable with no live/paid call (the provider is mocked; the point is it is never
reached).

**Acceptance Scenarios**:

1. **Given** a workspace whose month-to-date recorded spend equals its monthly cap, **When** a
   member invokes an agent, **Then** the request is refused with a `402 budget.exceeded` error and
   no provider call and no stream output occur.
2. **Given** the same over-cap workspace, **When** a member starts an eval run, **Then** it is
   refused with the same budget-exceeded error before any paid call.
3. **Given** an over-cap workspace, **When** the refusal is returned, **Then** the error payload
   names the cap and the month-to-date spend so the caller understands why.
4. **Given** an over-cap workspace, **When** an invocation is refused, **Then** no new run record is
   created for the refused call (it never ran), consistent with how authorization refusals behave.

---

### User Story 2 - Under-cap work is unaffected (Priority: P1)

A workspace is comfortably under its monthly spend limit. Members invoke agents and run evals
exactly as before — the spend cap adds no friction, no false refusals, and no change to the normal
streaming/response behavior.

**Why this priority**: A safety control that blocks legitimate under-budget work is worse than no
control — it breaks the product. The cap must be invisible until it actually bites. US1 and US2
together are the MVP.

**Independent Test**: Seed a workspace whose month-to-date spend is below its cap; invoke and run an
eval; both proceed normally (stream/verdict as today), and the run is recorded as usual.

**Acceptance Scenarios**:

1. **Given** a workspace under its monthly cap, **When** a member invokes an agent, **Then** the
   invocation proceeds and streams normally and a run record is written as before.
2. **Given** a workspace with spend from a *previous* calendar month at/over the cap but this
   month's spend under the cap, **When** a member invokes, **Then** the invocation proceeds (the cap
   is month-to-date, not lifetime).
3. **Given** a workspace with no configured cap, **When** a member invokes, **Then** the system
   default monthly cap applies and under-default work proceeds.

---

### User Story 3 - Spend is observable against the cap (Priority: P2)

An operator or admin can see how much a workspace has spent so far this calendar month and how that
compares to its monthly cap — so they can raise the cap, investigate, or warn members before work is
blocked, rather than discovering the ceiling only when calls start failing.

**Why this priority**: Enforcement without visibility is operationally hostile — people hit a wall
with no warning. Valuable, but the cap itself (US1/US2) must land first; a number on a screen is
useless if the ceiling isn't actually enforced.

**Independent Test**: For a workspace with known recorded run-costs this month, request the spend
view and confirm it reports the month-to-date spend and the active cap consistent with what the
enforcement path uses.

**Acceptance Scenarios**:

1. **Given** a workspace with recorded spend this month, **When** an authorized caller requests the
   workspace spend view, **Then** it returns the month-to-date spend and the active monthly cap.
2. **Given** the spend view and the enforcement decision for the same workspace at the same instant,
   **Then** they agree on the month-to-date figure and the cap (one source of truth).

---

### Edge Cases

- **Exactly at the cap**: month-to-date spend equal to the cap blocks further invocations (the cap
  is inclusive — reaching it exhausts it).
- **Cap of zero**: a workspace configured with a zero monthly cap blocks all paid invocations.
- **Unset cap**: a workspace with no configured monthly cap falls back to the documented system
  default (not "unlimited").
- **Calendar-month rollover**: at the start of a new calendar month (UTC), month-to-date spend
  effectively resets — prior-month spend no longer counts toward the current cap.
- **Cache eviction**: any fast-path cache of the month-to-date figure being cleared/evicted MUST NOT
  raise the effective ceiling — the durable ledger remains authoritative (F7).
- **Spend cannot be determined**: if the platform cannot compute month-to-date spend for a
  workspace, the admission decision is **fail-closed** — the invocation is refused (FR-009).
- **In-flight concurrency overshoot**: because admission is judged against already-*recorded* spend,
  several invocations admitted concurrently can collectively push spend past the cap by a bounded
  amount; this is a documented limitation of this slice (see Assumptions), not unbounded.
- **Composition with authorization**: a caller lacking permission or workspace membership is refused
  by those controls before the budget is ever consulted (no information leak via the budget error).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST refuse a paid agent invocation when the caller's workspace month-to-date
  spend is at or above the workspace's active monthly spend cap, returning the `402 budget.exceeded`
  error envelope, and MUST do so **before** any provider/model call and **before** any streamed output
  is emitted.
- **FR-002**: The budget control MUST apply to every path that spends real money through the single
  runtime invocation — at minimum the interactive invoke path and the eval-run path — with identical
  semantics.
- **FR-003**: Month-to-date spend MUST be derived from the durable recorded-run cost ledger for the
  workspace within the current calendar month (UTC). Eviction or loss of any non-durable cache MUST
  NOT increase the effective ceiling (durability — F7).
- **FR-004**: The active cap MUST be the workspace's configured monthly spend limit; when unset, a
  documented system default MUST apply. Cross-workspace spend MUST never count toward a workspace's
  cap (workspace-scoped).
- **FR-005**: The refusal MUST be a deterministic, in-code control. Model instructions or UI gating
  MUST NOT be relied upon for enforcement (constitution P3).
- **FR-006**: The budget control MUST compose with tenancy and role-based authorization: callers who
  fail authentication, membership, or permission checks are refused by those controls first; the
  budget decision is evaluated only for an otherwise-authorized caller, and the budget error MUST NOT
  reveal the existence of resources the caller could not otherwise see.
- **FR-007**: The budget-exceeded error payload MUST include enough machine-readable context to
  explain the refusal — at minimum the active cap and the month-to-date spend for the workspace.
- **FR-008**: The system MUST expose, to an authorized caller, the workspace's current month-to-date
  spend together with its active monthly cap, consistent with the figure used for enforcement.
- **FR-009**: When the system cannot determine a workspace's month-to-date spend (e.g. the ledger
  query fails), it MUST **fail closed** — refuse the invocation rather than allow an un-capped call
  (constitution P3 fail-closed; F7 never-silently-un-cap). *(Clarified 2026-11-07.)*
- **FR-010**: The pure runtime library MUST remain free of HTTP and database dependencies; the budget
  control MUST live in the service/API layer (constitution P6 invariant preserved).
- **FR-011**: A refused invocation MUST NOT create a run record (it never executed), consistent with
  authorization refusals; the refusal is surfaced via the error response plus a structured
  log/metric, never a silent drop. *(Clarified 2026-11-07: no Run row; log/metric only.)*
- **FR-012**: The frontend contract MUST stay in sync: the single monthly-cap field already modeled
  (`Limits.monthlySpendUsd`) remains the cap of record, and the frontend's invoke/playground error
  contract MUST model the `402 budget.exceeded` refusal. (Frontend changes are contract-level and
  cannot be build/lint-verified in this environment — the Node toolchain is unavailable — and will be
  reported as build-unverified.)

### Key Entities *(include if feature involves data)*

- **Workspace monthly spend cap**: the per-workspace ceiling (USD) for a calendar month; stored with
  the workspace's operating limits; falls back to a system default when unset.
- **Month-to-date spend**: a derived figure — the sum of recorded run costs for the workspace since
  the start of the current calendar month (UTC). Not a stored counter; computed from the durable run
  ledger so it cannot drift or be reset by cache loss.
- **Run cost record**: the existing per-run recorded cost (identity + metrics only) that is the
  authoritative, durable ledger from which month-to-date spend is summed.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For a workspace at or above its monthly cap, 100% of new paid invocations (invoke and
  eval-run) are refused at admission, with zero provider calls made for those refused requests (aside
  from the documented bounded in-flight overshoot).
- **SC-002**: Every budget refusal occurs before any streamed output and before any provider call —
  verifiable by the absence of any stream frames and any provider interaction on a refused request.
- **SC-003**: With any fast-path spend cache cleared, an over-cap workspace is still refused — the
  ceiling holds on the durable ledger alone (F7 durability).
- **SC-004**: An authorized caller can retrieve a workspace's month-to-date spend and active cap, and
  that figure matches the value the enforcement path uses at the same time.
- **SC-005**: Workspaces under their cap experience zero budget-related refusals and no change to
  normal invoke/eval behavior (no false positives below the cap).
- **SC-006**: The invoke path and the eval-run path enforce the cap identically (same threshold, same
  error, same pre-call timing).
- **SC-007**: The pure runtime library remains import-clean of HTTP/DB after the change (P6 check
  stays green).

## Assumptions

- **Calendar month, UTC**: "monthly" means the current calendar month boundary in UTC, matching how
  recorded run timestamps are stored. (Per-workspace billing cycles / timezones are out of scope.)
- **Inclusive threshold**: reaching the cap exactly exhausts it (spend ≥ cap blocks); the next call
  at exactly the cap is refused.
- **Durable ledger is the recorded run cost**: the existing per-run cost is trustworthy and complete
  enough to sum as the spend ledger; this feature does not introduce a separate spend table or
  migration.
- **Admission-time check, bounded overshoot**: the cap is enforced at request admission against
  already-recorded spend. Concurrent in-flight invocations can overshoot the cap by at most
  (number of concurrent in-flight runs × the largest single-run cost). Tightening this with an atomic
  in-flight reservation (e.g. a durable reservation counter) is **deferred to P2** and documented, not
  built here.
- **Scope — workspace cap only**: per-agent spend caps (`agent.config.spend.monthlyCapUsd`), hard
  per-request cost preflight, and the heavy observability stack (distributed tracing / analytics
  warehouse) are **out of scope** for this slice and deferred.
- **No new dependencies, no migration**: the cap value and the cost ledger already exist in the data
  model; this slice adds enforcement and a read surface only.
- **Authorization precedes budget**: authentication/membership/permission failures are resolved
  before the budget decision, so the budget error is only ever seen by an otherwise-authorized caller.
- **Frontend is mock-only and Node-unavailable**: frontend edits keep the type/error contract in sync
  but are not compiled or linted in this environment; they are flagged build-unverified per the
  standing honesty requirement.
