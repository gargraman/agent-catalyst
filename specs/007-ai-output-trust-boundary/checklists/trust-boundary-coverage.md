# Checklist: Trust-Boundary Correctness & Edge Coverage — AI-Output Trust Boundary (M2b)

**Purpose**: Requirements-quality review of the trust-boundary's scenario/edge coverage — are all the correctness paths and boundary conditions specified?
**Created**: 2026-11-06
**Feature**: [spec.md](../spec.md)
**Depth**: Standard · **Audience**: implementer + reviewer (pre-implementation)

> Ownership: reviewer-owned. `[x]` = the requirement coverage is judged adequate, not that code passes.

## Primary & Alternate Flow Coverage

- [x] CHK001 Are requirements defined for the valid-first-try path (no correction) for both output and tool calls? [Coverage, Spec §US1 AC1 / §US2 AC1]
- [x] CHK002 Are requirements defined for the correction-succeeds-on-retry path (exactly one correction, then success)? [Coverage, Spec §US1 AC2 / §US2 AC2]
- [x] CHK003 Is the unknown-tool vs. bad-args distinction preserved as two distinct rejection reasons in the requirements? [Completeness, Spec §FR-005/FR-006]

## Exception / Fail-Closed Coverage

- [x] CHK004 Are requirements defined for the still-invalid-after-retry fail-closed path for BOTH defect types (`output_invalid`, `tool_call_rejected`)? [Coverage, Spec §US1 AC3 / §US2 AC3]
- [x] CHK005 Are requirements defined for a correction turn that itself errors (provider failure/timeout on the retry) resolving to an existing error outcome, not `success`? [Exception Flow, Spec §Edge Cases]
- [x] CHK006 Does the spec require that a rejected tool call is never surfaced as executable even when the budget is exhausted? [Coverage, Spec §FR-006, SC-002]

## Boundary / Edge Coverage

- [x] CHK007 Is the interaction between the correction loop and the invocation deadline specified (the deadline bounds all correction turns; a deadline mid-correction → `timeout`)? [Edge Case, Spec §FR-011, §Edge Cases]
- [x] CHK008 Is budget exhaustion specified precisely (which outcome, guaranteed termination, no unbounded loop)? [Edge Case, Spec §FR-010, SC-004, Clarifications]
- [x] CHK009 Are requirements defined for mixed defects (output + tool) drawing on ONE shared correction budget? [Edge Case, Spec §Edge Cases]
- [x] CHK010 Are requirements defined for json-mode with no declared schema (parse-only) vs. with a schema? [Coverage, Spec §Edge Cases]
- [x] CHK011 Are requirements defined for an empty/content-free but valid response (e.g., tool-call-only) so it is NOT mistaken for a defect? [Edge Case, Spec §Edge Cases]

## Consistency & Suppression

- [x] CHK012 Is the streaming-suppression requirement unambiguous about exactly what reaches the caller (final validated result + one typed terminal) vs. what is recorded (all turns)? [Clarity, Spec §FR-015a, Clarifications]
- [x] CHK013 Do the streaming and collecting entry points have a stated requirement to produce identical outcomes for equivalent inputs? [Consistency, Spec §FR-016, SC-008]

## Notes

- CHK005 (retry-itself-errors) and CHK011 (valid tool-call-only response) are the subtle paths most likely under-specified; a reviewer should confirm the spec, not just the plan, pins them.
