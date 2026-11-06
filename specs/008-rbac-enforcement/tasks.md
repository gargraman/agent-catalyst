# Tasks: RBAC Enforcement Slice (M5)

**Feature**: `specs/008-rbac-enforcement` | **Branch**: `feat/backend-m0-foundation` | **Date**: 2026-11-06

**Inputs**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md) (D1–D7), [data-model.md](./data-model.md) (47-route table), [contracts/rbac.md](./contracts/rbac.md), [quickstart.md](./quickstart.md)

**TDD is mandatory** (P9): each story's behavioral RED (deny expected, currently allowed) is written and shown to fail before enforcement is wired. Tests use `httpx` ASGITransport + `dev_headers(claims={ws: role})`; no live/paid calls. Change is API-layer only — `runtime/`+`evals/` untouched (P6). No new deps, no migration.

**Conventions**: `cd services && .venv/bin/python -m pytest`. `[P]` = parallelizable (different files). Story labels on story-phase tasks only.

---

## Phase 1: Setup

- [x] T001 Establish the green baseline + enumerate the authz surface: run `cd services && .venv/bin/python -m pytest -q` (record count), and confirm the 47 workspace-scoped routes and their intended permission from `data-model.md` (grep `@router\.(get|post|patch|delete)` across `services/api/routers/`). Identify existing tests that exercise publish / members / workspace-settings under the default `builder` claim (candidates for T017). No code change.

## Phase 2: Foundational (blocking prerequisites for all stories)

- [x] T002 [P] RED: write `services/tests/test_rbac_model.py` asserting (a) `api.permissions.ROLE_PERMISSIONS` has exactly the four roles with the permission sets from data-model.md (viewer=2; builder adds 9; publisher adds agent.publish+tool.approve; admin adds 6); (b) `has_permission(role, perm)` is fail-closed — unknown/empty role → False for every permission; (c) a parity check: parsing `frontend/src/lib/permissions.ts` fresh yields the same map as the committed `rbac_permissions.json`. Must fail (module/data absent).
- [x] T003 Write the generator `services/scripts/gen_rbac.py`: parse `frontend/src/lib/permissions.ts` (strip `//` comments; capture `const BUILDER|PUBLISHER|ADMIN: Permission[] = [...]` and the `ROLE_PERMISSIONS = {...}` object; resolve `...SPREAD` refs recursively; preserve order, dedupe; error on unknown ref/role) and write `services/api/rbac_permissions.json` (`{role: [perms]}`). Use a repo-relative path resolution so it runs from `services/`.
- [x] T004 Run `gen_rbac.py` to produce `services/api/rbac_permissions.json` (committed artifact).
- [x] T005 Create `services/api/permissions.py`: `Role`/`Permission` names, load `rbac_permissions.json` into `ROLE_PERMISSIONS: dict[str, frozenset[str]]`, and `has_permission(role, permission) -> bool` (fail-closed via `.get(role, frozenset())`). Make T002 pass (GREEN).
- [x] T006 Add `require_permission(permission: str)` to `services/api/deps.py`: a dependency factory returning a callable that depends on `session_principal` + `resolve_workspace`, reads `role = principal.workspace_claims[ws]`, and raises `AccessDenied("insufficient role", detail={"required_permission": permission, "role": role})` unless `has_permission(role, permission)`. Attach `required_permission = permission` to the returned callable for coverage introspection (D2). (No DB access in this dep.)

## Phase 3: User Story 1 (P1) — Privileged operations gated by role

**Goal**: every mutating/privileged route requires its permission; lower roles get 403 before any side effect.
**Independent test**: per-role matrix over representative write ops.
**Depends on**: Foundational.

- [x] T007 [US1] RED: write `services/tests/test_api_rbac_matrix.py` (ASGI client, compose Postgres) covering the deny matrix: viewer→POST agent = 403; builder→POST `…/publish` = 403; builder→POST `/members` = 403; builder→PATCH `/workspace/settings` = 403; publisher→POST `/members` = 403; and the allow cases: builder→POST agent = 201, publisher→publish = 200, admin→POST `/members` = 201. Assert denied calls create/change no row (query after). **Also assert (A1, FR-009) a denied response envelope is `{"error":{"code":"access.denied","detail":{"required_permission": …}}}` (not just the 403 status), and (A2, FR-015) that an under-privileged caller targeting a NON-existent resource id (e.g. viewer PATCH `/agents/{random-uuid}`) gets `403`, not `404` — proving the role check precedes resource load and leaks no existence.** Must fail (membership-only currently allows all).
- [x] T008 [US1] Annotate the **agent-lifecycle** routes in `services/api/routers/agents.py` + `evals.py`: `POST /agents`→`agent.create`; `PATCH /agents/{id}` & `POST /agents/{id}/versions`→`agent.edit`; `POST /agents/{id}/eval-runs`→`eval.run`; `POST /agents/{id}/publish`→`agent.publish` — via `dependencies=[Depends(require_permission("…"))]`.
- [x] T009 [P] [US1] Annotate **tools/mcp** routes in `services/api/routers/tools.py`: tools create/patch/archive/restore→`tool.manage`; mcp-servers create/patch/archive + mcp-servers/{id}/tools + mcp-tools/{id} patch→`mcp.manage` (GETs handled in US2/T013).
- [x] T010 [P] [US1] Annotate **skills** (`skills.py`: create/patch/publish/archive/restore→`skill.manage`) and **knowledge** (`knowledge.py`: knowledge-bases create/patch/archive/restore→`knowledge.manage`).
- [x] T011 [US1] Annotate **admin-plane** routes: `members.py` (POST/PATCH/DELETE→`member.manage`) and `workspaces.py` (`PATCH /workspace/settings` + `PATCH /workspace`→`settings.manage`). Make T007 pass (GREEN).

## Phase 4: User Story 2 (P1) — Least-privilege reads; unknown roles fully denied

**Goal**: readers/playground still work; unknown role denied everything (fail-closed).
**Depends on**: Foundational (US1 may be in progress; US2 annotates the remaining read/invoke routes).

- [x] T012 [US2] RED: extend `services/tests/test_api_rbac_matrix.py` with: viewer→GET/list agents/runs/metrics = 200; viewer→POST `…/invoke` authorized (`playground.use`); a caller with role `"superadmin"` (not in the model) → 403 on BOTH a read and a write (fail-closed, SC-003); valid role in an unheld workspace → 403 (membership composes). **Also assert (A3) that a denied invoke returns a plain `403` envelope BEFORE any SSE stream begins (a JSON `access.denied`, not a `text/event-stream` body / mid-stream error frame).** The unknown-role and read assertions must fail until reads/invoke are annotated + model is fail-closed.
- [x] T013 [US2] Annotate all **read** routes with `view` and the **invoke** route with `playground.use`: GETs across `agents.py`, `tools.py`, `skills.py`, `knowledge.py` (incl. `GET /ingestion-jobs`), `runs.py` (runs + metrics), `members.py` (`GET /members`), `workspaces.py` (`GET /workspace/settings`); `POST /agents/{id}/invoke`→`playground.use` in `invoke.py`. Make T012 pass (GREEN).

## Phase 5: User Story 3 (P2) — No FE/BE drift; deny-by-default by construction

**Goal**: server map stays equal to permissions.ts; every workspace route is annotated.
**Depends on**: US1+US2 (all routes annotated).

- [x] T014 [US3] RED→GREEN: write `services/tests/test_api_rbac_coverage.py` that walks `api.main.app.routes`, and for every route whose path starts with `/api/v1` asserts at least one dependency callable carries the `required_permission` attribute (deny-by-default, FR-014/SC-007). It fails now only if a route was missed in US1/US2 — use it to catch any gap, then confirm green.
- [x] T015 [US3] Confirm the parity/drift test (T002c) is green against the committed `rbac_permissions.json`; document the regeneration step (`python scripts/gen_rbac.py`) in `services/README`/quickstart and note it as a CI check (OpenAPI-style drift gate) in `docs/backend/09`.

## Phase 6: Polish & Cross-Cutting

- [x] T016 Audit ALL test files for higher-tier operations run under a lower-role claim: update `services/tests/test_api_evals_publish.py` (publish → `publisher` claim), `test_api_members.py` (→ `admin`), `test_api_workspaces.py` (settings/rename → `admin`), and any others found in T001 (e.g. `test_api_contracts.py`, `test_api_invoke_trust_boundary.py` if they publish/manage) to role-appropriate claims (FR-013). Builder-tier ops (create/edit/eval/tool/skill/kb/invoke/reads) keep the default builder claim.
- [x] T017 Run purity + full regression: `.venv/bin/python -m pytest tests/test_runtime_purity.py -q` (P6 green) then `.venv/bin/python -m pytest -q` (no regressions vs the T001 baseline + new RBAC tests); record the pass count.
- [x] T018 Reconcile docs: add an M5 RBAC-enforcement built-state banner to `docs/implementation-plan.md` and `docs/backend/09` (role→permission enforcement live; deny-by-default; access-policy/OIDC/Vault still deferred); update `docs/backend-integration-status.md` if it references authz.

---

## Dependencies

```
Setup (T001)
  └─► Foundational (T002 RED → T003 gen → T004 data → T005 model GREEN; T006 dep)
        ├─► US1 (T007 RED → T008, T009[P], T010[P], T011 → GREEN)      ← MVP (with US2)
        ├─► US2 (T012 RED → T013 GREEN)
        │     └─► US3 (T014 coverage, T015 drift) [needs all routes annotated]
        └─► Polish (T016 update claims → T017 regression → T018 docs)
```

- **Foundational blocks all** (model + dep used everywhere). T002 RED before T005 GREEN; T003→T004→T005 sequential (generator → data → loader).
- **US1 and US2** both annotate routes (different routes/files mostly); US2's coverage result (US3/T014) needs both done.
- **T016 (claim updates)** must land with/just after US1+US2 or the full regression (T017) will show the expected 403s on old tests — do T016 before T017.

## Parallel opportunities

- Foundational: T002 (test) can be written while T003 (generator) is drafted, but T005 GREEN needs both.
- US1: **T009** (tools/mcp) and **T010** (skills/knowledge) are different files → parallel; T008/T011 touch agents/evals/members/workspaces.
- Route annotation across routers is largely independent per file.

## Implementation strategy

- **MVP = Setup + Foundational + US1 + US2** (enforcement live for writes AND reads/unknown-role). US3 (coverage + drift) hardens it; both are small.
- **TDD per story**: show the deny-matrix RED (currently allowed) before annotating; never weaken a test to pass. The existing-test claim updates (T016) are part of the feature and evidence enforcement works.
- Every GREEN runs the focused matrix + affected regressions; full regression at T017.

## Task count & format

- **Total: 18 tasks** — Setup 1, Foundational 5, US1 5, US2 2, US3 2, Polish 3.
- Per story: **US1 = 5**, **US2 = 2**, **US3 = 2**.
- All tasks carry checkbox + sequential ID + optional `[P]` + story label where applicable + explicit file path.
