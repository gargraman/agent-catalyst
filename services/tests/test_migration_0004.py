"""Migration 0004 — binding tables + minimal knowledge_bases exist with RLS (feature 001).

RED until 0004 is written + applied. Verifies the schema the version snapshot depends on:
the four new tables, their tenant RLS (enabled AND forced, like 0002), and the two new enums.
Connects via the ac_app integration engine (conftest); skips cleanly when the DB is unreachable.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

_NEW_TABLES = ("knowledge_bases", "retrieval_bindings", "skill_bindings", "tool_bindings")
_NEW_ENUMS = ("tool_kind", "tool_risk_tier")


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
        for t in _NEW_TABLES:
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


async def test_new_enums_exist(engine):
    async with engine.connect() as c:
        rows = await c.execute(
            text("select typname from pg_type where typname = any(:names)"),
            {"names": list(_NEW_ENUMS)},
        )
        present = {r[0] for r in rows}
    assert present == set(_NEW_ENUMS), f"missing enums: {set(_NEW_ENUMS) - present}"
