---
description: "Task list for M4-remainder — Resource CRUD + Frontend Contract Sync"
---

# Tasks: M4-remainder — Resource CRUD + Frontend Contract Sync

**Input**: Design documents from `/specs/006-resource-crud-sync/`
**Prerequisites**: plan.md, spec.md, research.md (D1–D12), data-model.md, contracts/api-surface.md, quickstart.md

**Tests**: REQUIRED — the constitution (P9) mandates strict Red→Green→Refactor. Every behavior-changing task has a
RED test written and shown failing before implementation. Frontend has **no unit harness** (`frontend/CLAUDE.md`), so
FE verification is `gen:api` zero-drift + `npm run build`/`lint` + manual render (honest per research D10), not
fabricated FE unit tests.

**Organization**: by user story. **MVP = US1 + US2** (catalog CRUD + frontend proof). US3–US5 are incremental.

## Path Conventions

Backend: `services/api/`, `services/migrations/`, `services/tests/` (cwd `services/`, `pythonpath=["."]`).
Frontend: `frontend/src/`. Focused test command: `.venv/bin/python -m pytest -q <path>`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: tooling only — no tested behavior (allowed before RED, P9).

- [x] T001 [P] Add `openapi-typescript` devDependency and a `gen:api` script (emit `src/lib/api/` from the API's OpenAPI 3.1) to `frontend/package.json`
- [ ] T002 Read `frontend/node_modules/next/dist/docs/` (App Router conventions) and record any deltas before touching FE code — modified-Next.js caveat (`frontend/AGENTS.md`)

---

## Phase 2: Foundational (BLOCKING — no user story starts until this is done)

**⚠️ CRITICAL**: the migration, ORM, and drift helper are prerequisites for every story.

- [x] T003 Write RED test `services/tests/test_migration_0005.py`: asserts tables `tools, mcp_servers, mcp_tools, skills, workspace_settings, members, runs` exist, each with its `*_ws_isolation` RLS policy (enabled+forced), plus `agents.needs_reeval` and `knowledge_bases.archived_at` columns. Must FAIL before T004.
- [x] T004 Implement migration `services/migrations/versions/0005_registries_members_runs.py` — create the 7 tables per data-model.md (reuse enums `tool_kind`/`tool_risk_tier` from 0004; `UNIQUE(workspace_id,name)` tools, `UNIQUE(workspace_id,slug)` skills, `mcp_tools.server_id` FK→mcp_servers, `workspace_settings` PK=workspace_id 1:1, `UNIQUE(workspace_id,subject)` members, `runs` index `(workspace_id, created_at desc)`, `cost_usd numeric(12,6)`); add `agents.needs_reeval boolean NOT NULL DEFAULT false` and `knowledge_bases.archived_at timestamptz NULL` and `archived_at` on tools/mcp_servers/skills; apply `_enable_rls` to all 7. GREEN T003.
- [x] T005 [P] Extend ORM `services/api/models.py`: `Tool`, `McpServer`, `McpTool`, `Skill`, `WorkspaceSettings`, `Member`, `Run` (WorkspaceScoped), `needs_reeval` on `Agent`, `archived_at` where archivable — column types/constraints verbatim from data-model.md.
- [x] T006 [P] Extend `services/tests/_api_helpers.py` with seeders `seed_tool`, `seed_skill`, `seed_mcp_server`, `seed_kb`, `seed_member`, `seed_run` (via `tenant_session`, workspace-scoped).
- [x] T007 Write RED unit test `services/tests/test_api_drift.py`: a bound tool's auth-mode/scope edit flags only the bound agents' `needs_reeval`; a bound MCP-tool edit flags bound agents; a skill content edit flags bound agents; a cosmetic (description) edit flags none; an unbound resource flags none. Must FAIL before T008.
- [x] T008 Implement `services/api/drift.py` `flag_bound_agents(session, kind, resource_id)` — joins `tool_bindings.tool_id` **and `tool_bindings.mcp_tool_id`** / `skill_bindings.skill_id` / `retrieval_bindings.kb_id` → `version_id` → `agent_id`, sets `agents.needs_reeval=true` (an MCP-tool edit flags bound agents just like an HTTP-tool edit). GREEN T007.

**Checkpoint**: schema + ORM + drift ready; migration applied (`alembic upgrade head`); user stories can begin.

---

## Phase 3: User Story 1 — Tool & skill catalog CRUD (Priority: P1) 🎯 MVP

**Goal**: builders create/edit/list/archive HTTP tools, MCP servers+tools, and skills; bound-resource edits flag re-eval.
**Independent Test**: create+edit+archive a tool and a skill in ws A; edit a bound one → bound agents flagged; ws B sees neither.

### Tests (RED first)

- [x] T009 [P] [US1] `services/tests/test_api_tools.py`: tools create/get/list/patch/archive/restore; `ETag`/`If-Match` → `conflict.stale`; `Idempotency-Key` → `idempotency.in_progress`; `?include_archived` (archived hidden by default, shown with filter, still fetchable by id — clarify #3); auth-mode/scope patch → drift flag; **FR-021** reject/ignore any secret-looking field.
- [x] T010 [P] [US1] `services/tests/test_api_skills.py`: skills CRUD + `POST /skills/:id/publish` stamps **unsigned provenance** (no signature) + archive/restore; content edit → drift flag; **editing a published skill's content reverts it to `draft`** (FR-004), a cosmetic edit does not.
- [x] T011 [P] [US1] `services/tests/test_api_mcp.py`: mcp_servers CRUD + nested mcp_tools manage; archive.
- [x] T012 [P] [US1] Extend `services/tests/test_api_tenancy.py` (integration): negative cross-tenant for `tools`, `skills`, `mcp_servers`, `mcp_tools` (ws B gets zero rows / 404).

### Implementation

- [x] T013 [P] [US1] `services/api/schemas/tools.py`: `ToolCreate/Patch/Out`, `McpServerCreate/Patch/Out` (incl. `tools: McpToolOut[]`), `McpToolCreate/Patch/Out` — mirror data.ts `RepoTool`/`McpServer`/`McpTool`; `http_config` non-secret only.
- [x] T014 [P] [US1] `services/api/schemas/skills.py`: `SkillCreate/Patch/Out`, `SkillPublishOut` — mirror data.ts `Skill`/`SkillResource`.
- [x] T015 [US1] `services/api/routers/tools.py`: tools + mcp CRUD + archive/restore (clear `archived_at`); reuse `resolve_workspace`/`tenant_db`/`idempotency_guard`/`rate_limit`/ETag; call `drift.flag_bound_agents` on auth-mode/scope patch (HTTP and MCP tools). GREEN T009/T011.
- [x] T016 [US1] `services/api/routers/skills.py`: CRUD + publish (unsigned provenance) + archive/restore; content edit → `drift.flag_bound_agents` **and revert a published skill to `draft`** (FR-004). GREEN T010.
- [x] T017 [US1] Register tools + skills routers in `services/api/main.py`; confirm they appear in OpenAPI.
- [x] T018 [US1] Run US1 suites + `test_api_drift.py` + tenancy; GREEN all; refactor; keep M4a regressions green.

**Checkpoint**: tool/skill/MCP catalog fully functional, tenant-isolated, drift-aware.

---

## Phase 4: User Story 2 — Frontend sync + remaining-implementation tracker (Priority: P1)

**Goal**: real typed client, representative hooks swapped mock→real, living mock-vs-real tracker.
**Independent Test**: `gen:api` zero drift; a representative page renders real data; tracker reconciles with `@backend` grep.
**Depends on**: M4a endpoints (agents/invoke/eval/publish) + US1 endpoints (tools/skills) being registered.

- [x] T019 [US2] Extend `services/tests/test_api_contracts.py`: assert the OpenAPI schema includes all 006 routes (tools/skills/mcp/knowledge/workspace/members/runs/metrics) with typed request/response + error envelope.
- [ ] T020 [US2] Run `npm run gen:api` → generate+commit `frontend/src/lib/api/`; the client type-checks; regeneration yields **zero drift**.
- [ ] T021 [P] [US2] Swap agents hooks in `frontend/src/lib/store.ts` (`useAgents`/`addAgent`/`updateAgent`) mock→real via the generated client, preserving hook shapes + loading/empty/error states.
- [ ] T022 [P] [US2] Swap tools (`useTools`/`addTool`/`updateTool`) + skills hooks mock→real in `frontend/src/lib/store.ts` (requires US1 endpoints).
- [ ] T023 [US2] Swap playground `send()` (invoke SSE), eval-run, and publish to real in `frontend/src/lib/store.ts` (+ `frontend/src/components/eval-explainer.tsx` where publish/eval UI lives) — M4a surfaces.
- [x] T024 [US2] Produce `docs/backend-integration-status.md`: enumerate every `@backend` seam (`grep -rn "@backend" frontend/src`), classify REAL/MOCK grouped by resource, totals reconcile with the grep count; no mock labeled implemented (**SC-004**).
- [ ] T025 [US2] FE verification: `npm run lint && npm run build` (zero type errors, zero client drift); manually load Agents + Tools pages against the running API and confirm real data (**SC-003**).

**Checkpoint**: representative console runs on the real backend; remaining work is tracked.

---

## Phase 5: User Story 3 — Knowledge-base metadata CRUD (Priority: P2)

**Goal**: KB metadata lifecycle (ingestion honestly deferred).
**Independent Test**: create/edit/archive a KB in ws A; content edit flags bound agents; ws B isolated; ingestion state honest.

- [x] T026 [P] [US3] `services/tests/test_api_knowledge.py`: KB create/get/list/patch/archive/restore; content/retrieval-config edit → drift flag; `GET /ingestion-jobs` returns honest-deferred (no fabricated docs/chunks).
- [x] T027 [P] [US3] Extend `test_api_tenancy.py`: negative cross-tenant for `knowledge_bases`.
- [x] T028 [P] [US3] `services/api/schemas/knowledge.py`: `KnowledgeBaseCreate/Patch/Out`, `IngestionJobOut` — mirror data.ts; no live ingestion counts.
- [x] T029 [US3] `services/api/routers/knowledge.py`: CRUD + archive/restore over the existing `knowledge_bases` table (+ `archived_at`); content/retrieval edit → `drift.flag_bound_agents`; `GET /ingestion-jobs` honest-deferred. Register in main.py. GREEN T026.

**Checkpoint**: KB metadata managed; ingestion honestly marked deferred.

---

## Phase 6: User Story 4 — Workspace & member administration (Priority: P2)

**Goal**: settings/limits/eval-policy + member roster within the active workspace (roles stored, not enforced).
**Independent Test**: update ws A settings+policy; add/remove member + set role; ws B unaffected.

- [x] T030 [P] [US4] `services/tests/test_api_workspaces.py`: `GET/PATCH /workspace/settings` (limits + eval-policy) with `If-Match`; `PATCH /workspace` rename.
- [x] T031 [P] [US4] `services/tests/test_api_members.py`: roster list/add/remove/set-role; role is **stored, not enforced** (assert no RBAC gating applied here).
- [x] T032 [P] [US4] Extend `test_api_tenancy.py`: negative cross-tenant for `workspace_settings` and `members`.
- [x] T033 [P] [US4] `services/api/schemas/workspaces.py` + `services/api/schemas/members.py`: mirror data.ts `WorkspaceSettings`/`Limits`/`EvalPolicy`/`Member`.
- [x] T034 [US4] `services/api/routers/workspaces.py` (settings get/patch + rename) and `services/api/routers/members.py` (roster CRUD); register in main.py. GREEN T030/T031.

**Checkpoint**: per-tenant administration operable; role enforcement explicitly deferred.

---

## Phase 7: User Story 5 — Run history + metrics (Priority: P3)

**Goal**: durable run records feed history + dashboard metrics.
**Independent Test**: a (mocked) invoke persists a run (incl. failure); history lists newest-first; metrics reconcile.

- [x] T035 [P] [US5] `services/tests/test_api_runs.py`: a mocked invoke persists a `runs` row (success AND forced-failure path) with `source='playground'/'published'`; an eval-run persists `source='eval'`; **assert identity+metrics only — no raw prompt/response content stored** (FR-013/SC-009); `GET /runs` newest-first paginated; `GET /runs/:id`.
- [x] T036 [P] [US5] `services/tests/test_api_metrics.py`: `GET /metrics/dashboard|runs|evals` over a **rolling 30-day window, daily buckets**; `call volume` counts playground+published (eval excluded); `eval trend` over eval runs; **empty window → zero-filled buckets, not error** (FR-015/SC-009); workspace-scoped reconcile with seeded runs.
- [x] T037 [P] [US5] Extend `test_api_tenancy.py`: negative cross-tenant for `runs`.
- [x] T038 [P] [US5] `services/api/schemas/runs.py` + `services/api/schemas/metrics.py`: mirror data.ts `Run`/`RunsStats`/`EvalStats`/`DashStats`/`Series`.
- [x] T039 [US5] Extend `services/api/routers/invoke.py` **and `services/api/routers/evals.py`**: wire `RunContext.record_run` to INSERT one `runs` row per invocation (success + failure) within the tenant session — `source` from the RunContext (`playground`/`published`/`eval`), cost from the runtime `RunRecord` (P7), **identity+metrics only, no raw content** (FR-013). GREEN the persist test.
- [x] T040 [US5] `services/api/routers/runs.py` (list/get) + `services/api/routers/metrics.py` (server-side aggregation over `runs`); register in main.py. GREEN T035/T036.

**Checkpoint**: invocations durably recorded; history + metrics real.

---

## Phase 8: Polish & Cross-Cutting

- [x] T041 Data-handling test in `services/tests/test_api_contracts.py`: assert no endpoint persists/returns a secret value (**FR-021/SC-008**); assert `runs` store **no raw prompt/response content** (**SC-009**); assert removing a member **hard-deletes** the roster row and credentials/member-PII never appear in error bodies/tracker (**P5 Data Handling**); secret-scan stays green.
- [x] T042 [P] Update `docs/backend/08-api-surface.md` + `docs/backend/01-data-model.md` to the built state (006 banner, new tables/endpoints).
- [x] T043 [P] Update `docs/implementation-plan.md` M4-remainder row to built state (what shipped vs deferred to M5/M7).
- [x] T044 Run the full suite `.venv/bin/python -m pytest -q` + `-m integration` and `quickstart.md`; confirm **runtime/evals purity tests stay green** (FR-019) and M4a regressions pass; record evidence.

---

## Dependencies & Execution Order

- **Setup (P1: T001–T002)**: no deps.
- **Foundational (P2: T003–T008)**: T003→T004→T005→{T006,T007}; T007→T008. **Blocks all stories.**
- **US1 (T009–T018)**: after Foundational. Tests T009–T012 [P] → impl T013–T017 → T018 green.
- **US2 (T019–T025)**: after US1 (for T022) **and** M4a surfaces; T021 (agents) depends only on M4a; T019/T020 need routers registered. Tracker (T024) can start anytime.
- **US3 (T026–T029)**: after Foundational (uses `drift`). Independent of US1/US2/US4/US5.
- **US4 (T030–T034)**: after Foundational. Independent.
- **US5 (T035–T040)**: after Foundational; T039 extends the M4a invoke route. Independent of US1–US4.
- **Polish (T041–T044)**: after all desired stories.

### Parallel opportunities

- Setup T001 ∥ T002.
- Foundational T005 ∥ T006 (different files) after T004.
- Within each story, all test files [P] and all schema files [P] run in parallel; routers that touch `main.py`
  registration are sequential at the register step.
- Across stories: once Foundational is done, US1/US3/US4/US5 can be staffed in parallel (US2 trails US1).

### MVP / incremental delivery

1. Setup + Foundational → migration applied.
2. **US1 → US2 = MVP** (real catalog + real representative console + tracker). Stop & validate.
3. Add US3, US4, US5 incrementally, each independently testable.
4. Polish: docs reconciled, secret-absence + full regression, quickstart.

---

## Notes

- Every behavior task is preceded by a RED test; show failing evidence to the owner before implementation (P9).
- `[P]` = different files, no incomplete deps. Avoid same-file conflicts (router registration in `main.py` is serial).
- No live/paid provider calls anywhere (D10); invoke is mocked.
- Per standing instruction, reconcile design docs to built state at DoD (T042/T043).
