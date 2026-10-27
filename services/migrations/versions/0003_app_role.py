"""0003 non-superuser app role (09 §7: ac_app has no BYPASSRLS so RLS actually applies)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-25

The migration/owner role is a superuser and BYPASSES RLS — so the runtime/app path must connect
as a distinct NON-superuser role (`ac_app`) for the 0002 policies to take effect. This mirrors the
09 design (ac_app, no BYPASSRLS, PgBouncer txn-mode). Dev-only password; prod injects via Vault.
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ac_app') THEN
            CREATE ROLE ac_app LOGIN PASSWORD 'ac_app_pw';  -- dev-only; prod via Vault (09)
          END IF;
        END $$;
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO ac_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ac_app")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ac_app"
    )


def downgrade() -> None:
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM ac_app"
    )
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public FROM ac_app")
    op.execute("REVOKE USAGE ON SCHEMA public FROM ac_app")
    # Role left in place (cluster-global; other DBs/envs may share it).
