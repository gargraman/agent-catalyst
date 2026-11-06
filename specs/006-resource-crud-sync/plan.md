# Implementation Plan: M4-remainder — Resource CRUD + Frontend Contract Sync

**Branch**: `006-resource-crud-sync` | **Date**: 2026-11-05 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/006-resource-crud-sync/spec.md`

## Summary

Widen the real backend from the six M4a surfaces to the remaining **plain, non-secret resource CRUD** the console
needs, and wire a **representative slice** of the frontend to it with a **living mock-vs-real tracker**. New
workspace-scoped registries (`tools`, `mcp_servers` + `mcp_tools`, `skills`), administration (`workspace_settings`,
`members`), knowledge-base *metadata* CRUD (reusing the existing `knowledge_bases` table), and a first-class durable
`runs` store feeding run history + dashboard metrics. Every new table gets RLS (enable + force + ws-isolation policy,
grant to `ac_app`) exactly mirroring migration `0004`; every endpoint reuses the M4a patterns — typed Pydantic
contracts mirroring `frontend/src/lib/data.ts`, the shared error envelope + cursor pagination, `xmin`-as-ETag
optimistic concurrency, the in-flight idempotency guard, and the `tenant_session` (ac_app + `app.workspace_id` GUC).
A new deterministic **drift helper** flags bound agents for re-eval when a bound tool/skill/KB's trigger surface
changes. `services/runtime` and `services/evals` stay pure — all new code lands in `services/api`. Fully mocked at
the provider boundary; no live/paid calls.

**This is milestone-sized** (5 prioritized user stories + one migration + a frontend pass). The plan designs the
whole surface; `/speckit-tasks` phases it so **US1 + US2 are the shippable MVP** and US3–US5 follow incrementally.

### Verification of recommended actions + gaps surfaced (per request)

All five clarify decisions are carried into the design (member roster unenforced → §Member plane; runs table here →
`runs` store; soft-archive visibility → `archived_at` + list filter; exact drift-trigger set → drift helper; skill
signing = unsigned provenance stub). **Gaps this planning pass surfaced that were not explicit in the spec** (now
designed in, flagged for owner awareness):

1. **`needs_reeval` has no home in the ORM.** The frontend models `Agent.needsReEval`, but the backend `agents`
   table has no such column. FR-002/SC-005 require it — so `0005` **adds `agents.needs_reeval boolean`**. (New state, called out.)
2. **Workspace-root create/delete vs settings.** The `workspaces` table is the tenant ROOT (no RLS; filtered by
   membership, not the GUC). Creating/deleting a *workspace* is a platform-admin plane and stays **deferred** — US4
   covers only *settings/limits/eval-policy* and the *member roster* **within** the caller's active workspace.
3. **Soft-archive needs a column on KBs.** Agents already have `status='archived'`; `knowledge_bases` has no status
   column, so archivable new tables (and KB) get a nullable `archived_at`.
4. **Frontend has no automated test harness.** Per `frontend/CLAUDE.md` there is no `npm test`. FE verification is
   therefore `npm run build` + `npm run lint` (type/build) + a manual page render against the running API — not
   fabricated FE unit tests (honest per the constitution's API/frontend-integration row).
5. **Tool/MCP credential capture is a secret → deferred.** Tools store only **non-secret** config (endpoint,
   auth-mode, scopes). A tool that needs a real secret cannot be fully functional until M5/Vault; it is created with
   no persisted secret and surfaced as credential-pending (FR-021 forbids persisting a secret here).

## Technical Context

**Language/Version**: Python 3.12.13 (existing `services/.venv`); frontend TypeScript/React 19 on the modified
Next.js 16.3.8 (read `frontend/node_modules/next/dist/docs/` before any FE code — `frontend/AGENTS.md`).

**Primary Dependencies**: Backend — FastAPI/Starlette, SQLAlchemy 2.0 async (asyncpg), Alembic, Pydantic v2 +
`pydantic-settings`, httpx (ASGITransport test client). **All already installed (`.[api]`/`.[test]`) — no new backend
deps.** Frontend — add `openapi-typescript` (dev dependency) to generate the typed client from the emitted OpenAPI
3.1; no runtime FE deps added. Reuses `api/{errors,pagination,db,tenancy,models,settings,deps,sse}.py`.

**Storage**: Postgres (compose stack), migrations `0001`–`0004` today. **One new migration `0005`** adds the
registry/admin/ops tables + RLS, plus `agents.needs_reeval` and `archived_at` columns. Request path uses the existing
`tenant_session(workspace_id)` factory. KB table already exists (`0004`) — reused, not recreated.

**Testing**: pytest + pytest-asyncio (`asyncio_mode=auto`). Unit/contract tests via httpx `ASGITransport` with
dependency overrides (mocked principal, mocked runtime/gateway). Integration tests (`pytest.mark.integration`, skip
when DB unreachable) exercise real `tenant_session` + RLS — including the **negative cross-tenant** proof for every
new table (FR-016). Frontend: `npm run build` + `npm run lint` (no unit harness exists).

**Target Platform**: Linux server — the `api` deployable; the Next.js console for the FE swap.

**Project Type**: Web service (CRUD API over pure libraries) + a representative frontend wiring pass.

**Performance Goals**: Not latency-targeted. List endpoints MUST paginate (cursor keyset, newest-first) so large
catalogs don't unbounded-scan; metrics aggregate server-side.

**Constraints**: `services/runtime` + `services/evals` stay import-pure (both purity tests green). Tenancy
fail-closed (unset/invalid scope → zero rows), proven per table. **No secret value** persisted or returned (FR-021;
secret-scan stays green). No live/paid provider calls.

**Scale/Scope**: Single corporate pilot tenant. In: tools/MCP/skills registries, KB metadata, workspace
settings + member roster, runs + metrics, representative FE swap + tracker. Out (named, deferred): secret custody +
provider-keys/tokens/tool-approval (M5), RBAC enforcement + OIDC/IdP (M5), real ingestion/embeddings (M7), channels
(P2), async-worker deployable, workspace-root create/delete.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | How this feature complies | Status |
|---|---|---|
| **P1 Verifiable outcome / honesty** | Delivers real CRUD the builder can use; the tracker is the honesty instrument (mock never shown as real); deferrals labeled; completion report names commands + unverified items. | ✅ Pass |
| **P2 Govern scope / phases** | M4-remainder under the existing phased plan; non-goals explicit (secrets/RBAC/ingestion/channels deferred); scope chosen with the owner (CRUD not security). | ✅ Pass |
| **P3 Deterministic controls** | Input validation, workspace authorization, idempotency, optimistic concurrency, drift flagging — all in code, fail-closed. | ✅ Pass |
| **P4 Evidence about agent behavior** | No new judge/gate; publish stays advisory (M4a). Drift flag marks versions needing re-eval; it does not fabricate a verdict. | ✅ Pass |
| **P5 Tenant boundaries** | **Load-bearing.** Every new table is WorkspaceScoped with RLS (enable+force+policy, `0004` pattern); negative cross-tenant tests required per table; owner role never serves requests. | ✅ Pass |
| **P6 Reproducible / same runtime** | No runtime/eval changes; invoke still wraps the same `runtime.invoke`. New code only in `api`. Published version snapshots stay immutable; editing a bound resource flags re-eval rather than mutating a frozen snapshot. Purity tests stay green. | ✅ Pass |
| **P7 Provider/cost honesty** | Run records store cost from the runtime `RunRecord` (our profiles); metrics aggregate those recorded values, never recomputed from transport. | ✅ Pass |
| **P8 Observable / honest failure** | `runs` persists model/version identity, usage, latency, cost, outcome, correlation id for every invoke incl. failures; deferred capabilities report honest state (no fabricated ingestion counts). | ✅ Pass |
| **P9 Specify/test-first/evidence** | Full SDD chain; strict Red→Green→Refactor; RED (incl. a failing negative cross-tenant test per new table) shown before implementation. | ✅ Pass |

**Minimum Verification Checks applied:**
- *API / integration*: typed request/response + error contracts for every resource; OpenAPI 3.1 emitted →
  `openapi-typescript` zero-drift; **frontend type/build check** (`npm run build`/`lint`).
- *Database or tenant behavior*: `0005` migration up/down check; **workspace isolation + negative cross-tenant** per
  new table (headline evidence).
- *Security/secrets*: **no secret persisted/returned** (absence test + secret-scan); errors never leak internals.
- *Provider/runtime*: unchanged; invoke mocked; run cost from `RunRecord`.

**Result: PASS — no violations.** Complexity Tracking empty. *Honest note:* this feature introduces the first
schema migration since `0004` and the first frontend code changes of the backend build — both carry the extra
verification rows above.

## Project Structure

### Documentation (this feature)

```text
specs/006-resource-crud-sync/
├── plan.md              # This file
├── research.md          # Phase 0 — decisions (D1–D12)
├── data-model.md        # Phase 1 — new tables + API projections (mirror data.ts)
├── quickstart.md        # Phase 1 — how to validate
├── contracts/
│   └── api-surface.md   # Phase 1 — endpoints, error codes, headers, pagination, tracker format
├── checklists/requirements.md
└── tasks.md             # Phase 2 — /speckit-tasks (NOT here)
```

### Source Code (repository root)

```text
services/migrations/versions/
└── 0005_registries_members_runs.py   # NEW — tools, mcp_servers, mcp_tools, skills, workspace_settings,
                                       #        members, runs (+ RLS per 0004); agents.needs_reeval; archived_at

services/api/
├── models.py            # EXTEND — Tool, McpServer, McpTool, Skill, WorkspaceSettings, Member, Run ORM;
│                        #          needs_reeval on Agent; archived_at where archivable
├── drift.py             # NEW — flag_bound_agents(resource_kind, resource_id): deterministic re-eval flagging
├── schemas/             # NEW Pydantic contracts (mirror data.ts)
│   ├── tools.py · skills.py · knowledge.py · workspaces.py · members.py · runs.py · metrics.py
├── routers/             # NEW routers (+ EXTEND invoke)
│   ├── tools.py · skills.py · knowledge.py · workspaces.py · members.py · runs.py · metrics.py
│   └── invoke.py        # EXTEND — persist RunRecord to the runs table (replaces in-memory sink)
├── deps.py · errors.py · pagination.py · db.py · tenancy.py · settings.py · sse.py   # EXIST — reused
└── main.py              # EXTEND — register the new routers

services/tests/
├── test_api_tools.py · test_api_skills.py · test_api_knowledge.py
├── test_api_workspaces.py · test_api_members.py · test_api_runs.py · test_api_metrics.py
├── test_api_drift.py            # drift flagging (bound agent → needs_reeval; cosmetic → not)
├── test_api_tenancy.py          # EXTEND — negative cross-tenant for each new table
├── test_migration_0005.py       # up/down + RLS present on new tables
└── test_runtime_purity.py / test_evals_purity.py   # EXISTING — stay green

frontend/
├── src/lib/api/                 # NEW — openapi-typescript generated client + thin fetch wrappers
├── src/lib/store.ts             # EXTEND — swap representative hooks mock→real (agents/tools/skills/eval/publish/invoke)
└── package.json                 # EXTEND — openapi-typescript devDep + a `gen:api` script

docs/
└── backend-integration-status.md   # NEW — living mock-vs-real tracker (classifies every @backend seam)
```

**Structure Decision**: Extend the existing `services/api/` layer with per-resource schemas + routers and one
migration; add a small deterministic `drift.py` helper. No changes to `services/runtime` or `services/evals`
(consumed through existing contracts — P6 purity intact). Frontend changes are confined to a new generated
`src/lib/api/` client and a representative set of `store.ts` hook swaps, plus the tracker doc.

## Complexity Tracking

*No constitutional violations — section intentionally empty.*
