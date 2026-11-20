# Checklist: Authorization Correctness & Coverage (M5)

**Purpose**: Requirements-quality review of the authorization scenario/edge coverage — are all the allow/deny paths and safety properties specified?
**Created**: 2026-11-06
**Feature**: [spec.md](../spec.md)
**Depth**: Standard · **Audience**: implementer + reviewer (pre-implementation)

> Ownership: reviewer-owned. `[x]` = the requirement coverage is judged adequate, not that code passes.

## Deny / Allow Matrix Coverage

- [x] CHK001 Are requirements defined for the full per-tier **deny** matrix (viewer↛write, builder↛publish/delete/members/settings, publisher↛members/settings)? [Coverage, Spec §US1]
- [x] CHK002 Are requirements defined for the **allow** cases at each tier (builder creates/evals, publisher publishes, admin manages members/settings)? [Coverage, Spec §US1 AC4–5]
- [x] CHK003 Is the least-privilege read requirement defined (a `view`/`playground.use` holder can read + use playground but not mutate)? [Coverage, Spec §US2 AC1]

## Edge / Safety Coverage

- [x] CHK004 Is unknown-role fail-closed defined for BOTH reads and writes (not just writes)? [Edge Case, Spec §US2 AC2/SC-003]
- [x] CHK005 Is the no-existence-leak property defined (under-privileged caller gets 403 whether or not the resource exists; 403 before 404)? [Edge Case, Spec §FR-015/Edge Cases]
- [x] CHK006 Is the no-side-effect-on-denial property defined (a denied call performs no mutation / idempotency-guarded effect)? [Edge Case, Spec §FR-003/Edge Cases]
- [x] CHK007 Is denial surfacing for the streaming invoke route addressed (a 403 before the SSE stream begins, not a mid-stream frame)? [Gap, Spec §Operation table/invoke]

## Composition & Measurability

- [x] CHK008 Is tenancy composition defined (role check composes with membership + RLS; a valid role in an unheld workspace is still 403)? [Consistency, Spec §FR-008/US2 AC3]
- [x] CHK009 Is the deny-by-default coverage requirement measurable (every `/api/v1` route enumerable; an unannotated route detectable)? [Measurability, Spec §SC-007]
- [x] CHK010 Are the permissions that exist in the model but gate no current endpoint (`channel.manage`, `eval.author`, `eval.govern`, `key.manage`, `agent.delete`) accounted for as intentional (model completeness, not dead requirements)? [Coverage/Gap, Spec §Assumptions]

## Notes

- CHK005 and CHK007 are the subtle ones: 403-before-404 (no existence leak) and 403-before-stream for SSE invoke are both easy to get wrong; a reviewer should confirm the spec pins them, not just the plan.
