"""Migration 0008 — api_tokens table + RLS + the api_token_resolve SECURITY DEFINER function (feature 018).

Asserts the schema the caller-token plane depends on exists after `alembic upgrade head`: the columns, RLS
(enabled + forced), the token_hash uniqueness, the expiry CHECK, and that the app role can EXECUTE the
cross-tenant auth resolver (proving the SECURITY DEFINER grant). Integration: needs the compose Postgres.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from api import db

pytestmark = pytest.mark.integration


async def _column(conn, table: str, column: str):
    return (await conn.execute(text(
        "select data_type, is_nullable from information_schema.columns "
        "where table_name = :t and column_name = :c"), {"t": table, "c": column})).first()


async def test_api_tokens_columns() -> None:
    async with db.app_engine().connect() as conn:
        th = await _column(conn, "api_tokens", "token_hash")
        assert th is not None and th.data_type == "text" and th.is_nullable == "NO"
        for col in ("prefix", "name"):
            c = await _column(conn, "api_tokens", col)
            assert c is not None and c.is_nullable == "NO", f"api_tokens.{col} missing/nullable"
        created = await _column(conn, "api_tokens", "created_at")
        assert created is not None and created.data_type == "timestamp with time zone"
        for nullable_col in ("expires_at", "revoked_at", "last_used_at"):
            c = await _column(conn, "api_tokens", nullable_col)
            assert c is not None and c.is_nullable == "YES", f"api_tokens.{nullable_col} should be nullable"


async def test_api_tokens_rls_enabled_and_forced() -> None:
    async with db.app_engine().connect() as conn:
        row = (await conn.execute(text(
            "select relrowsecurity, relforcerowsecurity from pg_class where relname = 'api_tokens'"))).first()
        assert row is not None and row.relrowsecurity and row.relforcerowsecurity


async def test_token_hash_is_unique() -> None:
    async with db.app_engine().connect() as conn:
        row = (await conn.execute(text(
            "select 1 from pg_constraint where conrelid = 'api_tokens'::regclass and contype = 'u'"))).first()
        assert row is not None, "api_tokens must have a UNIQUE constraint (token_hash)"


async def test_expiry_check_constraint_present() -> None:
    async with db.app_engine().connect() as conn:
        row = (await conn.execute(text(
            "select 1 from pg_constraint where conrelid = 'api_tokens'::regclass and contype = 'c'"))).first()
        assert row is not None, "api_tokens must have the expires_at > created_at CHECK"


async def test_app_role_can_execute_resolver_function() -> None:
    """ac_app (the request-path role) must be able to EXECUTE the SECURITY DEFINER resolver (an unknown hash
    simply returns no rows). A permission error here means the GRANT in 0008 regressed."""
    async with db.app_engine().connect() as conn:
        rows = (await conn.execute(
            text("select id from api_token_resolve(:h)"), {"h": "deadbeef-not-a-real-hash"})).all()
        assert rows == []
