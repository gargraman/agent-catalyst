"""US1 — tenant isolation at the data layer (feature 001, spec FR-002..005, SC-001/002).

RED until tenant_session connects as ac_app (0003) so RLS (0002/0004) applies. Exercises the real
tenant_session factory with raw SQL (US1 is about the session/RLS boundary, not the ORM).
Integration-only; skips when the DB is unreachable. Writes use unique names (no cleanup needed; dev DB).
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from api.db import tenant_session

pytestmark = pytest.mark.integration


async def _mk_ws(session, name: str):
    r = await session.execute(
        text("insert into workspaces (name) values (:n) returning id"), {"n": name}
    )
    return r.scalar_one()


async def _mk_agent(session, ws, name: str):
    r = await session.execute(
        text("insert into agents (workspace_id, name, status) values (:w, :n, 'draft') returning id"),
        {"w": str(ws), "n": name},
    )
    return r.scalar_one()


async def _two_tenants():
    """Create two workspaces + one agent in workspace A. Returns (ws_a, ws_b, agent_a)."""
    suffix = uuid.uuid4().hex[:8]
    # workspaces has no RLS (tenant root) — any scope works for its insert.
    async with tenant_session(uuid.uuid4()) as s:
        ws_a = await _mk_ws(s, f"A-{suffix}")
        ws_b = await _mk_ws(s, f"B-{suffix}")
    async with tenant_session(ws_a) as s:
        agent_a = await _mk_agent(s, ws_a, f"agent-A-{suffix}")
    return ws_a, ws_b, agent_a


async def test_connection_role_is_ac_app():
    async with tenant_session(uuid.uuid4()) as s:
        role = (await s.execute(text("select current_user"))).scalar_one()
    assert role == "ac_app", f"request path must run as ac_app (RLS applies), not {role!r}"


async def test_cross_tenant_read_invisible():
    ws_a, ws_b, agent_a = await _two_tenants()
    async with tenant_session(ws_b) as s:
        n = (await s.execute(text("select count(*) from agents where id = :id"), {"id": agent_a})).scalar_one()
    assert n == 0, "workspace B must not see workspace A's agent"


async def test_cross_tenant_update_and_delete_noop():
    ws_a, ws_b, agent_a = await _two_tenants()
    async with tenant_session(ws_b) as s:
        upd = await s.execute(text("update agents set name='hijacked' where id = :id"), {"id": agent_a})
        assert upd.rowcount == 0, "workspace B must not update workspace A's agent"
        dele = await s.execute(text("delete from agents where id = :id"), {"id": agent_a})
        assert dele.rowcount == 0, "workspace B must not delete workspace A's agent"


async def test_unset_scope_is_fail_closed():
    ws_a, ws_b, agent_a = await _two_tenants()
    # Empty/invalid scope → NULLIF→NULL → zero rows (fail-closed), not all rows.
    async with tenant_session("") as s:
        n = (await s.execute(text("select count(*) from agents where id = :id"), {"id": agent_a})).scalar_one()
    assert n == 0, "a session with no workspace scope must see zero rows (fail-closed)"


async def test_malformed_scope_is_fail_closed():
    """FR-004: an invalid (non-empty, non-uuid) scope must fail closed to zero rows, not 500 on a
    `::uuid` cast error."""
    ws_a, ws_b, agent_a = await _two_tenants()
    async with tenant_session("not-a-uuid") as s:
        n = (await s.execute(text("select count(*) from agents where id = :id"), {"id": agent_a})).scalar_one()
    assert n == 0, "a malformed workspace scope must see zero rows (fail-closed)"
