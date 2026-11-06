"""0005 registries (tools/mcp/skills) + workspace admin (settings/members) + runs; drift/archive columns

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-05

M4-remainder (feature 006). Adds the plain, non-secret resource tables the console needs:
- tools, mcp_servers, mcp_tools, skills       (bindable catalog registries)
- workspace_settings (1:1 workspace), members (roster — stored role, not enforced here)
- runs                                         (durable run-record store; source of history + metrics)

Each tenant table is workspace-owned with RLS (enabled + forced + ws-isolation policy + grant to ac_app),
mirroring 0002/0004. Also:
- agents.needs_reeval            — drift flag (FR-002); set when a bound tool/skill/KB trigger surface changes.
- knowledge_bases.archived_at    — soft-archive (FR-003); agents already have status='archived'.
- tools/mcp_servers/skills.archived_at — soft-archive.

Enums tool_kind / tool_risk_tier already exist (0004) — bind to them, DO NOT recreate.
Registry binding FKs stay deferred (0004 note): skill_bindings.skill_id / tool_bindings.tool_id keep NO FK
(M4a-created versions may reference ids that predate these registries). mcp_tools.server_id IS a real FK
(both tables are new). No secret columns are created (FR-021 — custody is M5/Vault).
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

_RLS_TABLES = ("tools", "mcp_servers", "mcp_tools", "skills", "workspace_settings", "members", "runs")


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
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO ac_app")


def upgrade() -> None:
    # --- drift + archive columns on existing tables ---
    op.execute("ALTER TABLE agents ADD COLUMN needs_reeval boolean NOT NULL DEFAULT false")
    op.execute("ALTER TABLE knowledge_bases ADD COLUMN archived_at timestamptz")

    # --- tools registry (RepoTool; data.ts:830). http_config is NON-SECRET only (FR-021). ---
    op.execute(
        """
        CREATE TABLE tools (
            id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL REFERENCES workspaces(id),
            name         text NOT NULL,
            description  text NOT NULL DEFAULT '',
            kind         tool_kind NOT NULL DEFAULT 'http',
            http_config  jsonb NOT NULL DEFAULT '{}',    -- endpoint/auth_mode/oauth_scopes; NO credential value
            risk_tier    tool_risk_tier NOT NULL DEFAULT 'read',
            archived_at  timestamptz,
            created_at   timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, name)
        )
        """
    )

    # --- mcp_servers + mcp_tools (McpServer/McpTool; data.ts:694/681) ---
    op.execute(
        """
        CREATE TABLE mcp_servers (
            id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL REFERENCES workspaces(id),
            name         text NOT NULL,
            url          text NOT NULL DEFAULT '',
            transport    text NOT NULL DEFAULT 'http',
            status       text NOT NULL DEFAULT 'disabled',  -- connected/error/disabled (no live probe here)
            archived_at  timestamptz,
            created_at   timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, name)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE mcp_tools (
            id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL REFERENCES workspaces(id),
            server_id    uuid NOT NULL REFERENCES mcp_servers(id),  -- both new → real FK
            name         text NOT NULL,
            description  text NOT NULL DEFAULT '',
            enabled      boolean NOT NULL DEFAULT true,
            risk_tier    tool_risk_tier NOT NULL DEFAULT 'read',
            UNIQUE (server_id, name)
        )
        """
    )

    # --- skills registry (Skill; data.ts:603). provenance = unsigned stub (FR-004); no signature column. ---
    op.execute(
        """
        CREATE TABLE skills (
            id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL REFERENCES workspaces(id),
            slug         text NOT NULL,
            name         text NOT NULL,
            description  text NOT NULL DEFAULT '',
            instructions text NOT NULL DEFAULT '',
            resources    jsonb NOT NULL DEFAULT '[]',
            content_hash text NOT NULL DEFAULT '',
            status       text NOT NULL DEFAULT 'draft',    -- draft/published
            provenance   jsonb,                            -- {by, at} unsigned stub; NULL until published
            archived_at  timestamptz,
            created_at   timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, slug)
        )
        """
    )

    # --- workspace_settings (1:1 with workspace; WorkspaceSettings/Limits/EvalPolicy data.ts:1055/1056/1064) ---
    op.execute(
        """
        CREATE TABLE workspace_settings (
            workspace_id     uuid PRIMARY KEY REFERENCES workspaces(id),
            default_provider text NOT NULL DEFAULT '',
            default_model    text NOT NULL DEFAULT '',
            limits           jsonb NOT NULL DEFAULT '{}',   -- monthlySpendUsd/maxConcurrent/requestTimeoutS
            eval_policy      jsonb NOT NULL DEFAULT '{}'    -- minCases/qualityThreshold/efficiencyBudgetPct/allowLowConfidenceOverride
        )
        """
    )

    # --- members (Member; data.ts:53). console_role STORED, not enforced here (FR-012). ---
    op.execute(
        """
        CREATE TABLE members (
            id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL REFERENCES workspaces(id),
            subject      text NOT NULL,
            display_name text NOT NULL DEFAULT '',
            email        text NOT NULL DEFAULT '',
            console_role text NOT NULL DEFAULT 'viewer',   -- viewer/builder/publisher
            created_at   timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, subject)
        )
        """
    )

    # --- runs (Run; data.ts:975). Identity + metrics ONLY — no raw prompt/response content (FR-013/SC-009). ---
    op.execute(
        """
        CREATE TABLE runs (
            id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id   uuid NOT NULL REFERENCES workspaces(id),
            agent_id       uuid,
            version_id     uuid,
            model          text NOT NULL DEFAULT '',
            profile_hash   text NOT NULL DEFAULT '',        -- the frozen profile's content hash (not an id)
            prompt_hash    text NOT NULL DEFAULT '',
            usage          jsonb NOT NULL DEFAULT '{}',    -- {input, output, total} tokens
            latency_ms     int  NOT NULL DEFAULT 0,
            cost_usd       numeric(12,6) NOT NULL DEFAULT 0,  -- from runtime RunRecord (our profiles), never transport
            outcome        text NOT NULL DEFAULT 'ok',     -- ok/error/refused/timeout
            source         text NOT NULL DEFAULT 'playground',  -- playground/published/eval
            correlation_id text NOT NULL DEFAULT '',
            created_at     timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute("CREATE INDEX ix_tools_workspace ON tools(workspace_id)")
    op.execute("CREATE INDEX ix_mcp_servers_workspace ON mcp_servers(workspace_id)")
    op.execute("CREATE INDEX ix_mcp_tools_server ON mcp_tools(server_id)")
    op.execute("CREATE INDEX ix_skills_workspace ON skills(workspace_id)")
    op.execute("CREATE INDEX ix_members_workspace ON members(workspace_id)")
    op.execute("CREATE INDEX ix_runs_workspace_created ON runs(workspace_id, created_at DESC)")

    for t in _RLS_TABLES:
        _enable_rls(t)


def downgrade() -> None:
    for t in _RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {t}_ws_isolation ON {t}")
    op.execute("DROP TABLE IF EXISTS runs CASCADE")
    op.execute("DROP TABLE IF EXISTS members CASCADE")
    op.execute("DROP TABLE IF EXISTS workspace_settings CASCADE")
    op.execute("DROP TABLE IF EXISTS skills CASCADE")
    op.execute("DROP TABLE IF EXISTS mcp_tools CASCADE")
    op.execute("DROP TABLE IF EXISTS mcp_servers CASCADE")
    op.execute("DROP TABLE IF EXISTS tools CASCADE")
    op.execute("ALTER TABLE knowledge_bases DROP COLUMN IF EXISTS archived_at")
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS needs_reeval")
