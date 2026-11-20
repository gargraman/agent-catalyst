# Observability & Runs/Metrics Requirements Checklist: M4-remainder — Resource CRUD + Frontend Sync

**Purpose**: Validate that run-record capture and metrics-aggregation *requirements* are complete, measurable, and
honest (P7/P8) before implementation. Tests the spec, not the code.
**Created**: 2026-11-05
**Feature**: [spec.md](../spec.md)

**Note**: Reviewer-owned. `[x]` = reviewer approval of requirements quality, not implementation completion.

## Requirement Completeness

- [x] CHK001 - Are the run-record fields enumerated completely (model/profile identity, version/prompt identity, usage, latency, cost, outcome, correlation id, source, workspace)? [Completeness, Spec §FR-013]
- [x] CHK002 - Is the requirement to record a run on FAILURE (not only success) explicitly stated? [Coverage, Spec §FR-013, Constitution P8]
- [x] CHK003 - Are run-history requirements specified (newest-first ordering, pagination, workspace scope)? [Completeness, Spec §FR-014]
- [x] CHK004 - Is correlation-id propagation from invoke → run record specified? [Completeness, Constitution P8]

## Requirement Clarity

- [x] CHK005 - Is "outcome" defined with an explicit value set (e.g. ok/error/refused/timeout)? [Clarity, data-model]
- [x] CHK006 - Is the cost-source requirement specified (from the runtime RunRecord / our profiles, never recomputed from transport)? [Clarity, Spec §FR-013, Constitution P7]
- [x] CHK007 - Are the dashboard metrics defined (what spend-trend/eval-trend/call-volume mean, the time window, bucketing)? [Ambiguity, Spec §FR-015]
- [x] CHK008 - Is the boundary between this run store and the deferred M6 observability stack specified? [Clarity, research D5]

## Requirement Consistency

- [x] CHK009 - Is workspace-scoping required for both runs and metrics (no cross-tenant aggregation), consistent with FR-016? [Consistency, Spec §FR-015/FR-016]
- [x] CHK010 - Is the metrics source consistent with the run-record fields (every metric derivable from stored run data)? [Consistency]

## Edge Case & Coverage

- [x] CHK011 - Are requirements defined for empty-state metrics (no runs yet)? [Edge Case, Gap]
- [x] CHK012 - Is the relationship between eval-runs and the runs/metrics store specified (does an eval produce run records)? [Ambiguity, Gap]
- [x] CHK013 - Are run-record retention/deletion requirements defined? [Gap, Constitution P5]

## Acceptance Criteria Quality

- [x] CHK014 - Can the metrics requirements be objectively reconciled against the recorded runs? [Measurability, Spec §SC-007]
- [x] CHK015 - Is degradation honesty specified (metrics must not fabricate values when run data is incomplete)? [Clarity, Constitution P8]

## Traceability

- [x] CHK016 - Is each metric traceable to the specific run-record fields it aggregates? [Traceability]
