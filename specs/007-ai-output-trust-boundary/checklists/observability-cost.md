# Checklist: Observability & Cost Honesty — AI-Output Trust Boundary (M2b)

**Purpose**: Requirements-quality review of the honesty/observability and cost-accounting requirements (constitution P7/P8).
**Created**: 2026-11-06
**Feature**: [spec.md](../spec.md)
**Depth**: Standard · **Audience**: implementer + reviewer (pre-implementation)

> Ownership: reviewer-owned. `[x]` = the requirement is judged complete/clear, not that it is implemented.

## One-Run-Per-Invocation (P8)

- [x] CHK001 Is the "exactly one honest Run record per invocation" invariant stated for ALL paths, including fail-closed and corrected invocations? [Completeness, Spec §FR-013, SC-005]
- [x] CHK002 Is `correction_turns` required to be observable on the Run with a defined baseline (0 when clean; equal to turns taken otherwise)? [Measurability, Spec §FR-014, SC-009]

## Cost Accounting (P7)

- [x] CHK003 Is the requirement that usage/cost be summed across ALL model turns (original + every correction) explicit and measurable? [Clarity, Spec §FR-012, SC-005]
- [x] CHK004 Is cost required to come from our own capability profiles (never trusted from transport) for the correction turns as well? [Consistency, Spec §FR-012, Constitution P7]
- [x] CHK005 Does the spec require that spend is still reported (usage accounted) even when the invocation fails closed, rather than dropped? [Gap/Clarity, Spec §FR-012]

## Honest Surfacing (P8)

- [x] CHK006 Is the typed-frame surfacing requirement (no raw 500, no truncated stream) specified for EVERY new outcome? [Coverage, Spec §FR-015, SC-006]
- [x] CHK007 Is there a requirement that no raw prompt/response content is persisted by the new states (metrics/identity only)? [Completeness, Spec §FR-019 / feature 006 SC-009, Constitution P5]

## Dependencies & Assumptions

- [x] CHK008 Is the persistence location/mechanism for `correction_turns` pinned at the requirements level, or explicitly deferred to the plan? [Traceability, Gap]

## Notes

- CHK005 (spend reported on fail-closed) and CHK008 (persistence of `correction_turns`) are currently decided in research/plan (D7/D9), not the spec body — a reviewer should decide whether the spec must state them for a P7/P8 audit trail.
