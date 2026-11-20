"""0010 idp_binding + group_members — IdP group → members-store sync (feature 020)

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-13

M5 group-sync slice. Two additive, backfill-safe changes:

- `workspaces.idp_binding jsonb NOT NULL DEFAULT '{}'` (D1): the per-workspace map from AC role/team to a
  NATIVE IdP group reference — an Okta group name, an Entra object-GUID, or a SAML attribute value. No
  `ap-<prefix>-<role>` naming convention is assumed (FR-021); the existing `group_prefix varchar(64)` could
  express only the Okta prefix sugar. Default `'{}'` → every existing workspace keeps today's behavior (no
  sync until an admin configures a binding). The umbrella data-model wrongly called this "existing" — it did
  not exist on disk.

- `group_members` (D5): the notification fan-out projection — `source='login_seen'` (upserted on login) or
  `source='members_of'` (scheduled enumeration). CACHE-ONLY; it MUST NEVER be read to grant access (FR-023).
  WorkspaceScoped under RLS (enabled + forced + ws-isolation policy + grant to ac_app), mirroring 0005/0008
  (defense-in-depth, P5) — the umbrella's global `(group_ref, subject)` pk is superseded by the tenant-keyed
  `(workspace_id, group_ref, subject)`.

`members` is WRITTEN (upsert) by the sync but NOT altered — it already has UNIQUE (workspace_id, subject)
(0005), console_role (0005), teams (0006), RLS. No change to the authZ read path (membership.py, 013/008).
"""
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # D1 — IdP-agnostic native-ref binding per workspace (roles + teams). Backfill-safe default.
    op.execute("ALTER TABLE workspaces ADD COLUMN idp_binding jsonb NOT NULL DEFAULT '{}'::jsonb")

    # D5 — notification fan-out projection (cache only; never an authZ read — FR-023).
    op.execute(
        """
        CREATE TABLE group_members (
            workspace_id  uuid NOT NULL REFERENCES workspaces(id),
            group_ref     text NOT NULL,
            subject       text NOT NULL,
            source        text NOT NULL,                       -- 'login_seen' | 'members_of'
            seen_at       timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, group_ref, subject),
            CONSTRAINT ck_group_members_source CHECK (source IN ('login_seen', 'members_of'))
        )
        """
    )
    op.execute("CREATE INDEX ix_group_members_workspace ON group_members(workspace_id)")

    # WorkspaceScoped RLS, mirroring 0005/0008: enable + FORCE + ws-isolation policy keyed on the app GUC.
    op.execute("ALTER TABLE group_members ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE group_members FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY group_members_ws_isolation ON group_members
          USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
          WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
        """
    )
    # Projection is upsert-only (login/refresh); rows age via seen_at. No DELETE grant (mirrors 0008).
    op.execute("GRANT SELECT, INSERT, UPDATE ON group_members TO ac_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS group_members_ws_isolation ON group_members")
    op.execute("DROP TABLE IF EXISTS group_members CASCADE")
    op.execute("ALTER TABLE workspaces DROP COLUMN IF EXISTS idp_binding")
