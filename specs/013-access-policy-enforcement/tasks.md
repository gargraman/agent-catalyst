---

description: "Task list — Per-Resource Access-Policy Enforcement (feature 013)"
---

# Tasks: Per-Resource Access-Policy Enforcement

**Input**: Design documents from `specs/013-access-policy-enforcement/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: REQUIRED. The constitution (P9) mandates a strict Red→Green→Refactor loop for every behavior-changing
task — the smallest failing test *for the intended behavior* precedes implementation, and Red evidence is shown to
the owner before Green. Focused commands are named per task.

**Commands** (run from repo root unless noted):
- Unit: `cd services && .venv/bin/python -m pytest -q <path>`
- Integration: `cd services && .venv/bin/python -m pytest -q -m integration <path>` (compose stack up; `alembic upgrade head`)
- Purity: `cd services && .venv/bin/python -m pytest -q tests/test_runtime_purity.py`
- FE: `cd frontend && nvm use 24 && npm run lint && npm run build`

**Format**: `[ID] [P?] [Story] Description` — `[P]` = parallelizable (different files, no incomplete deps).

---

## Phase 1: Setup (Shared data)

- [X] T001 [P] Create the canonical decision-table fixture `services/tests/fixtures/access_policy_cases.json` from `contracts/access-policy-evaluation.md` (all rows incl. executer, empty-restriction, private-owner, malformed). Shared parity artifact (FR-013/SC-003). Scaffolding data only — no behavior.

---

## Phase 2: Foundational (BLOCKS all user stories)

**Purpose**: the shared enforcement spine (evaluator, columns, dependency/helpers, schemas) that every story uses.

- [X] T002 [P] **Red**: `services/tests/test_access_policy_eval.py` — assert `can_use`/`can_manage` for every row of `access_policy_cases.json`. Fails (module absent). Cmd: `pytest -q tests/test_access_policy_eval.py`.
- [X] T003 **Green**: implement `services/api/access_policy.py` — pure module: `PolicyPrincipal` dataclass; `parse_policy`/normalize (fail-closed defaults per data-model.md); `can_use(policy, p)`, `can_manage(policy, p)` mirroring `access-policy.ts` + contracts table. NO FastAPI/HTTP/DB import (P6). Passes T002.
- [X] T004 **Red**: `services/tests/test_migration_0006.py` — after `upgrade head`, assert `members.teams` and `access_policy` exist with correct defaults on agents/tools/mcp_servers/mcp_tools/skills. Fails. Cmd: `pytest -q -m integration tests/test_migration_0006.py`.
- [X] T005 **Green**: ORM edits in `services/api/models.py` (`Member.teams: text[]`; `access_policy` JSONB on Agent/Tool/McpServer/McpTool/Skill) + migration `services/migrations/versions/0006_access_policy_and_member_teams.py` (additive, defaulted, reversible — data-model.md). Passes T004; existing RLS/immutability integration tests stay green.
- [X] T006 **Red**: `services/tests/test_api_members_teams.py` — member create/patch with `teams` round-trips and is RLS/workspace-scoped. Fails. Cmd: `pytest -q -m integration tests/test_api_members_teams.py`.
- [X] T007 **Green**: add `teams` to `services/api/schemas/members.py` (MemberOut + create/update) and accept/return it in `services/api/routers/members.py` (existing `members.manage` permission; no new permission). Passes T006.
- [X] T008 **Green**: enforcement spine in `services/api/deps.py` + `services/api/membership.py`: `membership.teams_for(session, subject)` (RLS) — **returns `[]` when the caller has no member row (fail-closed, never raises)** so existing tests/dev principals without a seeded member keep working on default-`workspace` resources; `access_principal` dependency (builds `PolicyPrincipal` = role from `workspace_claims[ws]`, teams via one read, subject); `ensure_use(policy, principal)` → raise `NotFound` when denied; `ensure_manage(policy, principal)` → `NotFound` if not usable else `AccessDenied`. No route wired yet (exercised by story tests). Keep `runtime/` untouched.
- [X] T009 [P] **Green**: add a typed `AccessPolicy` Pydantic model in `services/api/schemas/access_policy.py` using the **camelCase** field names of `access-policy.ts` (`visibility`, `useRoles`, `useTeams`, `manageRoles`, `manageTeams`, `owner`) — FR-018. Add optional `access_policy: AccessPolicy` to create/patch and surface it on `*Out` in `services/api/schemas/{agents,tools,mcp,skills,knowledge}.py`; validate/normalize via `access_policy.parse_policy` (malformed → `input.invalid`).

**Checkpoint**: evaluator proven by unit table; columns live; principal+helpers+schemas ready.

---

## Phase 3: User Story 1 — Restricted resource invisible & unusable to outsiders (P1) 🎯 MVP

**Goal**: restricted resources are omitted from non-matching members' lists, return 404 on direct read, 404 on
invoke — never 403 — while matching members and admins succeed (SC-001, no existence leak).

**Independent Test**: seed a restricted resource; non-matching member → list-omit + 404; matching → 200; admin → 200.

- [X] T010 [P] [US1] **Red**: in `services/tests/test_api_access_policy.py`, restricted USE across families (knowledge_bases + agents at minimum): list omits the resource for a non-matching member; `GET /{id}` → 404 (same body as absent, **never 403**); matching member → 200; admin → 200 even on empty/malformed policy. Fails. Cmd: `pytest -q -m integration tests/test_api_access_policy.py`.
- [X] T011 [P] [US1] **Red**: non-matching caller `invoke` AND `eval-run` → 404, with **no Run row written and no provider call** (SC-004). Both call `runtime.invoke` so both are agent USE. Fails.
- [X] T012 [US1] **Green**: wire `ensure_use` into `list` (filter page) and `GET /{id}` (404) across all 6 families' routers (`routers/{agents,tools,mcp,skills,knowledge}.py`) — including the **nested** mcp-tools list (`GET /mcp-servers/{id}/tools`). Admin/owner bypass via evaluator. KNOWN BEHAVIOR (G4): post-filter pagination may return a short page (<limit) when some rows are filtered out while a `next_cursor` is still set — accepted for the pilot (clients page until the cursor is null); do not over-engineer compensating over-fetch. Seed member rows WITH teams in the US1 tests so a matching member exists (G5).
- [X] T013 [US1] **Green**: wire `ensure_use` into the agent USE paths — `services/api/routers/invoke.py` AND `create_eval_run` in `routers/evals.py` — BEFORE `runtime.invoke(...)`, fail-closed 404, no side effect. NOTE: `create_eval_run` currently loads only the *version* (`_load_version`); it MUST now also load the `Agent` row and evaluate `ensure_use(agent.access_policy, principal)` first. Preserve P6 (enforcement in router, not runtime). Passes T010–T011.

**Checkpoint**: restricted visibility + no-leak + fail-closed invoke all green (MVP).

---

## Phase 4: User Story 2 — Private resource reachable only by owner + admin (P2)

**Goal**: private resources are owner-only (plus admin); other members get 404; executer callers denied.

**Independent Test**: private resource owned by O → O succeeds, other member 404, admin succeeds, executer denied.

- [X] T014 [P] [US2] **Red**: in `test_api_access_policy.py`, private resource — owner 200/manage; different non-admin member 404; admin 200; (unit-level) executer denied via evaluator. Fails. Cmd: `pytest -q -m integration tests/test_api_access_policy.py`.
- [X] T015 [US2] **Green**: owner-on-create **and owner-on-edit-to-private** — in each family's create AND patch handler, when `access_policy.visibility == "private"` and no `owner`, set `owner = principal.subject` (FR-012; never produce an ownerless-unreachable resource). Private read/use already enforced by T012/T013 via the evaluator; confirm manage path too. Passes T014.

**Checkpoint**: private tier correct; no ownerless-private lockout except admin-repairable.

---

## Phase 5: User Story 3 — Manage gated separately from use (P2)

**Goal**: manage (edit/publish/archive/restore) is denied 404 when not usable, 403 when usable-but-not-manageable;
admin/owner always manage; RBAC 403 still fires first.

**Independent Test**: member who can use but not manage → GET 200, PATCH 403; member who can't use → PATCH 404; admin → success.

- [X] T016 [P] [US3] **Red**: in `test_api_access_policy.py`, manage matrix across families — not-usable → 404 on PATCH/publish/archive; usable-not-manageable → 403; admin → success; RBAC-denied role → 403 before policy. Fails. Cmd: `pytest -q -m integration tests/test_api_access_policy.py`.
- [X] T017 [US3] **Green**: wire `ensure_manage` (404-then-403) into ALL manage handlers after the existing `require_permission` RBAC gate: per-family PATCH/archive/restore in `routers/{agents,tools,mcp,skills,knowledge}.py`; the agent **version create** (`agents.py::create_version`, `POST /agents/:id/versions`); and **publish** (`routers/evals.py::publish_version` — publish lives in the evals router and loads the agent itself). Passes T016.

**Checkpoint**: full use/manage separation with correct 404/403 semantics.

---

## Phase 6: User Story 4 — Execute-only caller gated on team alone (P3)

**Goal**: define & prove the `executer` evaluation rule (team-only; role-only denies; private denies all) for the
future token/channel plane. No HTTP route in this feature (token issuance is the next feature).

**Independent Test**: evaluator unit rows for executer (team allow / role-only deny / team mismatch deny / private deny).

- [X] T018 [US4] **Verify/Green**: confirm `access_policy_cases.json` + `test_access_policy_eval.py` assert the executer rows (contracts rows 11–13, 17); add any missing. Likely already green from T002/T003 — this task is the explicit executer-contract checkpoint. Cmd: `pytest -q tests/test_access_policy_eval.py`.

**Checkpoint**: executer evaluation contract locked for the next feature.

---

## Phase 7: Polish & Cross-Cutting

- [X] T019 [P] FE: add `teams: string[]` to the `Member` type + member create/update payloads in `frontend/src/lib/data.ts` (`@backend` marker). **Read `frontend/node_modules/next/dist/docs/` before any Next.js edit.**
- [X] T020 [P] FE: OPTIONAL display-only teams column in the **read-only** members roster (`frontend/src/app/(app)/settings/page.tsx`). **Do NOT add a team-edit control** — the roster is deliberately read-only / IdP-managed (FR-017). Teams are set via the members API, not the console.
- [X] T021 [P] FE: document teams as server-owned for authZ in `frontend/src/lib/iam.ts` / `auth.tsx` seams; note `MOCK_PROFILE.teams` is the pre-wiring preview.
- [X] T022 FE: re-emit `frontend/openapi.json` via `services/.venv/bin/python scripts/emit_openapi.py`; `npm run gen:api`; then `npm run lint && npm run build` clean (SC-006). (Depends on T007/T009 so the schema carries teams + access_policy.)
- [X] T023 Observability: emit a structured deny log (capability, tier, family+id, subject, workspace — no secret/token) from `ensure_use`/`ensure_manage` (FR-014). Add a focused assertion that no token/secret appears.
- [X] T024 Docs: reconcile `docs/backend-integration-status.md` (access-policy row → BUILT; member teams) and `docs/implementation-plan.md` M5 to the built state (standing rule: update docs at feature DoD).
- [X] T025 Full regression: `pytest -q` + `pytest -q -m integration` + purity test all green; run `quickstart.md` end-to-end. Report commands + evidence + any unverified requirement (P9).

---

## Dependencies & Execution Order

- **Setup (T001)** → no deps.
- **Foundational (T002–T009)** → blocks all stories. Order within: T002→T003 (evaluator); T004→T005 (migration/ORM); T006→T007 (member teams); then T008 (needs T003/T005/T007) and T009 [P].
- **US1 (T010–T013)** → after Foundational. MVP.
- **US2 (T014–T015)**, **US3 (T016–T017)** → after Foundational; independent of US1 but share the wired routers (sequence US1→US3 if same files to avoid conflicts).
- **US4 (T018)** → after T003 (evaluator). Essentially a checkpoint.
- **Polish (T019–T025)** → after the stories; T022 after T007/T009; T024/T025 last.

### Within each story
- Red test(s) before Green implementation; never weaken a test to pass.
- Models/migration before services; services/helpers before route wiring; route wiring before integration-green.

### Parallel opportunities
- T019/T020/T021 (distinct FE files) can run in parallel.
- T010/T011 (distinct test cases) can be authored in parallel; T014/T016 similarly.
- Router wiring tasks (T012/T017) touch the same router files — keep sequential to avoid conflicts.

---

## Implementation Strategy

**MVP** = Setup + Foundational + US1 (restricted visibility + no-leak + fail-closed invoke) — the headline P5
control, independently demonstrable. Then US2 (private), US3 (manage gating), US4 (executer contract), then the
FE contract-sync + docs polish. Commit per logical group; show Red evidence before each Green.
