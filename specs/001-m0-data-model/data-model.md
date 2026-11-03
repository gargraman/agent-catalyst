# Data Model — M0 Data-Model Completion (Phase 1)

Authoritative DDL source: `docs/backend/01-data-model.md`. This document records what *this feature* implements (ORM over existing tables + migration `0004` for the snapshot-complete subset) and the `VersionSnapshot` shape. Where the doc and the deployed schema differ, see `research.md` D5.

## Existing tables (modeled by ORM, already migrated in 0001–0003)

### Workspace (`workspaces`)
Tenant root. Columns (0001): `id uuid pk`, `name`, `group_prefix`, `created_at`. No RLS (it *is* the tenant root — see `0002` note). ORM: `Workspace`.

### Agent (`agents`)
Columns (0001): `id`, `workspace_id → workspaces`, `name`, `status agent_status`, `active_version_id → agent_versions` (circular FK), `created_at`. RLS: `workspace_id = app.workspace_id`. ORM: `Agent` (relationship `versions`, `active_version`).

### AgentVersion (`agent_versions`)
Columns (0001, the deployed subset): `id`, `agent_id → agents`, `workspace_id → workspaces`, `label`, `system_prompt`, `prompt_hash`, `snapshot_hash`, `model_config` (→ attr `model_config_`), `guardrail_config`, `io_contract`, `memory_config`, `context_config`, `profile_snapshot`, `publishable`, `created_at`. Immutability trigger (`0002`) freezes all behavior columns; `publishable`/`created_at` mutable. RLS: `workspace_id = app.workspace_id`.
> Drift vs doc (not implemented here — research.md D5): doc adds `status`, `created_by`, `note`, `unique(agent_id,label)`.
ORM: `AgentVersion` with relationships `retrieval_bindings`, `tool_bindings` (ordered), `skill_bindings`, and method `to_snapshot()`.

## New tables (migration `0004`, this feature)

> All are **WorkspaceScoped**: carry `workspace_id uuid not null references workspaces(id)` and get an RLS policy identical in shape to `0002` (`USING/WITH CHECK workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid`). FK notes per research.md D7.

### KnowledgeBase (`knowledge_bases`) — full, per doc
`id`, `workspace_id`, `slug`, `name`, `embedding_model` (read by the snapshot), `embedding_dim`, `chunk_config jsonb`, `index_version int default 1`, `access_policy jsonb`, `created_at`, `unique(workspace_id, slug)`. No `vector` column (chunks deferred to M7). ORM: `KnowledgeBase`.

### RetrievalBinding (`retrieval_bindings`)
`id`, `workspace_id`, `version_id → agent_versions`, `kb_id → knowledge_bases`, `kb_index_version int`, `retrieval_config jsonb`, `injection text default 'tool'`, `unique(version_id, kb_id)`. Snapshot reads: `kb.embedding_model`, `retrieval_config`, `injection`, `kb_id`; **excludes** `kb_index_version` from the hash. ORM: `RetrievalBinding` (relationship `kb`).

### SkillBinding (`skill_bindings`)
`id`, `workspace_id`, `version_id → agent_versions`, `skill_id uuid` (no FK yet — registry deferred), `skill_content_hash text`, `unique(version_id, skill_id)`. Snapshot reads: `skill_id`, `skill_content_hash`. ORM: `SkillBinding`.

### ToolBinding (`tool_bindings`)
`id`, `workspace_id`, `version_id → agent_versions`, `kind tool_kind`, `tool_id uuid` / `mcp_tool_id uuid` / `subagent_config_id uuid` (plain uuids, no FK — registries deferred), `schema_snapshot jsonb`, `config_snapshot jsonb`, `risk_tier tool_risk_tier default 'read'`, `requires_approval bool default false`, `auth_mode text`, `mcp_scopes text[]`. Snapshot reads all of the above (except the row `id`). ORM: `ToolBinding`, relationship ordered by `(kind, tool_id, mcp_tool_id, subagent_config_id)`.

### Enums (created in `0004`)
- `tool_kind` — values per doc (e.g. `http`, `builtin`, `mcp`, `subagent`); confirm exact set against `01-data-model.md` enum section at implementation time.
- `tool_risk_tier` — `read | write | destructive` (confirm against doc).

## VersionSnapshot (runtime input — `services/runtime/snapshot.py`)

A **frozen dataclass** (`@dataclass(frozen=True)`), assembled by `AgentVersion.to_snapshot()`, imported by `runtime/` **only** (never the ORM). Carries the fully-resolved, behavior-defining config the runtime executes, plus the content identity:

- Identity: `version_id`, `agent_id`, `workspace_id`, `label`, `snapshot_hash` (== stored `snapshot_hash`, recomputable via `compute_snapshot_hash`).
- Model: `model_config` (provider, model, api_version, params, fallback), `profile_snapshot` (resolved capability profiles).
- Behavior: `system_prompt`, `prompt_hash`, `io_contract`, `memory_config`, `context_config`, `guardrail_config`.
- Bindings (resolved, immutable tuples): `retrieval` (kb_id, embedding_model, retrieval_config, injection, kb_index_version-for-display), `tools` (kind, schema_snapshot, config_snapshot, risk_tier, requires_approval, subagent_config_id, auth_mode, mcp_scopes, identity keys), `skills` (skill_id, skill_content_hash).

**Invariants**:
- Built purely from the frozen `AgentVersion` row + its bindings — never from the live `agents` row (FR-009).
- `snapshot_hash` is clone-stable (per-row `id`s excluded) and excludes `kb_index_version` (reuses `compute_snapshot_hash`; FR-007/008).
- Exact field set is the minimum the runtime (M2) consumes; additions are additive and must fold into the hash only if behavior-defining.

## Relationships summary

```
Workspace 1─* Agent 1─* AgentVersion 1─* {RetrievalBinding, SkillBinding, ToolBinding}
                                   RetrievalBinding *─1 KnowledgeBase
Agent.active_version_id ─1 AgentVersion   (circular FK, per 0001)
```

## State / immutability
- `AgentVersion` frozen columns: write-once (enforced by `0002` trigger; ORM surfaces the error — FR-010). "Edit a version" = create a new version row.
- Bindings: written with the version; behavior changes surface via `snapshot_hash` (research.md D6).
