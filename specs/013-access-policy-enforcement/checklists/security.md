# Security Checklist: Per-Resource Access-Policy Enforcement

**Purpose**: Requirements-quality review of the security/authorization aspects of this feature ("unit tests for
the spec" — do the requirements, not the code, fully and unambiguously specify the fail-closed, no-leak behavior?)
**Created**: 2026-11-09
**Feature**: [spec.md](../spec.md)

**Review Ownership**: reviewer-owned. `[x]` = the requirements-quality criterion is satisfied (NOT that code is done).

## Fail-Closed Completeness

- [ ] CHK001 Are requirements defined for EVERY ambiguous policy shape (empty restricted side, missing lists, missing owner, unknown visibility) and do they all resolve to deny-for-non-admin? [Completeness, Spec FR-004/FR-005, edge cases]
- [ ] CHK002 Is the behavior on a malformed/unknown `visibility` value specified as most-restrictive (not open)? [Clarity, Spec edge cases, contracts rows 20–21]
- [ ] CHK003 Is the invoke/use denial specified to occur BEFORE any provider call or state change (no partial side effect)? [Clarity, Spec FR-010/SC-004]
- [ ] CHK004 Is admin bypass bounded to the workspace (never cross-tenant) and never conferred on an executer caller? [Consistency, Spec FR-006, Assumptions]

## No Existence Leak (P5)

- [ ] CHK005 Is the 404-vs-403 rule unambiguously tied to USE visibility for every boundary (list/read/manage/invoke)? [Clarity, Spec FR-008/FR-009, enforcement-http.md]
- [ ] CHK006 Do requirements state the 404 body is indistinguishable from a genuinely-absent resource (no stale-ETag or validation hint that confirms existence)? [Completeness, enforcement-http.md no-leak set]
- [ ] CHK007 Is it specified that list endpoints omit (not error on) resources the principal cannot use? [Completeness, Spec FR-008]

## Composition With Existing Controls

- [ ] CHK008 Is the evaluation order (RLS → RBAC → access policy) specified, with each layer's failure mode? [Consistency, plan R7/FR-011]
- [ ] CHK009 Is it specified that RBAC 403 (role-coarse, resource-independent) may precede policy evaluation without leaking a specific resource's existence? [Clarity, research R2]
- [ ] CHK010 Are requirements explicit that access policy ADDS to, never REPLACES, RBAC + membership + RLS? [Consistency, Spec FR-011]

## Identity, Teams & Owner

- [ ] CHK011 Is the source of a principal's teams specified (server `members.teams` under RLS), and are teams scoped per-workspace? [Completeness, Spec FR-016, Assumptions]
- [ ] CHK012 Is owner assignment on create-as-private specified so a private resource is never ownerless-and-unreachable (admin still repairs)? [Completeness, Spec FR-012, edge cases]
- [ ] CHK013 Is the executer caller's lack of owner identity specified to deny all `private` resources? [Clarity, Spec FR-007, contracts row 17]

## Secret / Data Handling (P5/P8)

- [ ] CHK014 Do requirements state deny-decision logs carry no token/secret and no sensitive policy content beyond the already-in-scope subject? [Completeness, Spec FR-014, enforcement-http.md observability]
- [ ] CHK015 Is it specified that a malformed `access_policy` on write is rejected (input.invalid), never silently stored as an open grant? [Clarity, data-model.md schema contracts]

## Contract Parity & Verifiability

- [ ] CHK016 Is the backend specified as the authoritative evaluator with the FE preview required to match the same decision table? [Consistency, Spec FR-013/SC-003]
- [ ] CHK017 Are the negative cross-principal scenarios enumerated as measurable, named-command tests (not prose)? [Measurability, Spec SC-001/SC-005, quickstart.md]
- [ ] CHK018 Is the P6 purity constraint (evaluator imports no web/DB framework; invoke enforcement in the router) stated as a verified invariant? [Clarity, plan R1/SC-005]

## Notes

- All items are requirements-quality questions, answerable from spec.md + plan.md + contracts/ without reading code.
- Leave unchecked any item the reviewer has not yet confirmed; `/speckit-implement` reads but never edits markers.
