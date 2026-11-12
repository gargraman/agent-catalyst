"""0009 api_tokens.created_by — attribute a caller token to the console user that minted it (feature 018)

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-12

Post-018 hardening. Adds a nullable `created_by` column recording the minting principal's subject so an admin
force-logout of a user (`POST /org/revoke-user {subject}`) can durably revoke every caller token that user
minted in the workspace — not just cut their console session. Nullable because pre-0009 rows have no minter on
record. Indexed by (workspace_id, created_by) for the RLS-scoped offboarding sweep.
"""
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE api_tokens ADD COLUMN created_by text")
    # offboarding sweep: WHERE workspace_id=? AND created_by=? AND revoked_at IS NULL
    op.execute("CREATE INDEX ix_api_tokens_created_by ON api_tokens(workspace_id, created_by)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_api_tokens_created_by")
    op.execute("ALTER TABLE api_tokens DROP COLUMN IF EXISTS created_by")
