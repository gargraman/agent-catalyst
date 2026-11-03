"""Database connection URLs + engines + the tenant-scoped session factory (09 §7).

- asyncpg for the app/runtime/tests (async everywhere on the request path — 00L §4).
- psycopg (sync) only for out-of-band Alembic migrations (00L §4.1).
- The APP path connects as the non-superuser `ac_app` role so RLS (0002) actually applies; the
  migration/owner role `ac` BYPASSES RLS and must never serve requests (09 §7, migration 0003).
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from uuid import UUID

from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.tenancy import set_workspace_scope

# Superuser/owner (migrations + tooling). BYPASSES RLS — never use on the request path.
ASYNC_URL = os.environ.get(
    "AC_DATABASE_URL", "postgresql+asyncpg://ac:ac_dev_pw@localhost:5433/agent_catalyst"
)
SYNC_URL = os.environ.get(
    "AC_DATABASE_SYNC_URL", "postgresql+psycopg://ac:ac_dev_pw@localhost:5433/agent_catalyst"
)
# Non-superuser app role — the request/job path. RLS applies. Dev password; prod via Vault (09).
APP_ASYNC_URL = os.environ.get(
    "AC_APP_DATABASE_URL", "postgresql+asyncpg://ac_app:ac_app_pw@localhost:5433/agent_catalyst"
)

# Lazy singletons so importing this module never opens a connection (keeps unit tests infra-free).
_app_engine = None
_app_sessionmaker: async_sessionmaker | None = None


def app_engine():
    global _app_engine
    if _app_engine is None:
        _app_engine = create_async_engine(APP_ASYNC_URL, poolclass=NullPool)
    return _app_engine


def _app_sessions() -> async_sessionmaker:
    """Lazy singleton sessionmaker (stateless config bound to the app engine) — built once, not per request."""
    global _app_sessionmaker
    if _app_sessionmaker is None:
        _app_sessionmaker = async_sessionmaker(app_engine(), expire_on_commit=False)
    return _app_sessionmaker


@asynccontextmanager
async def tenant_session(workspace_id: str | UUID) -> AsyncIterator[AsyncSession]:
    """Yield an AsyncSession bound to exactly one workspace for one transaction.

    Connects as the non-superuser ``ac_app`` role and sets app.workspace_id (via set_config,
    is_local=true) so RLS (0002/0004) filters every tenant-owned table; an unset/empty/invalid
    scope → zero rows (fail-closed). The transaction commits on clean exit, rolls back on error.
    """
    async with _app_sessions()() as session:
        async with session.begin():
            await set_workspace_scope(session, workspace_id)
            yield session
