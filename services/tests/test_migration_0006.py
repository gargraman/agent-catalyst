"""Migration 0006 — access_policy columns + member teams (feature 013).

Asserts the additive, backfill-safe columns exist with their ungoverned defaults after `alembic upgrade head`.
Integration: needs the compose Postgres.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from api import db

pytestmark = pytest.mark.integration

# table -> (column, must-appear-in-default)
_ACCESS_POLICY_TABLES = ["agents", "tools", "mcp_servers", "mcp_tools", "skills", "knowledge_bases"]


async def _column(conn, table: str, column: str):
    return (await conn.execute(text(
        "select data_type, column_default, is_nullable from information_schema.columns "
        "where table_name = :t and column_name = :c"), {"t": table, "c": column})).first()


async def test_member_teams_column_exists_with_empty_default() -> None:
    async with db.app_engine().connect() as conn:
        row = await _column(conn, "members", "teams")
        assert row is not None, "members.teams column is missing"
        assert row.data_type == "ARRAY"
        assert row.is_nullable == "NO"
        assert "{}" in (row.column_default or "")          # default empty array


async def test_access_policy_column_on_all_governed_families() -> None:
    async with db.app_engine().connect() as conn:
        for table in _ACCESS_POLICY_TABLES:
            row = await _column(conn, table, "access_policy")
            assert row is not None, f"{table}.access_policy column is missing"
            assert row.data_type == "jsonb"
            assert row.is_nullable == "NO"
            assert "workspace" in (row.column_default or ""), f"{table}.access_policy default not 'workspace'"
