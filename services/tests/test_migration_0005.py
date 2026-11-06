"""Migration 0005 — registry/admin/ops tables exist with RLS, + the drift/archive columns (feature 006).

RED until 0005 is written + applied. Verifies the schema M4-remainder CRUD depends on: the seven new
tenant tables, their RLS (enabled AND forced, like 0002/0004), and the two column additions
(agents.needs_reeval, knowledge_bases.archived_at). Connects via the ac_app integration engine (conftest);
skips cleanly when the DB is unreachable.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

_NEW_TABLES = (
    "tools", "mcp_servers", "mcp_tools", "skills", "workspace_settings", "members", "runs",
)
# workspace_settings is keyed by workspace_id (its PK); every other new table carries workspace_id too.
_WS_SCOPED = _NEW_TABLES


async def test_new_tables_exist(engine):
    async with engine.connect() as c:
        rows = await c.execute(
            text(
                "select table_name from information_schema.tables "
                "where table_schema='public' and table_name = any(:names)"
            ),
            {"names": list(_NEW_TABLES)},
        )
        present = {r[0] for r in rows}
    assert present == set(_NEW_TABLES), f"missing tables: {set(_NEW_TABLES) - present}"


async def test_new_tables_carry_workspace_id(engine):
    async with engine.connect() as c:
        for t in _WS_SCOPED:
            r = await c.execute(
                text(
                    "select 1 from information_schema.columns "
                    "where table_name=:t and column_name='workspace_id'"
                ),
                {"t": t},
            )
            assert r.first() is not None, f"{t} missing workspace_id"


async def test_new_tables_have_rls_enabled_and_forced(engine):
    async with engine.connect() as c:
        rows = await c.execute(
            text(
                "select relname, relrowsecurity, relforcerowsecurity from pg_class "
                "where relname = any(:names)"
            ),
            {"names": list(_NEW_TABLES)},
        )
        state = {r[0]: (r[1], r[2]) for r in rows}
    for t in _NEW_TABLES:
        assert state.get(t) == (True, True), f"{t} RLS not enabled+forced: {state.get(t)}"


async def test_ws_isolation_policy_present_on_each_table(engine):
    async with engine.connect() as c:
        rows = await c.execute(
            text("select tablename from pg_policies where tablename = any(:names)"),
            {"names": list(_NEW_TABLES)},
        )
        with_policy = {r[0] for r in rows}
    assert with_policy == set(_NEW_TABLES), f"tables without a policy: {set(_NEW_TABLES) - with_policy}"


async def test_drift_and_archive_columns_added(engine):
    async with engine.connect() as c:
        needs = await c.execute(text(
            "select 1 from information_schema.columns "
            "where table_name='agents' and column_name='needs_reeval'"))
        assert needs.first() is not None, "agents.needs_reeval missing"
        kb = await c.execute(text(
            "select 1 from information_schema.columns "
            "where table_name='knowledge_bases' and column_name='archived_at'"))
        assert kb.first() is not None, "knowledge_bases.archived_at missing"


async def test_archivable_tables_have_archived_at(engine):
    async with engine.connect() as c:
        for t in ("tools", "mcp_servers", "skills"):
            r = await c.execute(text(
                "select 1 from information_schema.columns "
                "where table_name=:t and column_name='archived_at'"), {"t": t})
            assert r.first() is not None, f"{t} missing archived_at"
