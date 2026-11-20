"""M0 integration (RED→GREEN) — agent_versions immutability (F15 fix).

RED after 0001 (no trigger): a frozen-column UPDATE succeeds silently.
GREEN after 0002: a frozen-column UPDATE RAISES (loud, not a silent DO-INSTEAD-NOTHING drop),
while a mutable-column UPDATE (publishable) is still allowed.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.conftest import mk_agent, mk_version, mk_ws, set_ws

pytestmark = pytest.mark.integration


async def test_frozen_column_update_is_rejected(engine):
    async with engine.connect() as c:
        trans = await c.begin()
        try:
            ws = await mk_ws(c, "ws-imm")
            await set_ws(c, ws)
            ag = await mk_agent(c, ws, "ag")
            vid = await mk_version(c, ag, ws)

            with pytest.raises(Exception) as ei:
                await c.execute(
                    text("update agent_versions set system_prompt = 'HACKED' where id = :i"),
                    {"i": str(vid)},
                )
            assert "immutable" in str(ei.value).lower(), "freeze trigger must raise, not silently drop"
        finally:
            await trans.rollback()


async def test_frozen_update_does_not_persist(engine):
    """The rejected UPDATE must leave the frozen value untouched."""
    async with engine.connect() as c:
        # seed + commit
        t = await c.begin()
        ws = await mk_ws(c, "ws-imm2")
        await set_ws(c, ws)
        ag = await mk_agent(c, ws, "ag")
        vid = await mk_version(c, ag, ws)
        await t.commit()
    try:
        async with engine.connect() as c2:
            t = await c2.begin()
            await set_ws(c2, ws)
            with pytest.raises(Exception):
                await c2.execute(
                    text("update agent_versions set system_prompt = 'HACKED' where id = :i"),
                    {"i": str(vid)},
                )
            await t.rollback()
        async with engine.connect() as c3:
            t = await c3.begin()
            await set_ws(c3, ws)
            val = (
                await c3.execute(
                    text("select system_prompt from agent_versions where id = :i"), {"i": str(vid)}
                )
            ).scalar_one()
            assert val == "sys", "frozen value must be unchanged after a rejected update"
            await t.rollback()
    finally:
        async with engine.connect() as c4:
            t = await c4.begin()
            await set_ws(c4, ws)
            await c4.execute(text("delete from agent_versions where id = :i"), {"i": str(vid)})
            await c4.execute(text("delete from agents where id = :i"), {"i": str(ag)})
            await c4.execute(text("delete from workspaces where id = :i"), {"i": str(ws)})
            await t.commit()


async def test_mutable_column_update_is_allowed(engine):
    async with engine.connect() as c:
        trans = await c.begin()
        try:
            ws = await mk_ws(c, "ws-mut")
            await set_ws(c, ws)
            ag = await mk_agent(c, ws, "ag")
            vid = await mk_version(c, ag, ws)
            # publishable is mutable (the eval worker flips it) — must NOT raise.
            await c.execute(
                text("update agent_versions set publishable = true where id = :i"), {"i": str(vid)}
            )
            val = (
                await c.execute(
                    text("select publishable from agent_versions where id = :i"), {"i": str(vid)}
                )
            ).scalar_one()
            assert val is True
        finally:
            await trans.rollback()
