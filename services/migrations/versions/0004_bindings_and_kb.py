"""0004 snapshot-complete bindings + minimal knowledge_bases (feature 001, spec 001-m0-data-model)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-03

Adds exactly what the version snapshot (runtime/hashing.py:compute_snapshot_hash + AgentVersion.to_snapshot)
reads: knowledge_bases (for retrieval_binding.kb.embedding_model), retrieval_bindings, skill_bindings,
tool_bindings — each workspace-owned with RLS (enabled + forced), mirroring 0002.

DDL follows docs/backend/01-data-model.md. Scope decision (spec Clarifications / research D7):
- knowledge_bases is created WITHOUT a vector column (chunks/data_sources/documents are ingestion → M7).
- Registry tables (tools, skills, mcp_tools, subagent_configs) are deferred (M4+), so the binding
  columns that would FK to them (tool_id/mcp_tool_id/subagent_config_id/skill_id) are plain nullable
  uuids with NO FK constraint for now. Real FKs that exist today are kept: workspace_id → workspaces,
  version_id → agent_versions, retrieval_bindings.kb_id → knowledge_bases.
- Per-binding immutability triggers are deferred (research D6): a binding change already busts
  agent_versions.snapshot_hash (frozen by 0002), so edits are detectable/version-busting.
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_RLS_TABLES = ("knowledge_bases", "retrieval_bindings", "skill_bindings", "tool_bindings")


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {table}_ws_isolation ON {table}
          USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
          WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
        """
    )
    # ac_app is covered by 0003's default privileges, but grant explicitly to avoid ordering surprises.
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO ac_app")


def upgrade() -> None:
    # Enums (verbatim from 01-data-model.md §3). agent_status already exists (0001) — do not recreate.
    op.execute("CREATE TYPE tool_kind      AS ENUM ('http','builtin','subagent','mcp')")
    op.execute("CREATE TYPE tool_risk_tier AS ENUM ('read','write','destructive')")

    op.execute(
        """
        CREATE TABLE knowledge_bases (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id    uuid NOT NULL REFERENCES workspaces(id),
            slug            text NOT NULL,
            name            text NOT NULL,
            embedding_model text NOT NULL,                 -- read by the snapshot (retrieval binding)
            embedding_dim   int  NOT NULL,
            chunk_config    jsonb NOT NULL DEFAULT '{}',
            index_version   int  NOT NULL DEFAULT 1,       -- kb_index_version source (EXCLUDED from snapshot_hash)
            access_policy   jsonb NOT NULL DEFAULT '{"visibility":"workspace"}'::jsonb,
            created_at      timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, slug)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE retrieval_bindings (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id     uuid NOT NULL REFERENCES workspaces(id),
            version_id       uuid NOT NULL REFERENCES agent_versions(id),
            kb_id            uuid NOT NULL REFERENCES knowledge_bases(id),
            kb_index_version int  NOT NULL,                -- pinned at freeze; NOT folded into snapshot_hash
            retrieval_config jsonb NOT NULL,
            injection        text NOT NULL DEFAULT 'tool',
            UNIQUE (version_id, kb_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE skill_bindings (
            id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id       uuid NOT NULL REFERENCES workspaces(id),
            version_id         uuid NOT NULL REFERENCES agent_versions(id),
            skill_id           uuid NOT NULL,              -- registry (skills) deferred (M4+): no FK yet
            skill_content_hash text NOT NULL,              -- pinned at freeze; folds into snapshot_hash
            UNIQUE (version_id, skill_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE tool_bindings (
            id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id       uuid NOT NULL REFERENCES workspaces(id),
            version_id         uuid NOT NULL REFERENCES agent_versions(id),
            kind               tool_kind NOT NULL,
            tool_id            uuid,                       -- registry (tools) deferred: no FK yet
            mcp_tool_id        uuid,                       -- registry (mcp_tools) deferred: no FK yet
            subagent_config_id uuid,                       -- registry (subagent_configs) deferred: no FK yet
            schema_snapshot    jsonb NOT NULL,             -- frozen at bind
            config_snapshot    jsonb NOT NULL DEFAULT '{}',
            risk_tier          tool_risk_tier NOT NULL DEFAULT 'read',
            requires_approval  boolean NOT NULL DEFAULT false,
            auth_mode          text,                       -- 'shared_service' | 'per_user'; NULL for non-MCP
            mcp_scopes         text[]                      -- NULL for non-MCP
        )
        """
    )

    op.execute("CREATE INDEX ix_retrieval_bindings_version ON retrieval_bindings(version_id)")
    op.execute("CREATE INDEX ix_skill_bindings_version ON skill_bindings(version_id)")
    op.execute("CREATE INDEX ix_tool_bindings_version ON tool_bindings(version_id)")
    op.execute("CREATE INDEX ix_knowledge_bases_workspace ON knowledge_bases(workspace_id)")

    for t in _RLS_TABLES:
        _enable_rls(t)


def downgrade() -> None:
    for t in _RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {t}_ws_isolation ON {t}")
    op.execute("DROP TABLE IF EXISTS tool_bindings CASCADE")
    op.execute("DROP TABLE IF EXISTS skill_bindings CASCADE")
    op.execute("DROP TABLE IF EXISTS retrieval_bindings CASCADE")
    op.execute("DROP TABLE IF EXISTS knowledge_bases CASCADE")
    op.execute("DROP TYPE IF EXISTS tool_risk_tier")
    op.execute("DROP TYPE IF EXISTS tool_kind")
