"""FR-026 — tenant GUC does NOT leak across pooled connections under transaction-mode pooling (021).

The tenant scope is set with ``set_config('app.workspace_id', :ws, is_local=true)`` — i.e. ``SET LOCAL`` —
INSIDE the request transaction (``tenancy.set_workspace_scope`` / ``db.tenant_session``). ``SET LOCAL`` is
transaction-scoped, so it is discarded at COMMIT/ROLLBACK and a connection handed to the next request (whether
by SQLAlchemy's pool or by PgBouncer in transaction mode) cannot carry one request's tenant into the next.

This file proves that invariant against a **real connection pool** (NOT ``NullPool`` — the request path uses
``NullPool`` precisely because PgBouncer owns pooling in the deployment, so a unit NullPool engine would never
exercise the shared-physical-connection reuse path this test exists to defend). ``pool_size=1, max_overflow=0``
forces two sequential requests onto the ONE physical connection, exactly the leak path.

The load-bearing assertion — ``current_setting('app.workspace_id', true)`` is EMPTY at the start of request B,
before B sets its own scope — holds for both the direct-pooled and the through-PgBouncer topology (PgBouncer
txn-mode may even reassign a different server backend per transaction; the GUC must not survive either way).

Topology:
- Default: a pooled engine straight to Postgres (``AC_DATABASE_URL`` / conftest default, ac_app role). Same
  physical backend reused → also asserts ``pg_backend_pid()`` equality (the reuse is real, not vacuous).
- Through PgBouncer: set ``AC_PGBOUNCER_URL`` to the txn-mode pooler (``deploy/compose.yaml``) to exercise the
  full deployment hop; the backend-pid equality assertion is skipped there (txn-mode may rotate backends).

Integration-tier: skips cleanly when neither the DB nor the pooler is reachable.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests._api_helpers import seed_agent, seed_workspace

pytestmark = pytest.mark.integration

# Through PgBouncer when AC_PGBOUNCER_URL is set (deploy stack, txn mode); else the app role straight to PG.
_THROUGH_PGBOUNCER = bool(os.environ.get("AC_PGBOUNCER_URL"))
_POOL_URL = os.environ.get("AC_PGBOUNCER_URL") or os.environ.get(
    "AC_DATABASE_URL", "postgresql+asyncpg://ac_app:ac_app_pw@localhost:5433/agent_catalyst"
)


@pytest.fixture
async def pooled_engine():
    """A real-pool (pool_size=1) async engine — forces two requests onto ONE physical connection, so the
    cross-request GUC-leak path is actually exercised (NullPool would open a fresh connection each time)."""
    eng = create_async_engine(_POOL_URL, pool_size=1, max_overflow=0)
    try:
        async with eng.connect() as c:
            await c.execute(text("select 1"))
    except Exception as e:  # noqa: BLE001 — pooler/DB unreachable → skip, not fail (unit run stays green)
        await eng.dispose()
        pytest.skip(f"pooled stack unavailable ({'pgbouncer' if _THROUGH_PGBOUNCER else 'postgres'}): {e}")
    yield eng
    await eng.dispose()


async def _scoped_request(engine, workspace_id: str):
    """Run one request transaction scoped to `workspace_id` on the pooled connection; return (scope_at_entry,
    backend_pid, own_agent_visible_count). `scope_at_entry` is the GUC value BEFORE this request sets it — the
    leak detector."""
    async with engine.begin() as conn:
        scope_at_entry = (
            await conn.execute(text("select current_setting('app.workspace_id', true)"))
        ).scalar_one()
        backend_pid = (await conn.execute(text("select pg_backend_pid()"))).scalar_one()
        # SET LOCAL via set_config — the exact mechanism tenancy.set_workspace_scope uses on the request path.
        await conn.execute(text("select set_config('app.workspace_id', :ws, true)"), {"ws": str(workspace_id)})
        return scope_at_entry, backend_pid


async def test_no_guc_leak_across_pooled_requests(pooled_engine):
    """Two tenants reusing one pooled physical connection → request B sees NO stale app.workspace_id from A,
    and B cannot read A's agent (0 cross-tenant reads)."""
    suffix = uuid.uuid4().hex[:8]
    ws_a = await seed_workspace(name=f"pool-A-{suffix}")
    ws_b = await seed_workspace(name=f"pool-B-{suffix}")
    agent_a = await seed_agent(ws_a, name=f"A-only-{suffix}")

    # Request A: scope ws_a (and confirm its own agent is visible within A's scope).
    scope_a_entry, pid_a = await _scoped_request(pooled_engine, ws_a)
    async with pooled_engine.begin() as conn:
        await conn.execute(text("select set_config('app.workspace_id', :ws, true)"), {"ws": ws_a})
        n_own = (
            await conn.execute(text("select count(*) from agents where id = :id"), {"id": agent_a})
        ).scalar_one()
    assert n_own == 1, "within its own scope, tenant A must see its agent (sanity: RLS isn't hiding everything)"

    # Request B on the SAME pooled connection, different tenant.
    scope_b_entry, pid_b = await _scoped_request(pooled_engine, ws_b)

    # 1. The leak detector: B's transaction started with an EMPTY scope — A's SET LOCAL did not survive COMMIT.
    assert scope_b_entry == "", (
        f"app.workspace_id leaked across pooled requests: expected '' at B's entry, got {scope_b_entry!r}"
    )

    # 2. Cross-tenant read is 0 under B's scope — B cannot see A's row.
    async with pooled_engine.begin() as conn:
        await conn.execute(text("select set_config('app.workspace_id', :ws, true)"), {"ws": ws_b})
        n_cross = (
            await conn.execute(text("select count(*) from agents where id = :id"), {"id": agent_a})
        ).scalar_one()
    assert n_cross == 0, "tenant B must not see tenant A's agent across a pooled connection (0 cross-tenant reads)"

    # 3. Reuse is real (not a vacuous test): straight-to-PG the same physical backend is reused. Through
    #    PgBouncer txn-mode the server backend may rotate per transaction, so skip the pid check there — the
    #    empty-scope assertion above already proves no-leak for that topology.
    if not _THROUGH_PGBOUNCER:
        assert pid_a == pid_b, "pool_size=1 must reuse the one physical connection (else the test is vacuous)"


async def test_stale_scope_is_not_inherited_when_request_forgets_to_set_it(pooled_engine):
    """Defense-in-depth: even a (hypothetical) request that never sets its scope must inherit no tenant from the
    prior pooled request — it sees an empty GUC → RLS NULLIF→NULL → zero rows (fail-closed), never A's data."""
    suffix = uuid.uuid4().hex[:8]
    ws_a = await seed_workspace(name=f"pool-stale-A-{suffix}")
    agent_a = await seed_agent(ws_a, name=f"A-stale-{suffix}")

    # Request A sets ws_a and commits.
    async with pooled_engine.begin() as conn:
        await conn.execute(text("select set_config('app.workspace_id', :ws, true)"), {"ws": ws_a})
        _ = (await conn.execute(text("select pg_backend_pid()"))).scalar_one()

    # Request B sets NO scope — must see an empty GUC and therefore zero rows (no inherited ws_a).
    async with pooled_engine.begin() as conn:
        scope_b = (
            await conn.execute(text("select current_setting('app.workspace_id', true)"))
        ).scalar_one()
        n = (
            await conn.execute(text("select count(*) from agents where id = :id"), {"id": agent_a})
        ).scalar_one()
    assert scope_b == "", f"a scope-less request inherited a stale tenant: {scope_b!r}"
    assert n == 0, "a scope-less pooled request must see zero rows (fail-closed), not the prior tenant's data"
