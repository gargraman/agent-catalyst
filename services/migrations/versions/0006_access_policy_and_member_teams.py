"""0006 per-resource access policy + member teams (feature 013)

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-09

Additive, backfill-safe, reversible. Enforcement layer (feature 013) needs:
- access_policy jsonb on the governed families that lack it (knowledge_bases already has it, 0004):
  agents, tools, mcp_servers, mcp_tools, skills. Default is the ungoverned {"visibility":"workspace"}, so
  every existing row keeps today's behavior (FR-001) — no backfill required.
- members.teams text[] — server-owned team membership (authZ), default empty array. Teams are half the
  access-policy contract (restricted = role AND team; executer = team alone); the real IdP token carries no
  groups, so teams live in the members store under RLS exactly as console_role does (feature 012, FR-016).

No new table, no RLS-policy change (columns inherit their table's existing RLS). Camel-case keys match
frontend/src/lib/access-policy.ts verbatim (FR-018).
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

_ACCESS_POLICY_TABLES = ("agents", "tools", "mcp_servers", "mcp_tools", "skills")
_WS_DEFAULT = """'{"visibility":"workspace"}'::jsonb"""


def upgrade() -> None:
    for table in _ACCESS_POLICY_TABLES:
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN access_policy jsonb NOT NULL DEFAULT {_WS_DEFAULT}"
        )
    op.execute("ALTER TABLE members ADD COLUMN teams text[] NOT NULL DEFAULT '{}'")


def downgrade() -> None:
    op.execute("ALTER TABLE members DROP COLUMN teams")
    for table in _ACCESS_POLICY_TABLES:
        op.execute(f"ALTER TABLE {table} DROP COLUMN access_policy")
