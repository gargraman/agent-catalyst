# Implementation Plan: M0 Data-Model Completion

**Branch**: `feat/backend-m0-foundation` | **Date**: 2026-11-03 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-m0-data-model/spec.md`

## Summary

Add the application data layer over the already-migrated M0 schema: SQLAlchemy 2.0 **async ORM** models for `workspaces / agents / agent_versions`, plus a new migration (`0004`) and ORM for the **snapshot-complete** binding set (`tool_bindings`, `skill_bindings`, `retrieval_bindings`) and a minimal `knowledge_bases` table — each workspace-owned with RLS. Provide a **tenant-scoped `AsyncSession` factory** that connects as the non-superuser `ac_app` role and sets `app.workspace_id` per transaction (fail-closed). Implement `AgentVersion.to_snapshot()` returning a **pure, frozen `VersionSnapshot`** (defined in `runtime/`, no DB imports) whose content hash reuses `services/runtime/hashing.py:compute_snapshot_hash`. This completes M0's data model and unblocks M2's `runtime.invoke`.

## Technical Context

**Language/Version**: Python 3.12 (`services/` requires-python >=3.12)

**Primary Dependencies**: SQLAlchemy 2.0 (async, `asyncpg`), Alembic (sync `psycopg` for migrations only), Pydantic v2. All already in `services/.venv`. No new runtime deps.

**Storage**: PostgreSQL 16 + pgvector (`deploy/compose.yaml`, host port 5433). RLS enforced via `app.workspace_id` GUC; app role `ac_app` (no BYPASSRLS).

**Testing**: pytest + pytest-asyncio (`asyncio_mode=auto`). Unit (infra-free) + integration (`@pytest.mark.integration`, real DB, skips when unreachable) — matching the existing 117-test suite.

**Target Platform**: Linux server (containerized); dev on macOS via compose.

**Project Type**: Backend service monorepo (`services/` = api · runtime · evals · ingestion). This feature is the shared persistence layer.

**Performance Goals**: Not a hot path; snapshot build is O(bindings). The GUC set adds one round-trip per transaction — acceptable (the design already assumes PgBouncer txn-mode).

**Constraints**: `runtime/` MUST stay pure (no DB/HTTP imports) — so `VersionSnapshot` lives in `runtime/`, the ORM lives outside it. Fail-closed tenancy. Frozen-version immutability (0002 trigger) must be honored.

**Scale/Scope**: 4 existing + 4 new tables; one session factory; one `to_snapshot()`. Registry CRUD tables (tools/skills/mcp) explicitly deferred (see spec Clarifications).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Relevance | Status |
|---|---|---|
| **P5 Tenant boundaries** | Session factory sets GUC as `ac_app`; RLS applies; negative cross-tenant + unset-scope tests required (FR-002/003/004/005, SC-001/002) | PASS — core of this feature |
| **P6 Reproducible versions** | `to_snapshot()` → immutable `VersionSnapshot`, clone-stable hash, `kb_index_version` excluded, reflects frozen columns not live agent (FR-006..009) | PASS |
| **P3 Deterministic controls** | Fail-closed scoping enforced in code + DB (NULLIF GUC → zero rows), not convention | PASS |
| **P7 Provider honesty** | N/A (no provider calls in this feature) | N/A |
| **P9 Test-first** | Red→Green; negative tests against real DB before impl; RED shown to owner before implementation code | PASS (process gate) |
| **P2 Phase governance** | Gate-independent M0 foundation, not M1+ expansion; scope bounded by owner clarification | PASS |

No violations → Complexity Tracking empty.

## Project Structure

### Documentation (this feature)

```text
specs/001-m0-data-model/
├── plan.md              # This file
├── research.md          # Phase 0 — decisions (GUC mechanism, module home, reserved-name collision)
├── data-model.md        # Phase 1 — entities, columns, relationships, VersionSnapshot shape
├── contracts/           # Phase 1 — internal interface contracts (session factory, to_snapshot, VersionSnapshot)
│   └── internal-interfaces.md
├── quickstart.md        # Phase 1 — how to run/verify the feature end-to-end
└── tasks.md             # Phase 2 — /speckit-tasks (not created here)
```

### Source Code (repository root)

```text
services/
├── runtime/
│   ├── hashing.py            # REUSE — compute_snapshot_hash (unchanged)
│   └── snapshot.py           # NEW — pure, frozen VersionSnapshot type (NO db/http imports); runtime's input type
├── api/
│   ├── db.py                 # UPDATE — add ac_app async engine + tenant-scoped AsyncSession factory (reuses existing URLs)
│   ├── tenancy.py            # NEW — framework-agnostic workspace-scope helper: set app.workspace_id per txn (09 §7)
│   └── models.py             # NEW — SQLAlchemy ORM + Alembic target (01): Workspace, Agent, AgentVersion(+to_snapshot), ToolBinding, SkillBinding, RetrievalBinding, KnowledgeBase
├── migrations/versions/
│   └── 0004_bindings_and_kb.py   # NEW — tool/skill/retrieval bindings + minimal knowledge_bases + RLS (DDL per 01-data-model.md)
└── tests/
    ├── conftest.py           # UPDATE — add real-ORM fixtures alongside the existing fakes (keep fakes until callers migrate)
    ├── test_orm_models.py        # NEW — typed CRUD + relationships (US3)
    ├── test_session_tenancy.py   # NEW — cross-tenant negative + unset-scope fail-closed, as ac_app (US1)
    └── test_to_snapshot.py       # NEW — snapshot fields, clone-stable hash, kb_index_version excluded, frozen-not-live (US2)
```

**Structure Decision**: Follow the canonical layout in `docs/backend/00-overview.md §3` (authoritative per the constitution's source hierarchy): the ORM + Alembic target lives in **`services/api/models.py`**, the workspace-scope/RLS session var helper in **`services/api/tenancy.py`**, and the engine/session factory in **`services/api/db.py`**. The eval/ingestion workers import these from `api` (a Python import, not an HTTP coupling — the only hard rule is that `runtime/` imports nothing from `api`). `VersionSnapshot` lives in **`runtime/snapshot.py`** so the runtime (its consumer) stays pure; `AgentVersion.to_snapshot()` in `api/models.py` returns that type (`api` → `runtime` is allowed). The FastAPI `deps.py`/`auth/`/`routers/`/`schemas/` from §3 are deferred to the API-surface feature. The DDL for the new tables is taken verbatim-in-intent from `docs/backend/01-data-model.md` (which already specifies the full schema and `to_snapshot` at line 913).

## Complexity Tracking

> No constitution violations — section intentionally empty.
