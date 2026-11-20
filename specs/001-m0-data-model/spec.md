# Feature Specification: M0 Data-Model Completion (ORM, tenant-scoped sessions, version snapshot)

**Feature Branch**: `feat/backend-m0-foundation` (existing; no new branch)

**Created**: 2026-11-03

**Status**: Draft

**Input**: User description: "M0 data-model completion: SQLAlchemy 2.0 async ORM for the existing migrated schema (workspaces, agents, agent_versions) plus the version binding tables (tool_bindings, retrieval_bindings, skill_bindings) that conftest.py already fakes. AsyncSession factory that sets app.workspace_id GUC as ac_app so RLS applies (fail-closed). AgentVersion.to_snapshot() → frozen VersionSnapshot, reusing hashing.py. Gate-independent M0 foundation. Honor constitution P5 (tenancy/RLS), P6 (immutable snapshots), TDD."

## Context (why this feature)

The M0 migrations (`0001–0003`) created the `workspaces / agents / agent_versions` tables, the row-level-security (RLS) policies, the immutability trigger, and the non-superuser `ac_app` role. But the application has **no typed way to read or write that schema**: `services/api/db.py` holds only connection URLs, there are no ORM models, there is no session factory that activates RLS, and `AgentVersion.to_snapshot()` — which the runtime depends on — does not exist (the test `conftest.py` only *fakes* it). This feature closes that gap so later milestones (the runtime spine, the API) have a real, tenant-safe data layer to build on. It is gate-independent foundation, not production expansion of the defensible cores.

## User Scenarios & Testing *(mandatory)*

The "users" of this foundation are the platform's own internal consumers: the request/job layer that must stay tenant-isolated, the runtime that executes frozen versions, and — ultimately — the builders and tenants whose data separation depends on it being correct.

### User Story 1 - Tenant isolation is enforced at the data layer (Priority: P1)

Any code path that touches tenant-owned data does so through a session bound to exactly one workspace, and the database itself refuses to return or mutate another workspace's rows — even if application code has a bug.

**Why this priority**: Constitution P5 makes workspace separation a security boundary from the first release. If this is wrong, every later feature leaks data. It is the single most important property of M0.

**Independent Test**: Open a session scoped to workspace A, insert an agent; open a session scoped to workspace B and attempt to read/update/delete A's agent — it must be invisible and unmodifiable. A session with no workspace scope must see zero rows (fail-closed).

**Acceptance Scenarios**:

1. **Given** an agent owned by workspace A, **When** a session scoped to workspace B queries agents, **Then** workspace A's agent is not returned.
2. **Given** an agent owned by workspace A, **When** a session scoped to workspace B attempts to update or delete it, **Then** no row is affected.
3. **Given** a session where the workspace scope was never set, **When** it queries any tenant-owned table, **Then** it returns zero rows (not an error, not all rows).
4. **Given** a session, **When** it connects, **Then** it connects as a role that does not bypass RLS.

### User Story 2 - The runtime can load a frozen, reproducible version snapshot (Priority: P1)

Given a published agent version, the runtime can obtain an immutable snapshot of everything that defines that version's behavior, cost, and authority — with a stable content hash — without reading the mutable agent configuration.

**Why this priority**: Constitution P6 and the core architectural invariant require that playground, eval, and production all execute the *same* frozen snapshot. `to_snapshot()` is the seam the runtime consumes; nothing downstream works without it.

**Independent Test**: Load a version, call `to_snapshot()`, and confirm the snapshot carries the behavior-defining fields and a hash that matches the frozen `snapshot_hash`; clone the version (new row id, same content) and confirm the snapshot hash is identical.

**Acceptance Scenarios**:

1. **Given** a stored agent version with its bindings, **When** a snapshot is produced, **Then** it contains the prompt, model/profile, I/O contract, memory, context, guardrail, and tool/retrieval/skill binding references that affect behavior, authority, or cost.
2. **Given** two versions with identical behavior-defining content but different row identifiers, **When** each is snapshotted, **Then** both produce the same snapshot hash (per-row ids do not affect the hash).
3. **Given** a version whose knowledge-base index version changes, **When** it is snapshotted, **Then** the snapshot hash does not change (the refreshing-knowledge input is deliberately excluded).
4. **Given** a snapshot, **When** anything in it is compared to the stored frozen columns, **Then** they are consistent (the snapshot reflects the frozen version, never the live agent row).

### User Story 3 - Domain data is accessed through a typed model (Priority: P2)

Application and test code reads and writes agents, versions, and their bindings through typed objects rather than hand-written SQL strings.

**Why this priority**: Enables the API and runtime milestones to be built safely and testably, and removes the raw-SQL helpers currently in `conftest.py`. Valuable but subordinate to the two safety properties above.

**Independent Test**: Create a workspace, agent, version, and one of each binding kind through the typed model and read them back with their relationships populated.

**Acceptance Scenarios**:

1. **Given** the typed model, **When** an agent with a version and bindings is created and re-read, **Then** the relationships (version → bindings, agent → versions) are navigable.
2. **Given** the immutability trigger from migration 0002, **When** code attempts to change a frozen column on an existing version, **Then** the write is rejected loudly (surfaced as an error, not silently dropped).

### Edge Cases

- A session whose workspace GUC is set to an empty string or an invalid value → treated as unset → zero rows (fail-closed), not a cast error.
- Connection reuse from a pool → one session's workspace scope must never bleed into the next (scope is per-transaction/request).
- `to_snapshot()` on a version missing optional bindings → produces a valid snapshot with empty binding lists, stable hash.
- Attempt to mutate a frozen column → immutability trigger raises; the ORM surfaces it rather than reporting success.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST expose a typed data model for `workspaces`, `agents`, `agent_versions`, and the version binding kinds (tool, retrieval, skill) matching the already-migrated schema (0001–0003).
- **FR-002**: The system MUST provide a session factory that yields one session per request/job and binds that session to a single workspace for its lifetime.
- **FR-003**: Each tenant-scoped session MUST connect as the non-superuser application role (`ac_app`) so the RLS policies are actually enforced (the migration/owner role bypasses RLS and MUST NOT be used on the request path).
- **FR-004**: A session MUST set the workspace scope such that an unset, empty, or invalid scope yields zero rows for every tenant-owned table (fail-closed), not an error and not all rows.
- **FR-005**: The workspace scope MUST be confined to the session's transaction so a pooled connection never carries one request's scope into another.
- **FR-006**: The system MUST produce a frozen version snapshot from a stored agent version, containing every field that affects behavior, authorization, or cost (prompt, model/profile, I/O contract, memory, context, guardrails, tool/retrieval/skill binding references).
- **FR-007**: The snapshot's content hash MUST reuse the existing hashing authority (`services/runtime/hashing.py`) and MUST be identical for two versions that differ only by per-row identifiers (clone-stable).
- **FR-008**: The snapshot MUST exclude the knowledge-base index version from the hash (the refreshing-knowledge input is intentionally decoupled, per design 04 §9).
- **FR-009**: The snapshot MUST reflect the frozen version columns, never the mutable live `agents` configuration.
- **FR-010**: The data layer MUST surface a frozen-column mutation attempt as an explicit error (honoring the 0002 immutability trigger), never report it as succeeded.
- **FR-011**: All existing M0 tests (currently 117) MUST remain green; the raw-SQL helpers in `conftest.py` SHOULD be replaceable by the typed model.
- **FR-012**: The feature MUST include negative cross-tenant tests (read, update, delete across workspaces) and a fail-closed unset-scope test, run against the real database.

### Key Entities *(include if feature involves data)*

- **Workspace**: the tenant root; owns all other tenant data. The security boundary.
- **Agent**: a mutable configuration owned by a workspace; points at its active version.
- **AgentVersion**: an immutable, frozen snapshot of behavior-defining configuration for one version of an agent; source of the runtime snapshot.
- **ToolBinding / RetrievalBinding / SkillBinding**: per-version references to the capabilities (tools, knowledge retrieval, skills) that affect behavior/authority/cost and must be captured in the snapshot.
- **VersionSnapshot**: the frozen, hashable value the runtime executes; derived from an AgentVersion + its bindings.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In 100% of cross-tenant access attempts (read, update, delete), the other tenant's rows are invisible/unaffected — verified by automated negative tests against the real database.
- **SC-002**: A session with no workspace scope returns 0 rows from every tenant-owned table in 100% of cases.
- **SC-003**: Snapshotting two versions that differ only by row identifiers yields byte-identical snapshot hashes in 100% of cases; changing only the knowledge-index version changes the hash in 0% of cases.
- **SC-004**: 100% of the behavior/authority/cost-defining fields enumerated in the design are present in the produced snapshot.
- **SC-005**: The full M0 test suite (prior 117 tests plus the new data-model tests) passes with 0 failures, and the raw-SQL helpers in `conftest.py` are no longer required for the new tests.
- **SC-006**: A frozen-column mutation attempt fails loudly in 100% of cases (never silently succeeds).

## Assumptions

- The schema already exists via migrations `0001–0003`; this feature adds the application model over it and does not change the DDL (any schema gap discovered becomes a new migration, specified separately).
- The `ac_app` role and the `app.workspace_id` RLS convention from `0002/0003` are the contract to build against; `services/runtime/hashing.py` is the reused hashing authority.
- The local Postgres from `deploy/compose.yaml` (host port 5433) is the integration-test target; tests skip cleanly when it is unavailable, matching the existing conftest pattern.
- Async SQLAlchemy on the request path; the sync driver remains only for Alembic (per `api/db.py`).
- Binding tables (`tool_bindings`, `retrieval_bindings`, `skill_bindings`) are modeled to match the shapes `conftest.py` already fakes and the columns `compute_snapshot_hash` reads.
- Out of scope (separate features): FastAPI app/routers, the settings layer, CI.

## Clarifications

### Session 2026-11-03

- **Q: How should feature 001 scope the binding/KB tables the snapshot reads, given only `workspaces/agents/agent_versions` are migrated?**
  **A: Snapshot-complete minimal.** This feature ADDS migrations + ORM for exactly what `compute_snapshot_hash` reads — `tool_bindings`, `skill_bindings`, `retrieval_bindings`, and a **minimal `knowledge_bases`** table (providing `embedding_model` for retrieval bindings) — each workspace-owned with RLS policies (and, where a column is behavior-frozen, consistent with the immutability model). This makes `to_snapshot()` genuinely real and testable against real rows.
  **Explicitly excluded** (deferred to M4+ registry features): the tool/skill/mcp **registry CRUD** tables (`tools`, `skills`, `mcp_servers`, `mcp_tools`). Bindings carry self-contained snapshot columns (`schema_snapshot`, `config_snapshot`, `skill_content_hash`, etc.) and only *reference* registry rows by id for sort-stability, so the snapshot does not require the registry tables to exist yet. FK constraints to not-yet-existing registry tables are omitted for now (binding `*_id` columns are plain identifiers until the registries land).

## Constitution Alignment

- **P5 (tenant boundaries):** FR-002/003/004/005, SC-001/002 — server-side isolation + RLS defense-in-depth with negative cross-tenant tests.
- **P6 (reproducible/reversible versions):** FR-006/007/008/009, SC-003/004 — immutable, hashable snapshot decoupled from live config and from refreshing knowledge.
- **P3 (deterministic controls):** fail-closed scoping enforced in code + DB, not convention.
- **P9 (test-first):** FR-012/SC-005 — Red→Green with negative tests run against the real DB before implementation code.
