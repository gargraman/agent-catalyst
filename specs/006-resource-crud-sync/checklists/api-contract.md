# API & Data Contract Requirements Checklist: M4-remainder — Resource CRUD + Frontend Sync

**Purpose**: Validate that CRUD, error-envelope, pagination, concurrency/idempotency, and data-model constraint
*requirements* are complete, measurable, and consistent before implementation. Tests the spec, not the code.
**Created**: 2026-11-05
**Feature**: [spec.md](../spec.md)

**Note**: Reviewer-owned. `[x]` = reviewer approval of requirements quality, not implementation completion.
`/speckit-implement` reads but does not modify these markers.

## Requirement Completeness

- [x] CHK001 - Are CRUD operations fully enumerated for each resource (create/get/list/patch/archive **and restore**)? [Completeness, Spec §FR-001/FR-003]
- [x] CHK002 - Is "restore" (clear archived_at) specified as a requirement, or does the spec only define archive? [Gap, Spec §FR-003]
- [x] CHK003 - Are optimistic-concurrency requirements (ETag source, If-Match required on PATCH, conflict.stale on mismatch) specified for every mutable resource? [Completeness, Spec §FR-017]
- [x] CHK004 - Is the pagination contract (cursor keyset on created_at+id, newest-first, stable ordering) specified for all list endpoints? [Completeness, Spec §FR-018]
- [x] CHK005 - Are error-envelope codes/shapes enumerated for every failure mode per endpoint? [Completeness, Spec §FR-018]
- [x] CHK006 - Are uniqueness constraints specified (tool name per workspace, skill slug per workspace, member subject per workspace)? [Completeness, data-model]

## Requirement Clarity

- [x] CHK007 - Are the drift-trigger fields enumerated unambiguously (tool auth-mode/scopes; skill instructions/resources/version/content-hash; KB content/retrieval config)? [Clarity, Spec §FR-002]
- [x] CHK008 - Is "cosmetic field" defined by enumeration so the non-triggering set is unambiguous? [Ambiguity, Spec §FR-002]
- [x] CHK009 - Are idempotency semantics specified (key scope, in-flight vs completed behavior, replay response) beyond "honor a key"? [Clarity, Spec §FR-017]
- [x] CHK010 - Is the soft-archive visibility rule (hidden by default / shown via include-archived / still fetchable by id) stated precisely? [Clarity, Spec §FR-003]
- [x] CHK011 - Is the skill publish transition (draft→published, unsigned provenance) specified, including what "published" enables? [Clarity, Spec §FR-004]
- [x] CHK012 - Are enum value sets specified (risk_tier, console_role, run outcome, mcp status)? [Clarity, data-model]

## Requirement Consistency

- [x] CHK013 - Is the soft-archive rule applied consistently across ALL archivable resources (tools, mcp_servers, skills, knowledge_bases)? [Consistency, Spec §FR-003]
- [x] CHK014 - Are the data-model entities and the contracts/api-surface endpoints consistent (no entity without an endpoint, no endpoint without a backing entity)? [Consistency]

## Edge Case & Coverage

- [x] CHK015 - Are requirements defined for editing/patching an already-published skill (immutable? forces re-version?)? [Gap, Spec §FR-004]
- [ ] CHK016 - Is the MCP nested lifecycle specified (archiving a server → its tools; adding/removing exposed tools)? [Coverage, Gap]
- [x] CHK017 - Are binding-integrity requirements defined for archiving a bound tool/skill/KB (resolves as removable, not corrupting the agent)? [Edge Case, Spec Edge Cases]
- [ ] CHK018 - Is rate-limiting specified for the new write endpoints (reuse the existing seam)? [Gap, adjacent Spec §FR-017]

## Acceptance Criteria Quality

- [x] CHK019 - Are field-level validation constraints (name length, required, slug format, numeric bounds) quantified and verifiable? [Measurability, data-model, Spec §FR-020]
- [x] CHK020 - Is contract fidelity (typed models mirror data.ts shapes) specified as a measurable zero-drift outcome? [Measurability, Spec §FR-005/SC-003]

## Traceability

- [x] CHK021 - Is each endpoint linked to its FR and its contract test via an ID scheme? [Traceability]
