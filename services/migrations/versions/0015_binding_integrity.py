"""0015 binding integrity — what an evaluation certified is exactly what runs (feature 025, US4; data-model §B1–§B6)

Closes audit gap 2 (2026-11-15): bindings pointed at catalog rows by bare uuid with no FK, could be edited after the
version committed, and `agents.active_version_id` could name another agent's version.

  (1) PREFLIGHT (FR-022) — `SET LOCAL row_security = off`, 8 rules; any violation ⇒ RuntimeError listing
      `<rule>=<count> ids=[…≤20]` (ids only, never prompt/document text). REFUSES, never repairs: the operator decides.
  (2) FK-target uniques on (workspace_id, id) / (id, agent_id).
  (3) Composite reference FKs, ON DELETE RESTRICT — composite so RI (which bypasses RLS, G8) also pins the WORKSPACE.
      `fk_agents_active_version` is recreated as (active_version_id, id) → agent_versions (id, agent_id),
      DEFERRABLE INITIALLY DEFERRED (agent ↔ version are created circularly in one transaction). It keeps the default
      NO ACTION delete rule: Postgres never defers RESTRICT, and versions have no delete path.
  (4) `ck_tool_bindings_one_target` — exactly one target per kind (subagent_config_id stays FK-less: no catalog).
  (5) Triggers: `bindings_freeze` (UPDATE/DELETE of any binding ⇒ 23001 `frozen_binding:`, F15 parity with
      `agent_versions_freeze`), `bindings_insert_window` (INSERT only while the parent version's own transaction is
      open — a version inserted under a SAVEPOINT has a subxact xmin and is rejected, research G5, pinned),
      `kb_chunks_dim_check` (23514 `chunk_dim_mismatch:` when the vector's dims ≠ the KB's declared dim).
TRUNCATE fires no row trigger, so test teardown is unaffected.

Revision ID: 0015
Revises: 0014
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

_BINDINGS = ("skill_bindings", "tool_bindings", "retrieval_bindings")

_ONE_TARGET = (
    "(kind IN ('http','builtin') AND tool_id IS NOT NULL AND mcp_tool_id IS NULL AND subagent_config_id IS NULL)"
    " OR (kind = 'mcp' AND mcp_tool_id IS NOT NULL AND tool_id IS NULL AND subagent_config_id IS NULL)"
    " OR (kind = 'subagent' AND subagent_config_id IS NOT NULL AND tool_id IS NULL AND mcp_tool_id IS NULL)"
)

# rule id → query returning the violating row ids (ids only — never text columns)
_PREFLIGHT: tuple[tuple[str, str], ...] = (
    ("skill_ref", "SELECT b.id FROM skill_bindings b WHERE NOT EXISTS "
                  "(SELECT 1 FROM skills s WHERE s.workspace_id = b.workspace_id AND s.id = b.skill_id)"),
    ("tool_ref", "SELECT b.id FROM tool_bindings b WHERE b.tool_id IS NOT NULL AND NOT EXISTS "
                 "(SELECT 1 FROM tools t WHERE t.workspace_id = b.workspace_id AND t.id = b.tool_id)"),
    ("mcp_tool_ref", "SELECT b.id FROM tool_bindings b WHERE b.mcp_tool_id IS NOT NULL AND NOT EXISTS "
                     "(SELECT 1 FROM mcp_tools m WHERE m.workspace_id = b.workspace_id AND m.id = b.mcp_tool_id)"),
    ("kb_ref_ws", "SELECT b.id FROM retrieval_bindings b WHERE NOT EXISTS "
                  "(SELECT 1 FROM knowledge_bases k WHERE k.workspace_id = b.workspace_id AND k.id = b.kb_id)"),
    ("chunk_orphan", "SELECT c.id FROM kb_chunks c WHERE NOT EXISTS "
                     "(SELECT 1 FROM knowledge_bases k WHERE k.workspace_id = c.workspace_id AND k.id = c.kb_id)"),
    ("chunk_dim", "SELECT c.id FROM kb_chunks c JOIN knowledge_bases k "
                  "ON k.workspace_id = c.workspace_id AND k.id = c.kb_id "
                  "WHERE vector_dims(c.embedding) <> k.embedding_dim"),
    ("tool_target", f"SELECT b.id FROM tool_bindings b WHERE NOT ({_ONE_TARGET})"),
    ("active_version_agent", "SELECT a.id FROM agents a JOIN agent_versions v ON v.id = a.active_version_id "
                             "WHERE v.agent_id <> a.id"),
)

_UNIQUES = (
    ("skills", "uq_skills_ws_id", "workspace_id, id"),
    ("tools", "uq_tools_ws_id", "workspace_id, id"),
    ("mcp_tools", "uq_mcp_tools_ws_id", "workspace_id, id"),
    ("knowledge_bases", "uq_kb_ws_id", "workspace_id, id"),
    ("agent_versions", "uq_agent_versions_id_agent", "id, agent_id"),
)

_FKS = (
    ("skill_bindings", "fk_skill_bindings_skill", "workspace_id, skill_id", "skills (workspace_id, id)"),
    ("tool_bindings", "fk_tool_bindings_tool", "workspace_id, tool_id", "tools (workspace_id, id)"),
    ("tool_bindings", "fk_tool_bindings_mcp_tool", "workspace_id, mcp_tool_id", "mcp_tools (workspace_id, id)"),
    ("retrieval_bindings", "fk_retrieval_bindings_kb_ws", "workspace_id, kb_id", "knowledge_bases (workspace_id, id)"),
    ("kb_chunks", "fk_kb_chunks_kb", "workspace_id, kb_id", "knowledge_bases (workspace_id, id)"),
)


def _preflight() -> None:
    bind = op.get_bind()
    bind.execute(text("SET LOCAL row_security = off"))     # a non-bypass role must fail LOUD, not see a filtered view
    found: list[str] = []
    for rule, sql in _PREFLIGHT:
        ids = [str(r[0]) for r in bind.execute(text(sql)).fetchall()]
        if ids:
            found.append(f"{rule}={len(ids)} ids=[{','.join(ids[:20])}]")
    if found:
        raise RuntimeError("0015 preflight refused: " + "; ".join(found)
                           + " — fix or remove these rows explicitly, then re-run (nothing was applied)")


def upgrade() -> None:
    _preflight()

    for table, name, cols in _UNIQUES:
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} UNIQUE ({cols})")

    for table, name, cols, target in _FKS:
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} FOREIGN KEY ({cols}) "
                   f"REFERENCES {target} ON DELETE RESTRICT")

    op.execute("ALTER TABLE agents DROP CONSTRAINT fk_agents_active_version")
    op.execute("ALTER TABLE agents ADD CONSTRAINT fk_agents_active_version FOREIGN KEY (active_version_id, id) "
               "REFERENCES agent_versions (id, agent_id) DEFERRABLE INITIALLY DEFERRED")

    op.execute(f"ALTER TABLE tool_bindings ADD CONSTRAINT ck_tool_bindings_one_target CHECK ({_ONE_TARGET})")

    op.execute(
        """
        CREATE OR REPLACE FUNCTION bindings_freeze() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'frozen_binding: % row % of version % is immutable', TG_TABLE_NAME, OLD.id, OLD.version_id
                USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION bindings_insert_window() RETURNS trigger AS $$
        DECLARE
            vx xid;
        BEGIN
            SELECT xmin INTO vx FROM agent_versions WHERE id = NEW.version_id;
            IF vx IS NULL OR NOT (vx = pg_current_xact_id()::xid) THEN
                RAISE EXCEPTION 'frozen_binding: % cannot be added to version % after it was committed',
                    TG_TABLE_NAME, NEW.version_id
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION kb_chunks_dim_check() RETURNS trigger AS $$
        DECLARE
            d int;
        BEGIN
            SELECT embedding_dim INTO d FROM knowledge_bases WHERE id = NEW.kb_id AND workspace_id = NEW.workspace_id;
            -- no visible KB ⇒ leave it to fk_kb_chunks_kb (RI) to refuse
            IF d IS NOT NULL AND vector_dims(NEW.embedding) <> d THEN
                RAISE EXCEPTION 'chunk_dim_mismatch: chunk has % dims, knowledge base % declares %',
                    vector_dims(NEW.embedding), NEW.kb_id, d
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in _BINDINGS:
        op.execute(f"CREATE TRIGGER {table}_freeze BEFORE UPDATE OR DELETE ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION bindings_freeze()")
        op.execute(f"CREATE TRIGGER {table}_insert_window BEFORE INSERT ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION bindings_insert_window()")
    op.execute("CREATE TRIGGER kb_chunks_dim_check BEFORE INSERT OR UPDATE OF embedding, kb_id ON kb_chunks "
               "FOR EACH ROW EXECUTE FUNCTION kb_chunks_dim_check()")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS kb_chunks_dim_check ON kb_chunks")
    for table in _BINDINGS:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_insert_window ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_freeze ON {table}")
    op.execute("DROP FUNCTION IF EXISTS kb_chunks_dim_check()")
    op.execute("DROP FUNCTION IF EXISTS bindings_insert_window()")
    op.execute("DROP FUNCTION IF EXISTS bindings_freeze()")

    op.execute("ALTER TABLE tool_bindings DROP CONSTRAINT IF EXISTS ck_tool_bindings_one_target")

    op.execute("ALTER TABLE agents DROP CONSTRAINT IF EXISTS fk_agents_active_version")
    op.execute("ALTER TABLE agents ADD CONSTRAINT fk_agents_active_version FOREIGN KEY (active_version_id) "
               "REFERENCES agent_versions (id)")                  # the pre-0015 (0001) shape

    for table, name, _cols, _target in reversed(_FKS):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
    for table, name, _cols in reversed(_UNIQUES):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
