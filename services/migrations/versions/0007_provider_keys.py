"""0007 provider_keys — workspace-scoped provider-credential custody (feature 014)

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-11

M5 secret-custody slice. Adds the `provider_keys` table: masked display value + an OPAQUE backend reference
(`vault_path` — an AWS Secrets Manager ARN for the `aws` provider, a `local://…` handle for `local`) +
metadata. The RAW secret is NEVER stored here — it lives only in the pluggable secrets backend
(api/secrets/). Mirrors docs/backend/01-data-model.md §4.3 and frontend/src/lib/data.ts ProviderKey.

Workspace-owned under RLS (enabled + forced + ws-isolation policy + grant to ac_app), mirroring 0002/0005.
Revoke is an UPDATE to a `revoked` tombstone (rows retained for audit) — so, unlike the 0005 registries,
NO DELETE grant. A `(workspace_id, provider, status)` index serves resolve-at-invoke cheaply.

New PG-native enum `provider_key_status` created here; downgrade drops the table then the enum.
"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TYPE provider_key_status AS ENUM ('active','expiring','revoked')")
    op.execute(
        """
        CREATE TABLE provider_keys (
            id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL REFERENCES workspaces(id),
            provider     text NOT NULL,                       -- matches the agent's provider for resolution
            label        text NOT NULL,
            masked       text NOT NULL,                       -- server-computed head/tail; NEVER the raw secret
            vault_path   text NOT NULL,                       -- OPAQUE backend ref (ARN / local handle)
            region       text,
            status       provider_key_status NOT NULL DEFAULT 'active',
            created_at   timestamptz NOT NULL DEFAULT now(),
            rotated_at   timestamptz,
            UNIQUE (workspace_id, label)
        )
        """
    )
    op.execute("CREATE INDEX ix_provider_keys_workspace ON provider_keys(workspace_id)")
    # resolve-at-invoke: WHERE workspace_id=? AND provider=? AND status='active'
    op.execute("CREATE INDEX ix_provider_keys_resolution ON provider_keys(workspace_id, provider, status)")

    op.execute("ALTER TABLE provider_keys ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE provider_keys FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY provider_keys_ws_isolation ON provider_keys
          USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
          WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
        """
    )
    # No DELETE: revoke is a tombstone UPDATE; rows are retained for audit.
    op.execute("GRANT SELECT, INSERT, UPDATE ON provider_keys TO ac_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS provider_keys_ws_isolation ON provider_keys")
    op.execute("DROP TABLE IF EXISTS provider_keys CASCADE")
    op.execute("DROP TYPE IF EXISTS provider_key_status")
