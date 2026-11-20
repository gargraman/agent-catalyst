# Checklist: Consistency & No-Drift (M5)

**Purpose**: Requirements-quality review of FE/BE parity, terminology consistency, and the existing-test blast radius.
**Created**: 2026-11-06
**Feature**: [spec.md](../spec.md)
**Depth**: Standard · **Audience**: implementer + reviewer (pre-implementation)

> Ownership: reviewer-owned. `[x]` = the requirement is judged complete/clear, not that it is implemented.

## Parity & Generation

- [x] CHK001 Is the parity requirement between the server map and `permissions.ts` specified and measurable (drift is detectable)? [Measurability, Spec §FR-010/SC-004]
- [x] CHK002 Is the generation approach specified clearly enough to implement — what is parsed (role arrays + `ROLE_PERMISSIONS`) and how `...SPREAD` references resolve? [Clarity, Spec §FR-010/Clarifications]
- [x] CHK003 Is the drift check's failure behavior specified (a clear signal when `permissions.ts` changes without regeneration)? [Clarity, Spec §US3 AC2]

## Terminology & Conflict

- [x] CHK004 Is terminology consistent (role names, permission names) across the spec, the operation→permission table, and the frontend source? [Consistency, Spec §Key Entities]
- [x] CHK005 Does the spec avoid conflicting statements about what a given role may do (FRs vs the operation table agree)? [Conflict, Spec §FR-005–007/table]
- [x] CHK006 Are the boundaries between this slice and the deferred access-policy / OIDC / Vault features stated so scope doesn't drift? [Consistency, Spec §Context/Assumptions]

## Blast Radius

- [x] CHK007 Is the existing-test blast radius explicitly identified (which test groups must move to publisher/admin claims)? [Completeness, Spec §FR-013]
- [x] CHK008 Is it specified that the full suite must pass under enforcement with no test depending on a lower role performing a higher-tier operation? [Measurability, Spec §SC-006]

## Notes

- CHK002 is the main implementation-risk surface: the spec must give enough of the parsing contract that the generator is unambiguous; the drift test (CHK001) is the backstop if the parse is wrong.
