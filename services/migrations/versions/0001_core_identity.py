"""0001 core identity: workspaces, agents, agent_versions (no RLS/immutability yet)

Revision ID: 0001
Revises:
Create Date: 2026-10-25

Tables only — the RLS policies and the immutability trigger land in 0002, so the integration
tests can first observe their ABSENCE (RED) and then their effect (GREEN).
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TYPE agent_status AS ENUM ('draft','published','archived','eval_failing')")

    op.execute(
        """
        CREATE TABLE workspaces (
            id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            name         varchar(80) NOT NULL,
            group_prefix varchar(64),
            created_at   timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    # agents + agent_versions have a circular FK (active_version_id ⇄ agent_id), so the FKs are
    # added after both tables exist.
    op.execute(
        """
        CREATE TABLE agents (
            id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id      uuid NOT NULL REFERENCES workspaces(id),
            name              varchar(80) NOT NULL,
            status            agent_status NOT NULL DEFAULT 'draft',
            active_version_id uuid,
            created_at        timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE agent_versions (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            agent_id         uuid NOT NULL,
            workspace_id     uuid NOT NULL REFERENCES workspaces(id),
            label            varchar(40) NOT NULL,
            -- frozen, behavior-defining columns (immutability enforced in 0002) --
            system_prompt    text  NOT NULL,
            prompt_hash      text  NOT NULL,
            snapshot_hash    text  NOT NULL,
            model_config     jsonb NOT NULL,
            guardrail_config jsonb NOT NULL DEFAULT '{}',
            io_contract      jsonb NOT NULL DEFAULT '{}',
            memory_config    jsonb NOT NULL DEFAULT '{}',
            context_config   jsonb NOT NULL DEFAULT '{}',
            profile_snapshot jsonb NOT NULL DEFAULT '{}',
            -- mutable columns --
            publishable      boolean NOT NULL DEFAULT false,
            created_at       timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        "ALTER TABLE agents ADD CONSTRAINT fk_agents_active_version "
        "FOREIGN KEY (active_version_id) REFERENCES agent_versions(id)"
    )
    op.execute(
        "ALTER TABLE agent_versions ADD CONSTRAINT fk_agent_versions_agent "
        "FOREIGN KEY (agent_id) REFERENCES agents(id)"
    )
    op.execute("CREATE INDEX ix_agents_workspace ON agents(workspace_id)")
    op.execute("CREATE INDEX ix_agent_versions_agent ON agent_versions(agent_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agent_versions CASCADE")
    op.execute("DROP TABLE IF EXISTS agents CASCADE")
    op.execute("DROP TABLE IF EXISTS workspaces CASCADE")
    op.execute("DROP TYPE IF EXISTS agent_status")
