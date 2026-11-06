# Phase 1 Data Model — M4-remainder Resource CRUD + Frontend Sync

New/changed persistence (migration `0005`) and the API projections that mirror `frontend/src/lib/data.ts`. Every
tenant table is `WorkspaceScoped` (carries `workspace_id` + RLS per `0004`). All timestamps `timestamptz`; PKs are
`uuid DEFAULT gen_random_uuid()`. `xmin` (system column) is the ETag — no explicit version column.

## Schema changes (migration `0005`)

### Column additions to existing tables

- **`agents.needs_reeval boolean NOT NULL DEFAULT false`** — drift flag (FR-002/SC-005; see `drift.py`). Mirrors
  `Agent.needsReEval` (data.ts:80).
- **`knowledge_bases.archived_at timestamptz NULL`** — soft-archive (clarify #3). (Agents reuse `status='archived'`.)

### New tables

**`tools`** — HTTP tool registry (data.ts `RepoTool` :830, `HttpToolConfig` :815)
| column | type | notes |
|---|---|---|
| id, workspace_id | uuid | PK; FK workspaces |
| name | text | unique (workspace_id, name) — identity is `name` per store.ts updateTool |
| description | text | |
| kind | tool_kind | reuse enum (`0004`): `http`/`builtin` here |
| http_config | jsonb | **non-secret only**: endpoint, auth_mode (`shared_service`/`per_user`), oauth_scopes. No credential value (FR-021) |
| risk_tier | tool_risk_tier | reuse enum (`0004`) |
| archived_at | timestamptz NULL | soft-archive |
| created_at | timestamptz | |

**`mcp_servers`** (data.ts `McpServer` :694) + **`mcp_tools`** (data.ts `McpTool` :681)
| mcp_servers | type | notes |
|---|---|---|
| id, workspace_id | uuid | |
| name, url, transport | text | non-secret connection metadata |
| status | text | `connected`/`error`/`disabled` (honest; no live probe here) |
| archived_at, created_at | timestamptz | |

| mcp_tools | type | notes |
|---|---|---|
| id, workspace_id | uuid | |
| server_id | uuid | **FK mcp_servers(id)** (both new → real FK) |
| name, description | text | |
| enabled | boolean | |
| risk_tier | tool_risk_tier | |

**`skills`** (data.ts `Skill` :603, `SkillResource` :593)
| column | type | notes |
|---|---|---|
| id, workspace_id | uuid | |
| slug, name, description | text | unique (workspace_id, slug) |
| instructions | text | on-invoke body |
| resources | jsonb | list of {name, kind, sizeKB?, contentHash?} |
| content_hash | text | frozen content identity (folds into bound snapshot_hash) |
| status | text | `draft`/`published` |
| provenance | jsonb NULL | **unsigned stub** {by, at} — no signature (clarify #5) |
| archived_at, created_at | timestamptz | |

> `skill_bindings.skill_id` / `tool_bindings.tool_id` keep **no FK** (M4a versions may reference pre-registry ids —
> `0004` note). Registries exist; retrofitting binding FKs is deferred.

**`workspace_settings`** (1:1 workspace; data.ts `WorkspaceSettings` :1055, `Limits` :1056, `EvalPolicy` :1064)
| column | type | notes |
|---|---|---|
| workspace_id | uuid | PK + FK workspaces (1:1) |
| default_provider, default_model | text | |
| limits | jsonb | {monthlySpendUsd, maxConcurrent, requestTimeoutS} — stored P1, enforced later (M6) |
| eval_policy | jsonb | {minCases, qualityThreshold, efficiencyBudgetPct, allowLowConfidenceOverride} |

**`members`** (data.ts `Member` :53; iam.ts `ConsoleRole` :20)
| column | type | notes |
|---|---|---|
| id, workspace_id | uuid | |
| subject | text | stable principal id (no IdP sync — clarify #1) |
| display_name, email | text | |
| console_role | text | `viewer`/`builder`/`publisher` — **stored, not enforced** here (clarify #1) |
| created_at | timestamptz | unique (workspace_id, subject) |

**`runs`** (data.ts `Run` :975; source for history + metrics)
| column | type | notes |
|---|---|---|
| id, workspace_id | uuid | |
| agent_id, version_id | uuid | identity of what ran |
| model, profile_id | text | provider/model identity (P7/P8) |
| prompt_hash | text | version/prompt identity |
| usage | jsonb | {input, output, total} tokens |
| latency_ms | int | |
| cost_usd | numeric(12,6) | from the runtime `RunRecord` (our profiles — never transport) |
| outcome | text | `ok`/`error`/`refused`/`timeout` |
| source | text | `playground`/`published`/`eval` |
| correlation_id | text | |
| created_at | timestamptz | index (workspace_id, created_at desc) for history/keyset |

**RLS**: `tools, mcp_servers, mcp_tools, skills, workspace_settings, members, runs` all get enable+force+
`…_ws_isolation` policy + grant to `ac_app` (identical to `0004._enable_rls`). `test_migration_0005` asserts each
policy exists.

## API projections (mirror data.ts; shapes are drop-in)

Each resource exposes typed `…Out` / `…Create` / `…Patch` Pydantic models matching the exported `data.ts` types, with
the shared error envelope and `Page[T]` cursor pagination. Representative mappings:

- **Tool**: `ToolOut` ↔ `RepoTool` (data.ts:830); create/patch accept name/description/http_config(non-secret)/
  risk_tier. Patch of auth_mode/scopes → `drift.flag_bound_agents`.
- **Skill**: `SkillOut` ↔ `Skill` (:603); `PublishOut` stamps unsigned provenance. Content edit → drift flag.
- **KnowledgeBase**: `KnowledgeBaseOut` ↔ `KnowledgeBase` (:492) minus live ingestion counts; ingestion status is an
  honest deferred value (no fabricated docs/chunks). Content/retrieval-config edit → drift flag.
- **Workspace admin**: `WorkspaceSettingsOut`/`LimitsOut`/`EvalPolicyOut` ↔ (:1055/:1056/:1064); `MemberOut` ↔ `Member` (:53).
- **Run / metrics**: `RunOut` ↔ `Run` (:975); `RunsStatsOut`/`EvalStatsOut`/`DashStatsOut`/`SeriesOut`
  (:1230/:1245/:1220/:1149) computed by server-side aggregation over `runs`.

## Lifecycle / state

- **Archive**: `active → archived` via `archived_at` (soft); default list hides archived; `?include_archived=true`
  shows them; `GET /:id` returns archived (clarify #3). Reversible by clearing `archived_at` (restore).
- **Skill publish**: `draft → published` (+ unsigned provenance). Only published skills are bindable (future bind
  path); publish never signs.
- **Drift flag**: any bound resource trigger-surface edit sets `agents.needs_reeval=true`; cleared when the builder
  re-snapshots/re-evaluates (existing agent-version flow). Published version snapshots are immutable (P6).

## Validation rules (deterministic, fail-closed — P3)

- Names/slugs required, non-empty, length-bounded; slug unique per workspace.
- `http_config` rejects any key that looks like a secret (no raw credential persisted — FR-021).
- `risk_tier` ∈ enum; `console_role` ∈ {viewer,builder,publisher}; `eval_policy`/`limits` numeric bounds validated.
- Malformed id → `input.invalid` (400); well-formed foreign/absent id → not-found (404); stale ETag →
  `conflict.stale` (409); in-flight idempotency key → `idempotency.in_progress` (409).
