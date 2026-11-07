# Backend Integration Status (mock ↔ real)

**Generated against:** feature 006 (M4-remainder) backend, branch `feat/backend-m0-foundation` · **Date:** 2026-11-05
**Total `@backend` seams** (`grep -rn "@backend" frontend/src | wc -l`): **253**

This tracker records the honest state of the mock→real migration on **two independent axes**:

- **Backend endpoint** — does a real, tested API endpoint exist? (BUILT / DEFERRED)
- **Frontend wired** — has the console hook been swapped from the mock store to that endpoint? (MOCK / REAL)

> ⚠️ **Current frontend-wired state: 0 / 253 seams are REAL.** The backend endpoints below are built and tested,
> but the frontend console still calls the in-memory mock store. Wiring requires generating the typed client
> (`npm run gen:api`) and swapping hooks, which needs a **Node/npm toolchain that is not available in the current
> build environment** (`node`/`npm` not found). The OpenAPI 3.1 contract is emitted to `frontend/openapi.json` and
> the `gen:api` script + `openapi-typescript` devDep are wired, so the wiring is a mechanical step in a Node env.
> Until then, no seam may be described as implemented in the console (P1/P9).

## By resource family

| Family | Backend endpoint | FE wired | Endpoints / reason |
|---|---|---|---|
| **agents** (CRUD, versions) | ✅ BUILT (M4a) | ⬜ MOCK | `POST/GET/LIST/PATCH /api/v1/agents`, `POST …/versions` |
| **invoke** (SSE) | ✅ BUILT (M4a) | ⬜ MOCK | `POST /api/v1/agents/:id/invoke` |
| **eval-runs** | ✅ BUILT (M4a) | ⬜ MOCK | `POST /api/v1/agents/:id/eval-runs` (inline) |
| **publish** (advisory) | ✅ BUILT (M4a) | ⬜ MOCK | `POST /api/v1/agents/:id/publish` — advisory divergence (verdict, not blocked) |
| **tools** (HTTP) | ✅ BUILT (006/US1) | ⬜ MOCK | `/api/v1/tools` CRUD + archive/restore |
| **mcp** (servers + tools) | ✅ BUILT (006/US1) | ⬜ MOCK | `/api/v1/mcp-servers[/:id[/tools]]`, `/api/v1/mcp-tools/:id` |
| **skills** | ✅ BUILT (006/US1) | ⬜ MOCK | `/api/v1/skills` CRUD + publish (unsigned stub) + archive/restore |
| **knowledge-bases** (metadata) | ✅ BUILT (006/US3) | ⬜ MOCK | `/api/v1/knowledge-bases` CRUD + archive/restore |
| **ingestion-jobs** | 🟡 DEFERRED (M7) | ⬜ MOCK | `GET /api/v1/ingestion-jobs` returns honest empty; real ingestion is M7 |
| **workspace settings/limits/eval-policy** | ✅ BUILT (006/US4) | ⬜ MOCK | `GET/PATCH /api/v1/workspace/settings`, `PATCH /api/v1/workspace` |
| **members** (roster) | ✅ BUILT (006/US4) | ⬜ MOCK | `/api/v1/members` CRUD — role stored, NOT enforced (RBAC → M5) |
| **runs** (history) | ✅ BUILT (006/US5) | ⬜ MOCK | `GET /api/v1/runs[/:id]` |
| **metrics** (dashboard) | ✅ BUILT (006/US5) | ⬜ MOCK | `GET /api/v1/metrics/{dashboard,runs,evals}` |
| **spend cap** (durable monthly) | ✅ BUILT (009/M6) | ⬜ MOCK | `GET /api/v1/workspace/spend` (MTD vs cap); enforced on invoke + eval-runs — `402 budget.exceeded` over cap, durable (Postgres ledger, F7) |
| **provider keys** | 🟡 DEFERRED (M5) | ⬜ MOCK | secret custody (Vault) deferred — no endpoint built |
| **runtime tokens** (mint) | 🟡 DEFERRED (M5) | ⬜ MOCK | token plane deferred |
| **tool approvals** | 🟡 DEFERRED (M5/P2) | ⬜ MOCK | approval workflow deferred |
| **access policy / RBAC** | 🟡 DEFERRED (M5) | ⬜ MOCK | per-resource policy + 4-role enforcement deferred |
| **OIDC / IdP sync** | 🟡 DEFERRED (M5) | ⬜ MOCK | real identity provisioning deferred |
| **channels** | 🟡 DEFERRED (P2) | ⬜ MOCK | messaging channels post-v1.0 |
| **workspace create/delete (root)** | 🟡 DEFERRED | ⬜ MOCK | platform-admin plane out of scope |

## Totals

- **Backend endpoints BUILT:** 13 families (agents, invoke, eval, publish, tools, mcp, skills, KB, workspace settings, members, runs, metrics; + honest-deferred ingestion-jobs stub).
- **Backend DEFERRED:** provider keys, tokens, tool approvals, access-policy/RBAC, OIDC, channels, workspace-root — all M5/M7/P2.
- **Frontend seams wired to real API:** **0 / 253** (pending Node toolchain for client-gen + hook swap).

## How to complete the frontend wiring (in a Node environment)

```bash
cd frontend
npm install                 # installs openapi-typescript (now in devDependencies)
npm run gen:api             # frontend/openapi.json → src/lib/api/schema.ts (typed client)
# then swap representative hooks in src/lib/store.ts (useAgents/addAgent/updateAgent, playground send,
#   eval-run, publish, useTools/addTool/updateTool, skills) mock→real, preserving hook shapes
npm run lint && npm run build   # zero type errors / zero client drift (SC-003)
```

Update this tracker's **FE wired** column to REAL as each family's hooks are swapped and build-verified.
