"""Database connection URLs + engines + the tenant-scoped session factory (09 §7).

- asyncpg for the app/runtime/tests (async everywhere on the request path — 00L §4).
- psycopg (sync) only for out-of-band Alembic migrations (00L §4.1).
- The APP path connects as the non-superuser `ac_app` role so RLS (0002) actually applies; the
  migration/owner role `ac` BYPASSES RLS and must never serve requests (09 §7, migration 0003).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.settings import get_settings
from api.tenancy import set_workspace_scope

# URLs come from the typed settings layer (single source; driver-validated fail-closed — api/settings.py).
_settings = get_settings()
# Superuser/owner (migrations + tooling). BYPASSES RLS — never use on the request path.
ASYNC_URL = _settings.database_url
SYNC_URL = _settings.database_sync_url
# Non-superuser app role — the request/job path. RLS applies. Dev password; prod via Vault (09).
APP_ASYNC_URL = _settings.app_database_url

# Lazy singletons so importing this module never opens a connection (keeps unit tests infra-free).
_app_engine = None
_app_sessionmaker: async_sessionmaker | None = None


def _app_connect_args() -> dict:
    """asyncpg connect args the app (`ac_app`) role needs because feature 021 (D4) routes it through PgBouncer in
    TRANSACTION pool mode. Txn-mode hands a pooled server backend to a different client between transactions, so
    asyncpg's default per-connection prepared-statement cache + fixed statement names (``__asyncpg_stmt_N__``)
    collide across clients on a reused backend → intermittent ``prepared statement "..." already exists / does not
    exist`` under concurrent load. Disable the cache and give every prepared statement a process-unique name so no
    two pooled clients can clash. Harmless on a direct connection (dev/tests), so the app path uses it always; the
    owner/migration engine (``ASYNC_URL``) connects DIRECT to Postgres and deliberately gets none of this."""
    return {
        "statement_cache_size": 0,                                   # no asyncpg server-side prepared-stmt cache
        "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4().hex}__",  # unique names → no cross-client clash
    }


def app_engine():
    global _app_engine
    if _app_engine is None:
        _app_engine = create_async_engine(APP_ASYNC_URL, poolclass=NullPool,
                                          connect_args=_app_connect_args())
    return _app_engine


def _app_sessions() -> async_sessionmaker:
    """Lazy singleton sessionmaker (stateless config bound to the app engine) — built once, not per request."""
    global _app_sessionmaker
    if _app_sessionmaker is None:
        _app_sessionmaker = async_sessionmaker(app_engine(), expire_on_commit=False)
    return _app_sessionmaker


@asynccontextmanager
async def auth_lookup_session() -> AsyncIterator[AsyncSession]:
    """Yield an UNSCOPED ``ac_app`` session for caller-token auth resolution ONLY (feature 018).

    Caller-token auth is inherently cross-tenant: the bearer carries no workspace, so the workspace must be
    DERIVED from the matching row before any tenant scope can be set (chicken-and-egg). This session has no
    ``app.workspace_id`` GUC — every RLS-protected table therefore returns ZERO rows through it (fail-closed).
    It is used solely to call the ``api_token_resolve(hash)`` SECURITY DEFINER function (migration 0008), which
    bypasses RLS as its owner but only ever returns the single row matching a globally-unique token_hash. The
    app role still never bypasses RLS directly. Do NOT use this session for tenant reads/writes — use
    ``tenant_session`` for those.
    """
    async with _app_sessions()() as session:
        async with session.begin():
            yield session


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
