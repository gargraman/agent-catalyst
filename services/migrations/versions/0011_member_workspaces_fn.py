"""0011 member_workspaces() — workspace discovery (self-scoped cross-tenant membership lookup)

The console must list the workspaces a signed-in user may enter BEFORE any workspace is selected (the
workspace switcher). That is inherently cross-tenant: `members` is RLS-scoped on `app.workspace_id` (0005) and
the app role must NOT bypass RLS broadly (09 §7); the real IdP token carries no groups (0006), so the members
store is the only source of a subject's workspaces. Mirror the `api_token_resolve` pattern (0008): a narrow
SECURITY DEFINER function that runs as the owner (so it can read across tenants) but only ever returns the rows
for the ONE subject passed in. It is called through the unscoped `auth_lookup_session`, so `ac_app` itself never
gains a broad RLS bypass — the function is the single, auditable definer. `ac_app` gets EXECUTE only.

Revision ID: 0011
Revises: 0010
"""
from __future__ import annotations

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION member_workspaces(p_subject text)
        RETURNS TABLE (id uuid, name text, group_prefix text, console_role text)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT w.id, w.name, w.group_prefix, m.console_role
            FROM public.members m
            JOIN public.workspaces w ON w.id = m.workspace_id
            WHERE m.subject = p_subject
            ORDER BY w.name
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION member_workspaces(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION member_workspaces(text) TO ac_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS member_workspaces(text)")
