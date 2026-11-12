"""Migration 0009 — api_tokens.created_by column + sweep index (feature 018 hardening).

Asserts the schema the offboarding sweep depends on exists after `alembic upgrade head`: a nullable
`created_by text` column (pre-0009 rows have no minter) and the (workspace_id, created_by) index the
RLS-scoped `revoke-user {subject}` sweep reads. Integration: needs the compose Postgres.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from api import db

pytestmark = pytest.mark.integration


async def test_created_by_column_is_nullable_text() -> None:
    async with db.app_engine().connect() as conn:
        row = (await conn.execute(text(
            "select data_type, is_nullable from information_schema.columns "
            "where table_name = 'api_tokens' and column_name = 'created_by'"))).first()
        assert row is not None, "api_tokens.created_by missing"
        assert row.data_type == "text" and row.is_nullable == "YES"


async def test_sweep_index_present() -> None:
    async with db.app_engine().connect() as conn:
        row = (await conn.execute(text(
            "select 1 from pg_indexes where tablename = 'api_tokens' "
            "and indexname = 'ix_api_tokens_created_by'"))).first()
        assert row is not None, "ix_api_tokens_created_by (workspace_id, created_by) missing"
