# Requirements-Quality Checklist: Durable Monthly Workspace Spend Cap

**Purpose**: Pre-implementation "unit tests for the spec" — validate that the spend-cap requirements are
complete, clear, consistent, and measurable before `/speckit-implement`.
**Created**: 2026-11-07
**Feature**: [spec.md](../spec.md)
**Audience**: implementer + reviewer

> Ownership: this is a reviewer-owned requirements-quality artifact. `[x]` means the reviewer judged the
> criterion satisfied by the *requirements* (not that code works). `/speckit-implement` reads but does not
> modify these markers. Items are intentionally left unchecked.

## Enforcement Correctness & Coverage

- [ ] CHK001 - Is the blocking threshold specified unambiguously as inclusive (spend ≥ cap blocks, not >)? [Clarity, Spec §FR-001, §Assumptions, §Edge Cases]
- [ ] CHK002 - Is the month window defined with an explicit boundary and timezone (calendar month, UTC, from first-of-month 00:00)? [Clarity, Spec §FR-003, §Assumptions]
- [ ] CHK003 - Is "before any provider call AND before any streamed output" stated as a timing requirement, not merely "reject"? [Completeness, Spec §FR-001, §SC-002]
- [ ] CHK004 - Are BOTH paid paths (interactive invoke and eval-run) required to enforce identically (same threshold, error, timing)? [Coverage, Spec §FR-002, §SC-006]
- [ ] CHK005 - Is the fail-closed behavior when month-to-date spend cannot be determined stated as a MUST with a single unambiguous outcome (refuse)? [Clarity, Spec §FR-009]
- [ ] CHK006 - Is the cap source (workspace `monthlySpendUsd`) and its fallback (documented system default, not "unlimited") fully specified for the unset case? [Completeness, Spec §FR-004, §Edge Cases]
- [ ] CHK007 - Are the zero-cap and exactly-at-cap boundary conditions explicitly addressed? [Edge Case, Spec §Edge Cases]
- [ ] CHK008 - Is the calendar-month rollover (prior-month spend ceases to count) specified as a requirement rather than left implicit? [Completeness, Spec §FR-003, §Edge Cases]
- [ ] CHK009 - Is the error surfaced on refusal named precisely (402 `budget.exceeded`) and its machine-readable detail (cap + month-to-date) required? [Clarity, Spec §FR-007, Contract §1]

## Durability (F7) & Honest Limitations

- [ ] CHK010 - Is "eviction/loss of any non-durable cache MUST NOT raise the effective ceiling" written as a testable requirement? [Measurability, Spec §FR-003, §SC-003]
- [ ] CHK011 - Is the durable source of truth identified as the recorded-run cost ledger (not an evictable counter)? [Clarity, Spec §FR-003, §Key Entities]
- [ ] CHK012 - Is month-to-date spend specified as a derived figure (summed on demand), not a stored counter that could drift or reset? [Consistency, Spec §Key Entities]
- [ ] CHK013 - Is the bounded in-flight concurrency overshoot documented honestly as a known limitation, with the bound quantified (concurrent runs × largest single-run cost)? [Completeness, Spec §Assumptions, §Edge Cases]
- [ ] CHK014 - Is the Redis atomic in-flight reservation explicitly marked out of scope / deferred rather than silently omitted? [Assumption, Spec §Assumptions]
- [ ] CHK015 - Are other deferrals (per-agent cap, per-request preflight, heavy observability stack) enumerated so scope is bounded? [Clarity, Spec §Assumptions]

## Composition, Tenancy & No-Leak

- [ ] CHK016 - Is the control ordering (authentication → membership → role → budget) specified so 403 precedes 402? [Consistency, Spec §FR-006, §Edge Cases, data-model §Composition]
- [ ] CHK017 - Is it required that the budget error is reachable only by an otherwise-authorized caller (no existence leak via the budget path)? [Completeness, Spec §FR-006]
- [ ] CHK018 - Is workspace-scoping stated as a requirement — a tenant's spend never counts toward another's cap? [Coverage, Spec §FR-004, §SC-005 (US2 cross-tenant), data-model §Tenancy]
- [ ] CHK019 - Is "no Run record is created for a refused (non-executed) invocation" specified, and reconciled against P8's "one Run per invocation"? [Consistency, Spec §FR-011, §Clarifications]
- [ ] CHK020 - Is the refusal required to be observable (error + structured log/metric) rather than a silent drop? [Completeness, Spec §FR-011]
- [ ] CHK021 - Is the observability read surface required to report the SAME month-to-date figure and cap the enforcement path uses (one source of truth)? [Consistency, Spec §FR-008, §SC-004]
- [ ] CHK022 - Is the read surface's authorization requirement specified (an authorized caller, workspace-scoped like other routes)? [Completeness, Contract §2]

## Cross-Cutting Requirement Quality

- [ ] CHK023 - Is the runtime-purity constraint (control lives in the service layer; runtime stays HTTP/DB-free) stated as a requirement with a verification (SC-007)? [Measurability, Spec §FR-010, §SC-007]
- [ ] CHK024 - Are all functional requirements traceable to at least one measurable success criterion? [Traceability, Spec §Success Criteria]
- [ ] CHK025 - Are the no-migration / no-new-dependency constraints stated so the implementation scope cannot silently expand? [Clarity, Spec §Assumptions]
- [ ] CHK026 - Is the frontend-sync obligation scoped precisely (model 402 `budget.exceeded`; keep `monthlySpendUsd` as the cap of record) and honestly flagged build-unverified? [Clarity, Spec §FR-012]

## Notes

- Scope: money-safety enforcement only; UX/frontend/accessibility intentionally excluded (FE is
  contract-only and build-unverified in this environment).
- Depth: Standard. All items are requirements-quality questions — none assert implementation behavior.
