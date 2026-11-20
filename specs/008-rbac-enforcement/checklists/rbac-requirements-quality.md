# Checklist: RBAC Requirements Completeness & Clarity (M5)

**Purpose**: Requirements-quality review ("unit tests for the spec") — is the role/permission model and its enforcement behavior fully and unambiguously specified before implementation?
**Created**: 2026-11-06
**Feature**: [spec.md](../spec.md)
**Depth**: Standard · **Audience**: implementer + reviewer (pre-implementation)

> Ownership: reviewer-owned. `[x]` = the reviewer judged the requirements-quality criterion satisfied — NOT that implementation is done. `/speckit-implement` reads but never modifies these markers.

## Requirement Completeness

- [x] CHK001 Are all four roles and their exact permission sets specified (viewer/builder/publisher/admin)? [Completeness, Spec §Key Entities/FR-001]
- [x] CHK002 Is a required permission assigned for every workspace-scoped route (no route left unmapped)? [Completeness, Spec §Operation→permission table/FR-002]
- [x] CHK003 Is the 403 error-envelope content specified (code `access.denied` + a detail naming the required permission, no sensitive leak)? [Completeness, Spec §FR-009]
- [x] CHK004 Are the scope exclusions (per-resource access policy, OIDC/member-sourced roles, Vault `key.manage` endpoint) stated explicitly rather than silently omitted? [Completeness, Spec §Context/FR-011]

## Requirement Clarity

- [x] CHK005 Is "fail-closed" defined precisely — unknown/empty/null role resolves to zero permissions and is denied? [Clarity, Spec §FR-004]
- [x] CHK006 Is "deny-by-default" specified as a structural guarantee (every route MUST declare a permission, enforced by a coverage check), not a convention? [Clarity, Spec §FR-014]
- [x] CHK007 Is the role source unambiguous (the resolved principal claim) with the alternative (members/OIDC) explicitly deferred? [Clarity, Spec §FR-008/Clarifications]
- [x] CHK008 Is the ordering of the role check relative to resource load unambiguously specified (403 before 404)? [Clarity, Spec §FR-015]

## Requirement Consistency & Traceability

- [x] CHK009 Does each privileged operation sit at the correct tier (publish=publisher, members/settings=admin, create/edit=builder) consistently between the FRs and the operation→permission table? [Consistency, Spec §FR-005–007/table]
- [x] CHK010 Is a stable requirement-ID scheme (FR-/SC-) used and referenced by the acceptance scenarios and clarifications? [Traceability]

## Notes

- CHK002 is load-bearing: the contract is only complete if **every** route has a permission; the plan enumerates all 47 — a reviewer should confirm none is missing or mis-tiered.
