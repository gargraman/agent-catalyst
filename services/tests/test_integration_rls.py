"""M0 integration (RED→GREEN) — row-level-security tenant isolation (09 §7).

RED after migration 0001 (no RLS): a tenant sees every row; an unset GUC sees everything.
GREEN after migration 0002: cross-tenant rows are invisible, and an UNSET GUC is fail-closed (0 rows).
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.conftest import mk_agent, mk_ws, set_ws

pytestmark = pytest.mark.integration


async def test_rls_isolates_tenants(engine):
    async with engine.connect() as c:
        trans = await c.begin()
        try:
            ws1 = await mk_ws(c, "ws-one")
            ws2 = await mk_ws(c, "ws-two")
            await set_ws(c, ws1)
            a1 = await mk_agent(c, ws1, "a1")
            await set_ws(c, ws2)
            a2 = await mk_agent(c, ws2, "a2")

            await set_ws(c, ws1)
            seen = (await c.execute(text("select id from agents"))).scalars().all()
            assert a1 in seen and a2 not in seen, "ws1 must see only its own agents"

            await set_ws(c, ws2)
            seen = (await c.execute(text("select id from agents"))).scalars().all()
            assert a2 in seen and a1 not in seen, "ws2 must see only its own agents"
        finally:
            await trans.rollback()


async def test_unset_guc_is_fail_closed(engine):
    # Seed committed data under a tenant GUC...
    async with engine.connect() as c:
        t = await c.begin()
        ws = await mk_ws(c, "ws-unset")
        await set_ws(c, ws)
        aid = await mk_agent(c, ws, "a-unset")
        await t.commit()
    try:
        # ...then a fresh connection that NEVER sets the GUC must see zero rows (fail-closed).
        async with engine.connect() as c2:
            cnt = (
                await c2.execute(text("select count(*) from agents where id = :i"), {"i": str(aid)})
            ).scalar_one()
            assert cnt == 0, "an unset app.workspace_id must expose no rows"
    finally:
        async with engine.connect() as c3:
            t = await c3.begin()
            await set_ws(c3, ws)
            await c3.execute(text("delete from agents where id = :i"), {"i": str(aid)})
            await c3.execute(text("delete from workspaces where id = :i"), {"i": str(ws)})
            await t.commit()
