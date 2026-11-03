---
description: "Task list — M0 Data-Model Completion"
---

# Tasks: M0 Data-Model Completion (ORM, tenant-scoped sessions, version snapshot)

**Input**: Design documents from `specs/001-m0-data-model/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md
**Tests**: INCLUDED — the constitution (P9) mandates Red→Green→Refactor; every behavior-changing task has a failing test first, shown to the owner before implementation code.
**Branch**: `feat/backend-m0-foundation` (no new branch)

## Format: `[ID] [P?] [Story] Description`
- **[P]**: parallelizable (different files, no incomplete deps)
- Paths are repo-relative under `services/`.

---

## Phase 1: Setup (Shared Infrastructure)

- [X] T001 Confirm prereqs green: `docker compose -f deploy/compose.yaml up -d`; `cd services && source .venv/bin/activate`; `alembic current` = `0003`; `pytest -q && pytest -q -m integration` = 117 passed (baseline before changes).
- [X] T002 [P] Create empty module scaffolds (no behavior): `services/runtime/snapshot.py`, `services/api/models.py`, `services/api/tenancy.py` (scaffolding permitted before RED per constitution §9).

---

## Phase 2: Foundational (Blocking Prerequisites — MUST complete before user stories)

**⚠️ Blocks all stories: the new tables and the app-role engine.**

- [X] T003 [RED] Write `services/tests/test_migration_0004.py` (integration): asserts `knowledge_bases`, `retrieval_bindings`, `skill_bindings`, `tool_bindings` exist, carry `workspace_id`, and have RLS **enabled + forced**; asserts enums `tool_kind` and `tool_risk_tier` exist. Run; confirm it FAILS (tables absent). Show failure to owner.
- [X] T004 Write migration `services/migrations/versions/0004_bindings_and_kb.py` (down_revision `0003`): create enums `tool_kind AS ENUM ('http','builtin','subagent','mcp')` and `tool_risk_tier AS ENUM ('read','write','destructive')` (verbatim from `01-data-model.md:90-91`; do NOT re-create the existing `agent_status` enum from 0001); `knowledge_bases` (full per doc: `id, workspace_id→workspaces, slug, name, embedding_model, embedding_dim, chunk_config jsonb, index_version int default 1, access_policy jsonb, created_at, unique(workspace_id,slug)` — NO vector column); `retrieval_bindings` (`version_id→agent_versions, kb_id→knowledge_bases, kb_index_version int, retrieval_config jsonb, injection text default 'tool', unique(version_id,kb_id)`); `skill_bindings` (`version_id→agent_versions, skill_id uuid (no FK — registry deferred), skill_content_hash text, unique(version_id,skill_id)`); `tool_bindings` (`version_id→agent_versions, kind tool_kind, tool_id/mcp_tool_id/subagent_config_id uuid (no FK — registries deferred), schema_snapshot jsonb, config_snapshot jsonb default '{}', risk_tier tool_risk_tier default 'read', requires_approval bool default false, auth_mode text, mcp_scopes text[]`). Add ENABLE+FORCE RLS + `*_ws_isolation` policies (shape per `0002`: `workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid`) to all four tables. Implement `downgrade()` (drop policies, tables, enums). Run T003 → GREEN. Verify `alembic upgrade head` then `downgrade -1` then `upgrade head` all clean.
- [X] T005 [P] Implement ORM base in `services/api/models.py`: `DeclarativeBase` (+ naming conventions), a `WorkspaceScoped` mixin providing `workspace_id`, and the `ac_app` async engine wiring referenced by db.py (type-only; no model bodies yet).
- [X] T006 [P] Add the `ac_app` async engine + `tenant_session`/`set_workspace_scope` **signatures** (not yet implemented) to `services/api/db.py` and `services/api/tenancy.py`, plus an `ac_app` async URL (default `postgresql+asyncpg://ac_app:ac_app_pw@localhost:5433/agent_catalyst`, env-overridable). Behavior lands in US1.

**Checkpoint**: tables + enums + RLS exist; base ORM + engine wired.

---

## Phase 3: User Story 1 — Tenant isolation at the data layer (Priority: P1) 🎯 MVP

**Goal**: Every session is bound to one workspace as `ac_app`; the DB refuses other tenants' rows; unset scope → zero rows (fail-closed).
**Independent Test**: insert under WS A; a session scoped to WS B cannot see/update/delete it; unset-scope session sees 0 rows.

- [X] T007 [RED] [US1] Write `services/tests/test_session_tenancy.py` (integration, connects as `ac_app`): (a) cross-tenant read returns nothing; (b) cross-tenant UPDATE/DELETE affect 0 rows; (c) unset/empty/invalid scope → 0 rows on `agents` and `agent_versions`; (d) assert the connection role is `ac_app` (not BYPASSRLS). Run; confirm FAILS. Show failure to owner.
- [X] T008 [US1] Implement ORM models `Workspace`, `Agent`, `AgentVersion` (core, mapping the **current 0001 columns**; `model_config` → attr `model_config_` per data-model.md; relationships placeholder) in `services/api/models.py`.
- [X] T009 [US1] Implement `set_workspace_scope()` in `services/api/tenancy.py` (`select set_config('app.workspace_id', :ws, true)`) and `tenant_session(workspace_id)` async context manager in `services/api/db.py` (ac_app engine, sets scope at txn start). Run T007 → GREEN.
- [X] T010 [US1] Refactor: keep T007 green; confirm no scope leak across pooled checkouts (add/verify a reuse case).

**Checkpoint**: US1 independently passes — tenant isolation proven as `ac_app`.

---

## Phase 4: User Story 2 — Frozen, reproducible version snapshot (Priority: P1)

**Goal**: `AgentVersion.to_snapshot()` → immutable `VersionSnapshot` with clone-stable hash, `kb_index_version` excluded, decoupled from the live agent row.
**Independent Test**: snapshot carries all behavior fields; hash == `compute_snapshot_hash` == stored; clone → identical hash; kb_index_version change → hash unchanged.

- [X] T011 [RED] [US2] Write `services/tests/test_to_snapshot.py` (integration + unit): asserts snapshot field coverage (data-model.md), `snapshot.snapshot_hash == compute_snapshot_hash(av) == stored`, clone-stability (new ids/same content → equal hash), `kb_index_version` mutation → unchanged hash, frozen-not-live, and parity against an equivalent faked `av` from `conftest.py`. Run; confirm FAILS. Show failure to owner.
- [X] T012 [P] [US2] Implement the pure `VersionSnapshot` frozen dataclass in `services/runtime/snapshot.py` (fields per data-model.md; tuples not lists; NO db/orm imports).
- [X] T013 [P] [US2] Implement ORM models `KnowledgeBase`, `RetrievalBinding`, `SkillBinding`, `ToolBinding` in `services/api/models.py` (columns per data-model.md; `ToolBinding` relationship `order_by (kind, tool_id, mcp_tool_id, subagent_config_id)`; `RetrievalBinding.kb` relationship).
- [X] T014 [US2] Wire `AgentVersion` binding relationships (`retrieval_bindings`, `tool_bindings` ordered, `skill_bindings`) and implement `AgentVersion.to_snapshot()` returning `runtime.snapshot.VersionSnapshot`, reusing `runtime/hashing.py:compute_snapshot_hash`. Run T011 → GREEN.
- [X] T015 [US2] Refactor: ensure bindings are eager-loaded for snapshot assembly (no lazy-load I/O escapes); keep T011 green.

**Checkpoint**: US2 independently passes — snapshot real and reproducible.

---

## Phase 5: User Story 3 — Typed domain model (Priority: P2)

**Goal**: Read/write agents, versions, bindings through typed objects with navigable relationships; frozen-column mutation surfaces loudly.
**Independent Test**: create ws→agent→version→one of each binding via ORM and read back with relationships; a frozen-column update raises.

- [X] T016 [RED] [US3] Write `services/tests/test_orm_models.py` (integration): typed create/read of `Workspace→Agent→AgentVersion→{Retrieval,Skill,Tool}Binding` + `KnowledgeBase`, relationship navigation, and a frozen-column UPDATE that MUST raise (0002 trigger surfaced by ORM, not silently succeed). Run; confirm FAILS. Show failure to owner.
- [X] T017 [US3] Add any missing relationship/back-population and ensure the ORM surfaces the immutability error as an exception. Run T016 → GREEN.
- [ ] T018 [US3] (DEFERRED — non-blocking; tests use local helpers) Refactor `services/tests/conftest.py`: add real-ORM fixtures (ws/agent/version/binding builders) alongside the existing fakes; keep fakes until callers migrate. All prior tests stay green.

**Checkpoint**: all three stories independently functional.

---

## Phase 6: Polish & Cross-Cutting

- [X] T019 [P] Import-purity check: assert `services/runtime/*` imports nothing from `api`/`sqlalchemy`/`fastapi` (add a tiny guard test).
- [X] T020 [P] Run full `quickstart.md` validation end-to-end (migrations up/down, both test runs, role check).
- [X] T021 Docs reconciliation (per "update docs after feature"): record the `agent_versions` schema drift (research.md D5) and the deferred binding-immutability triggers (D6) in `docs/implementation-plan.md` M0 status + flag the follow-up migration; note `0004` added in `docs/backend/01-data-model.md` status if applicable.
- [X] T022 Completion report: commands run, evidence (test counts), and explicitly list unverified/deferred items (registry FKs, status/created_by/note columns, binding triggers).

---

## Dependencies & Execution Order

- **Setup (P1)** → **Foundational (P2)** blocks everything.
- **US1 (P3)** and **US2 (P4)** both depend only on Foundational; US2's ORM models (T013) are independent of US1's session work (T009), so after Foundational, US1 and US2 can proceed in parallel. **US3 (P5)** depends on US1+US2 models.
- Within a story: RED test → models → service/method → refactor.

### Parallel opportunities
- T005/T006 [P] (foundational, different files).
- T012 (runtime/snapshot.py) ∥ T013 (api/models.py) within US2.
- US1 and US2 phases in parallel once Foundational is done (different primary files: db.py/tenancy.py vs snapshot.py + binding models).

---

## Implementation Strategy
- **MVP = US1 (tenant isolation)** — the single most important property (P5). Deliver + validate first.
- Then US2 (snapshot) unblocks M2. Then US3 (typed CRUD + conftest migration).
- **Owner gate:** per constitution §9, pause after each RED task (T003, T007, T011, T016) to show the failing test + evidence and get confirmation before writing the implementing task.

## Notes
- No new runtime deps (SQLAlchemy/asyncpg/alembic already in `.venv`); no pgvector needed (no vector column this feature).
- Keep prior 117 tests green throughout.
- `0004` FK omissions (registry tables) and `agent_versions` column drift are intentional, scoped-out, and flagged — not defects.
