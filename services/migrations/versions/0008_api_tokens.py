"""0008 api_tokens — caller tokens for the published-invoke plane (feature 018)

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-12

M5 caller-token plane. Adds the `api_tokens` table: a non-reversible `token_hash` (sha256 of a one-time
secret) + a short display `prefix` + workspace/agent binding + optional expiry + a durable `revoked_at`
tombstone. The RAW secret is NEVER stored — it is returned exactly once at mint and only the hash+prefix
persist (FR-010/011). Mirrors docs/backend/01-data-model.md §4.7 and the 015 shared data-model.

Workspace-owned under RLS (enabled + forced + ws-isolation policy + grant to ac_app), mirroring 0007.
Revoke is an UPDATE to `revoked_at` (rows retained as the authoritative record / for audit) — so, like
provider_keys, NO DELETE grant. `token_hash` is UNIQUE (constant-time-ish lookup key at invoke). A CHECK
guards `expires_at > created_at` when an expiry is set.
"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE api_tokens (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id  uuid NOT NULL REFERENCES workspaces(id),
            agent_id      uuid NOT NULL REFERENCES agents(id),      -- one token → one published endpoint
            name          text NOT NULL,
            token_hash    text NOT NULL UNIQUE,                     -- sha256(secret); NEVER the raw secret
            prefix        text NOT NULL,                            -- short display prefix, safe to show
            scopes        jsonb NOT NULL DEFAULT '{}'::jsonb,       -- {"invoke": true, "teams": [...]}
            created_at    timestamptz NOT NULL DEFAULT now(),
            last_used_at  timestamptz,
            expires_at    timestamptz,
            revoked_at    timestamptz,
            CONSTRAINT ck_api_tokens_expiry_after_create
                CHECK (expires_at IS NULL OR expires_at > created_at)
        )
        """
    )
    op.execute("CREATE INDEX ix_api_tokens_workspace ON api_tokens(workspace_id)")
    # list-by-agent: WHERE workspace_id=? AND agent_id=?
    op.execute("CREATE INDEX ix_api_tokens_agent ON api_tokens(workspace_id, agent_id)")

    op.execute("ALTER TABLE api_tokens ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE api_tokens FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY api_tokens_ws_isolation ON api_tokens
          USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
          WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
        """
    )
    # No DELETE: revoke is a tombstone UPDATE (revoked_at); rows are retained as the authoritative record.
    op.execute("GRANT SELECT, INSERT, UPDATE ON api_tokens TO ac_app")

    # Caller-token auth is inherently CROSS-TENANT: the caller presents only a bearer, and the workspace must
    # be DERIVED from the matching row — which RLS hides until app.workspace_id is set (chicken-and-egg). The
    # app role must NOT bypass RLS broadly (09 §7), so we expose a narrow SECURITY DEFINER function: it runs as
    # the owner (bypasses RLS) but only ever returns the single row matching a token_hash, for auth resolution.
    # token_hash is globally UNIQUE, so this leaks nothing beyond "which tenant/endpoint does THIS exact secret
    # map to" — the irreducible minimum the auth plane needs. ac_app gets EXECUTE only.
    op.execute(
        """
        CREATE FUNCTION api_token_resolve(p_hash text)
        RETURNS TABLE (
            id uuid, workspace_id uuid, agent_id uuid,
            scopes jsonb, expires_at timestamptz, revoked_at timestamptz
        )
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT id, workspace_id, agent_id, scopes, expires_at, revoked_at
            FROM public.api_tokens
            WHERE token_hash = p_hash
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION api_token_resolve(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION api_token_resolve(text) TO ac_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS api_token_resolve(text)")
    op.execute("DROP POLICY IF EXISTS api_tokens_ws_isolation ON api_tokens")
    op.execute("DROP TABLE IF EXISTS api_tokens CASCADE")
