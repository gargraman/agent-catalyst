# Phase 0 Research — M4-remainder Resource CRUD + Frontend Sync

Decisions resolving the Technical Context. No open `NEEDS CLARIFICATION` (the five spec clarifications are locked;
these are design choices derived from the existing code + constitution).

---

### D1 — One new migration `0005`, replicating the `0004` RLS pattern verbatim

- **Decision**: Add tables `tools`, `mcp_servers`, `mcp_tools`, `skills`, `workspace_settings`, `members`, `runs` in
  a single migration `0005`. Each tenant-owned table is `WorkspaceScoped` and gets RLS via the exact `0004` helper:
  `ALTER TABLE … ENABLE/FORCE ROW LEVEL SECURITY`, a `…_ws_isolation` policy
  (`USING/WITH CHECK workspace_id = NULLIF(current_setting('app.workspace_id', true),'')::uuid`), and
  `GRANT SELECT,INSERT,UPDATE,DELETE … TO ac_app`.
- **Rationale**: `0004` already proved the pattern; reusing it keeps the fail-closed tenancy guarantee uniform and
  the negative cross-tenant tests structurally identical.
- **Alternatives**: Multiple migrations per resource (rejected — one coherent schema step is easier to review/roll
  back); application-level filtering only (rejected — violates P5 defense-in-depth).

### D2 — Optimistic concurrency via `xmin`-as-ETag (reuse M4a D7)

- **Decision**: All mutable resources use the Postgres `xmin` system column as the ETag; PATCH requires `If-Match`,
  `UPDATE … WHERE id=:id AND xmin = cast(:etag as xid)`, returning `conflict.stale` (409) on no-match. Same int-bound
  `xmin` handling as M4a.
- **Rationale**: Migration-free, already implemented and tested for agents; consistent contract for the FE.
- **Alternatives**: A `version`/`updated_at` column (rejected — extra column + race windows vs the free system column).

### D3 — Idempotency via the in-flight guard (reuse M4a D8)

- **Decision**: Creates honor `Idempotency-Key` through the existing in-process `idempotency_guard` seam
  (`idempotency.in_progress` → 409). Durable (Redis) idempotency stays deferred.
- **Rationale**: Matches M4a; sufficient for the single-process pilot; named seam for later durability.

### D4 — Deterministic drift flagging (`api/drift.py`)

- **Decision**: A pure helper `flag_bound_agents(session, kind, resource_id)` sets `agents.needs_reeval = true` for
  every agent that has a version binding to the edited resource, invoked only when the edit touches the
  **trigger surface**: tool `auth_mode`/`oauth scopes`; skill `instructions`/`resources`/`version`/`content_hash`;
  KB content/retrieval config. Cosmetic fields (name/description/display) never flag. Binding lookup joins the
  relevant binding table (`tool_bindings.tool_id` / `skill_bindings.skill_id` / `retrieval_bindings.kb_id`) →
  `version_id` → `agent_id`.
- **Rationale**: Mirrors the console's existing `TRIGGER_FIELDS`/drift semantics (store.ts) and honors P6 — the
  frozen published snapshot is never mutated; the draft agent is merely flagged as needing a re-snapshot + re-eval.
- **Alternatives**: Recompute snapshot_hash eagerly (rejected — published versions are immutable; drift is a signal,
  not a mutation); DB trigger (rejected — flagging logic is field-selective and belongs in reviewable code, P3).

### D5 — First-class `runs` store; invoke persists through it (clarify #2)

- **Decision**: Add a `runs` table capturing `RunRecord` identity + outcome (model/profile id, version/prompt id,
  usage tokens, latency_ms, cost_usd, outcome, correlation_id, source, workspace_id, created_at). The invoke path's
  `RunContext.record_run` sink (today an in-memory `list.append` in M4a) is wired to INSERT one row per invoke
  **including failures** (P8). Run history + metrics read from this table. The heavy observability backend
  (tracing/analytics store, M6) layers on top later.
- **Rationale**: Closes the M4a in-memory-sink deferral; gives history/metrics a real source; keeps cost honest (P7).
- **Alternatives**: Defer runs to M6 (rejected by clarify — history/metrics are named FE seams needed now).

### D6 — Workspace administration: settings in, workspace-root create/delete out

- **Decision**: `workspace_settings` (1:1 with workspace; default provider/model + `limits` JSONB + `eval_policy`
  JSONB) and `members` (subject, display name, `console_role`, workspace_id) are workspace-scoped (RLS). US4 covers
  reading/updating settings/limits/eval-policy and the member roster (view/add/remove/set stored role) **within the
  active workspace**. Creating/deleting the `workspaces` root row (no RLS; platform-admin plane) stays deferred.
- **Rationale**: Settings/roster are plain per-tenant CRUD; the workspace-root plane is platform-admin + borders the
  deferred RBAC/IdP model (clarify #1: roles stored, not enforced; no IdP sync).
- **Alternatives**: Add settings columns onto `workspaces` (rejected — keeps the tenant root minimal and avoids
  RLS-exempting settings that should be tenant-scoped).

### D7 — Skills registry + unsigned provenance stub (clarify #5)

- **Decision**: `skills` table (slug, name, description, instructions, `resources` JSONB, `content_hash`, `status`
  draft/published, `provenance` JSONB {by, at}, `archived_at`). `POST /skills/:id/publish` sets `status='published'`
  and stamps provenance as an **explicitly-labeled unsigned stub** — no cryptographic signature. Existing
  `skill_bindings.skill_id` keeps **no FK** (M4a versions may reference ids that predate the registry — `0004` note).
- **Rationale**: Matches clarify #5 and avoids breaking existing binding rows; real signing deferred.

### D8 — Tools/MCP registry stores only non-secret config (FR-021)

- **Decision**: `tools` (name, description, `http_config` JSONB = endpoint/auth_mode/oauth_scopes [non-secret],
  risk_tier, archived_at); `mcp_servers` (name, url, transport, non-secret auth metadata, status, archived_at) +
  `mcp_tools` (server_id FK, name, description, enabled, risk_tier). **No secret value** is accepted/persisted/
  returned; a tool needing a real credential is created credential-pending and the actual custody is M5/Vault.
- **Rationale**: FR-021 + P5 (no secret leakage). `mcp_tools.server_id` is a real intra-feature FK (both tables new).

### D9 — Frontend sync: `openapi-typescript` client + representative hook swaps + tracker

- **Decision**: Add `openapi-typescript` (devDep) + a `gen:api` script that emits `frontend/src/lib/api/` types from
  the API's OpenAPI 3.1. Swap a representative hook set mock→real preserving existing shapes: M4a surfaces
  (`useAgents`/`addAgent`/`updateAgent`, playground `send()`, eval-run, publish) + US1 catalog
  (`useTools`/`addTool`/`updateTool`, `useSkills`/skill CRUD). Produce `docs/backend-integration-status.md` — a
  living tracker enumerating every `@backend` seam (`grep -rn "@backend" frontend/src`) classified real-vs-mock,
  grouped by resource, with totals. **Read `frontend/node_modules/next/dist/docs/` before writing any FE code**
  (modified Next.js caveat).
- **Rationale**: Delivers the user's second ask; proves the drop-in contract on a bounded surface (clarify: depth =
  representative, not all 253); the tracker is the P1/P9 honesty record.
- **Alternatives**: Full swap of all hooks (rejected — out of agreed depth, large regression surface); hand-written
  client (rejected — OpenAPI generation is the zero-drift contract per the plan).

### D10 — No live/paid calls anywhere

- **Decision**: All backend tests mock the gateway/runtime; the representative FE pages render against a locally-run
  API with a mocked provider. FE verification is `npm run build` + `npm run lint` + manual render (no FE unit harness
  exists). First live invoke remains the separate Phase-5 paid-call step.
- **Rationale**: Constitution — paid calls are never ordinary tests; honest FE verification given no test harness.

### D11 — Async worker + ingestion: shapes named, deployable deferred

- **Decision**: Eval stays inline (M4a). KB ingestion is metadata-only with an honest deferred status (no fabricated
  doc/chunk processing). The job-status (`/jobs/:id`, `/ingestion-jobs`) response shapes are named in the contract
  but not executed; the worker deployable is deferred.
- **Rationale**: Clarify + spec deferrals (ingestion → M7); keeps scope on plain CRUD.

### D12 — Soft-archive semantics (clarify #3)

- **Decision**: Archivable resources get a nullable `archived_at`; archive sets it (no hard delete). Default list
  excludes archived rows; an explicit `?include_archived=true` (or `status` filter) returns them; `GET /:id` still
  returns an archived row so bound references resolve as removable. Agents reuse the existing `status='archived'`.
- **Rationale**: Matches clarify #3 and the console's archive-not-delete policy; keeps bindings resolvable.

---

**Cross-cutting (all decisions)**: reuse the shared error envelope (`api/errors.py`) + cursor pagination
(`api/pagination.py`), the `tenant_session` (ac_app + GUC), and the M4a request dependencies (principal, workspace
resolution/authorization, rate-limit seam). OpenAPI 3.1 is the source of truth for the generated FE client.
