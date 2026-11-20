"""Migration 0010 — workspaces.idp_binding + group_members projection (feature 020).

Asserts the additive, backfill-safe column + the WorkspaceScoped RLS projection table exist after
`alembic upgrade head`. Integration: needs the compose Postgres.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from api import db

pytestmark = pytest.mark.integration


async def _column(conn, table: str, column: str):
    return (await conn.execute(text(
        "select data_type, column_default, is_nullable from information_schema.columns "
        "where table_name = :t and column_name = :c"), {"t": table, "c": column})).first()


async def test_idp_binding_column_exists_with_empty_default() -> None:
    async with db.app_engine().connect() as conn:
        row = await _column(conn, "workspaces", "idp_binding")
        assert row is not None, "workspaces.idp_binding column is missing"
        assert row.data_type == "jsonb"
        assert row.is_nullable == "NO"
        assert "{}" in (row.column_default or "")                 # default empty object


async def test_group_members_table_and_columns() -> None:
    async with db.app_engine().connect() as conn:
        tbl = (await conn.execute(text(
            "select count(*) from information_schema.tables where table_name = 'group_members'"))).scalar()
        assert tbl == 1, "group_members table is missing"
        for col, dtype in [("workspace_id", "uuid"), ("group_ref", "text"), ("subject", "text"),
                           ("source", "text"), ("seen_at", "timestamp with time zone")]:
            row = await _column(conn, "group_members", col)
            assert row is not None, f"group_members.{col} is missing"
            assert row.data_type == dtype, f"group_members.{col} type is {row.data_type}, expected {dtype}"


async def test_group_members_has_workspace_scoped_rls() -> None:
    async with db.app_engine().connect() as conn:
        forced = (await conn.execute(text(
            "select relforcerowsecurity from pg_class where relname = 'group_members'"))).scalar()
        assert forced is True, "group_members must FORCE row level security (D5)"
        policy = (await conn.execute(text(
            "select policyname from pg_policies where tablename = 'group_members'"))).scalar()
        assert policy == "group_members_ws_isolation", "group_members ws-isolation policy is missing"
