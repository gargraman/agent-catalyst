# Frontend-Sync & Honesty Requirements Checklist: M4-remainder — Resource CRUD + Frontend Sync

**Purpose**: Validate that the client-generation, hook-swap, and mock-vs-real tracker *requirements* are clear,
measurable, and honest (P1/P9) before implementation. Tests the spec, not the code.
**Created**: 2026-11-05
**Feature**: [spec.md](../spec.md)

**Note**: Reviewer-owned. `[x]` = reviewer approval of requirements quality, not implementation completion.

## Requirement Completeness

- [x] CHK001 - Is the "representative set of hooks" defined by an explicit list rather than a vague sample? [Ambiguity, Spec §FR-006]
- [x] CHK002 - Are requirements specified for preserving existing hook/function shapes (drop-in) during the swap? [Completeness, Spec §FR-006]
- [x] CHK003 - Is the tracker's required content specified (per-seam real/mock classification, grouped by resource, totals reconciling with the `@backend` grep count)? [Completeness, Spec §FR-007/SC-004]
- [x] CHK004 - Are loading/empty/error-state preservation requirements specified for swapped pages? [Coverage, Spec §US2]

## Requirement Clarity

- [x] CHK005 - Is "zero drift" for the generated client defined with a measurable check (regeneration produces no diff)? [Measurability, Spec §FR-005/SC-003]
- [x] CHK006 - Are the criteria for classifying a seam REAL vs MOCK defined unambiguously? [Ambiguity, Spec §FR-007]
- [x] CHK007 - Is the FE verification method specified given there is no unit harness (build/lint/manual render)? [Clarity, research D10]
- [x] CHK008 - Is the scope boundary (representative hooks, NOT all ~253 seams) stated unambiguously? [Clarity, Spec Assumptions]

## Requirement Consistency

- [x] CHK009 - Is the generated client required to derive solely from the OpenAPI source of truth (single source)? [Consistency, Spec §FR-005]
- [x] CHK010 - Is the honesty requirement (no mock seam labeled implemented) consistent with the constitution P1/P9 and stated as verifiable? [Consistency, Spec §SC-004]

## Edge Case & Coverage

- [x] CHK011 - Are requirements defined for the advisory-publish divergence in the swapped eval/publish UI (show verdict, do not hard-block)? [Gap, M4a divergence — is it carried into the 006 FE?]
- [x] CHK012 - Is the modified-Next.js prerequisite (read bundled docs before FE edits) captured as a requirement/assumption? [Dependency, Assumption, frontend/AGENTS.md]
- [x] CHK013 - Are requirements defined for keeping the tracker accurate over time (when/how it is regenerated)? [Gap]

## Acceptance Criteria Quality

- [x] CHK014 - Are the specific pages that must render real data to count as "done" identified and measurable? [Measurability, Spec §SC-003]
- [x] CHK015 - Can the tracker's completeness be objectively verified (totals reconcile with the grep seam count)? [Measurability, Spec §SC-004]

## Traceability

- [x] CHK016 - Is each swapped hook traceable to its backing endpoint and FR? [Traceability]
