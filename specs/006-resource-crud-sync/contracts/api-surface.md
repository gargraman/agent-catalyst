# API Surface — M4-remainder Resource CRUD + Frontend Sync

OpenAPI 3.1 is the source of truth (emitted by the FastAPI app; the FE client is generated from it). All routes are
workspace-scoped through `tenant_session` (ac_app + `app.workspace_id` GUC); the active workspace is resolved +
authorized from the caller claim exactly as M4a. All responses use the shared error envelope and `Page[T]` cursor
pagination. Prefix: `/api/v1`.

## Conventions (reused from M4a)

- **Auth/tenancy**: `X-Dev-Principal` (session stub) + `X-Workspace-Id`/`?workspace=`; unauthorized workspace → 403.
- **Pagination**: `?limit=&cursor=`; `Page[T] = { items: [...], next_cursor }`; keyset on `(created_at, id)` desc.
- **Concurrency**: mutating reads return `ETag` (xmin); `PATCH` requires `If-Match` → `409 conflict.stale` on mismatch.
- **Idempotency**: `POST` creates accept `Idempotency-Key` → `409 idempotency.in_progress` if in flight.
- **Archive filter**: list endpoints default to active-only; `?include_archived=true` includes archived; `GET /:id`
  returns archived rows.
- **Error codes**: `input.invalid` (400), `unauthorized` (401), `forbidden` (403), `not_found` (404),
  `conflict.stale` (409), `idempotency.in_progress` (409), `rate_limited` (429), `internal` (500 — no leak).

## Endpoints

### US1 — Tool & skill catalog

| Method | Path | Body / notes | Returns |
|---|---|---|---|
| POST | `/tools` | `ToolCreate` (non-secret http_config) | 201 `ToolOut` + ETag |
| GET | `/tools` | `?include_archived` | `Page[ToolOut]` |
| GET | `/tools/{id}` | | `ToolOut` + ETag |
| PATCH | `/tools/{id}` | `ToolPatch` + `If-Match`; auth_mode/scopes edit → drift flag | `ToolOut` |
| POST | `/tools/{id}/archive` | soft-archive | `ToolOut` |
| POST | `/mcp-servers` · GET · GET/{id} · PATCH · archive | `McpServerCreate/Patch` | `McpServerOut` (incl. `tools: McpToolOut[]`) |
| POST | `/mcp-servers/{id}/tools` · PATCH `/mcp-tools/{id}` | manage exposed tools | `McpToolOut` |
| POST | `/skills` · GET · GET/{id} · PATCH · archive | `SkillCreate/Patch`; content edit → drift flag | `SkillOut` |
| POST | `/skills/{id}/publish` | status→published + **unsigned provenance** stamp | `SkillOut` (status=published) |

### US3 — Knowledge bases (metadata)

| Method | Path | Notes | Returns |
|---|---|---|---|
| POST | `/knowledge-bases` | `KnowledgeBaseCreate` (name, embedding model, chunk config) | 201 `KnowledgeBaseOut` |
| GET · GET/{id} · PATCH · archive | `/knowledge-bases[/{id}]` | content/retrieval edit → drift flag | `KnowledgeBaseOut` / `Page[...]` |
| GET | `/ingestion-jobs` | **honest deferred** — returns empty/ named-not-executing status (no fabricated runs) | `Page[IngestionJobOut]` |

### US4 — Workspace & member administration (within active workspace)

| Method | Path | Notes | Returns |
|---|---|---|---|
| GET | `/workspace/settings` | settings + limits + eval-policy for active ws | `WorkspaceSettingsOut` + ETag |
| PATCH | `/workspace/settings` | `If-Match` | `WorkspaceSettingsOut` |
| PATCH | `/workspace` | rename (name/group_prefix) + `If-Match` | `WorkspaceOut` |
| GET | `/members` | roster | `Page[MemberOut]` |
| POST | `/members` · DELETE `/members/{id}` | add / remove | `MemberOut` / 204 |
| PATCH | `/members/{id}` | set **stored** console role (not enforced here) | `MemberOut` |

> Workspace-root create/delete is **out of scope** (platform-admin plane, deferred — see plan gap #2).

### US5 — Run history + metrics

| Method | Path | Notes | Returns |
|---|---|---|---|
| GET | `/runs` | newest-first, keyset paginated | `Page[RunOut]` |
| GET | `/runs/{id}` | | `RunOut` |
| GET | `/metrics/dashboard` | aggregates over `runs` for active ws | `DashStatsOut` (+ spark `Series`) |
| GET | `/metrics/runs` · `/metrics/evals` | roll-ups | `RunsStatsOut` / `EvalStatsOut` |

> Invoke (M4a `POST /agents/{id}/invoke`) is **extended**, not re-added: it now persists one `runs` row per
> invocation (success or failure) via `RunContext.record_run` → DB insert.

## Frontend contract sync (US2)

- **Client generation**: `npm run gen:api` → `openapi-typescript` reads the emitted OpenAPI 3.1 → `frontend/src/lib/api/`.
  Zero drift is a gate (regeneration produces no diff against the committed client in CI-equivalent check).
- **Representative hook swaps** (shapes preserved, source swapped mock→real): agents (`useAgents`/`addAgent`/
  `updateAgent`), playground `send()` (invoke SSE), eval-run, publish, tools (`useTools`/`addTool`/`updateTool`),
  skills CRUD. Loading/empty/error states preserved.
- **Tracker** — `docs/backend-integration-status.md`:

  ```text
  # Backend Integration Status (mock ↔ real)
  Generated against: <commit> | Seams: <total>  Real: <n>  Mock: <m>

  ## agents        [REAL]  POST/GET/LIST/PATCH /agents, versions, publish, invoke, eval-runs
  ## tools         [REAL]  /tools CRUD + archive            (feature 006)
  ## skills        [REAL]  /skills CRUD + publish           (feature 006)
  ## knowledge     [REAL meta / MOCK ingestion]  /knowledge-bases CRUD; ingestion deferred (M7)
  ## workspace     [REAL]  settings/limits/eval-policy, members roster (feature 006)
  ## runs/metrics  [REAL]  /runs, /metrics/*                (feature 006)
  ## provider-keys [MOCK]  secret custody deferred (M5)
  ## tokens        [MOCK]  runtime token mint deferred (M5)
  ## channels      [MOCK]  deferred (P2)
  ...one row per @backend seam family, each REAL or MOCK with the reason...
  ```

  Built by enumerating `grep -rn "@backend" frontend/src` and classifying each family; totals must reconcile with the
  grep count (SC-004).
