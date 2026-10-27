"""0002 RLS tenancy + agent_versions immutability (F15 fix)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-25

- RLS (09 §7): ENABLE + FORCE on every workspace-owned table; policy keys on the
  `app.workspace_id` GUC via current_setting(..., true) so an UNSET GUC yields NULL → zero rows
  (fail-closed). FORCE makes the policy apply even to the table owner.
- Immutability (F15 fix): the parent design used a RULE `DO INSTEAD NOTHING`, which silently drops
  a frozen-column UPDATE (the app believes it succeeded) and does nothing when snapshot_hash is
  left stale. This replaces it with a BEFORE UPDATE trigger that RAISES if any frozen column
  changed — loud, not silent — while allowing the mutable columns (publishable).
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# Tenant-owned tables that carry workspace_id and get the simple `workspace_id = app.workspace_id`
# RLS policy. NOTE (review #6): `workspaces` (the tenant-ROOT table) is deliberately NOT here — its
# row IS the tenant, so it can't use that policy (a workspace is created before any session is scoped
# to it; WITH CHECK on a generated id is a chicken-and-egg). Workspace visibility/mutation is a
# membership/claim-based access model handled at the app layer, and tightening ac_app's broad DML on
# workspaces + a workspaces-specific policy are M5 (security/tenancy) items. Until then, DB-level
# defense-in-depth does NOT cover the workspaces table — the app guard is the only control there.
_SCOPED_TABLES = ("agents", "agent_versions")


def upgrade() -> None:
    for t in _SCOPED_TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        # NULLIF(..., '') handles BOTH an unset GUC (NULL) and a reset custom-GUC placeholder
        # (empty string from a pooled connection) → NULL::uuid → no rows (fail-closed), instead of
        # an invalid-uuid cast error. Set the GUC per request/txn on the app path (09 §7).
        op.execute(
            f"""
            CREATE POLICY {t}_ws_isolation ON {t}
              USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
              WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
            """
        )

    # F15: raise on any frozen-column change; allow mutable columns (publishable, created_at).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION agent_versions_freeze() RETURNS trigger AS $$
        BEGIN
          IF ROW(NEW.system_prompt, NEW.prompt_hash, NEW.snapshot_hash, NEW.model_config,
                 NEW.guardrail_config, NEW.io_contract, NEW.memory_config, NEW.context_config,
                 NEW.profile_snapshot, NEW.label, NEW.agent_id, NEW.workspace_id)
             IS DISTINCT FROM
             ROW(OLD.system_prompt, OLD.prompt_hash, OLD.snapshot_hash, OLD.model_config,
                 OLD.guardrail_config, OLD.io_contract, OLD.memory_config, OLD.context_config,
                 OLD.profile_snapshot, OLD.label, OLD.agent_id, OLD.workspace_id)
          THEN
            RAISE EXCEPTION 'agent_versions is immutable: frozen columns cannot be updated (version %)', OLD.id
              USING ERRCODE = 'raise_exception';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER agent_versions_freeze BEFORE UPDATE ON agent_versions "
        "FOR EACH ROW EXECUTE FUNCTION agent_versions_freeze()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS agent_versions_freeze ON agent_versions")
    op.execute("DROP FUNCTION IF EXISTS agent_versions_freeze()")
    for t in _SCOPED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {t}_ws_isolation ON {t}")
        op.execute(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
