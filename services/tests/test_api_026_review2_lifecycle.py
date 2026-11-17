"""Feature 026 — second /code-review (US1+US2), lifecycle findings #1, #2, #7.

#1 publish ignored archive (an archived agent could be published by a publisher, bypassing the admin-only restore)
   and wrote no `agent.publish` audit row.
#2 PATCH gated only archive/restore/re-point: any other status transition (→published, published→draft,
   clearing eval_failing) went through on builder `agent.edit`, unaudited.
#7 the eval plane still ran an archived agent (spend on a retired agent; D4 says archived stops answering).
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, update

from api.db import tenant_session
from api.models import Agent, AuditLog, Run
from runtime.providers import gateway
from tests._api_helpers import client, dev_headers, seed_agent, seed_version, seed_workspace
from tests.test_api_evals_publish import _eval_body

pytestmark = pytest.mark.integration


async def _seed(status: str | None = None):
    ws = await seed_workspace()
    aid = await seed_agent(ws)
    vid = await seed_version(ws, aid)
    if status:
        async with tenant_session(ws) as s:
            await s.execute(update(Agent).where(Agent.id == uuid.UUID(aid))
                            .values(status=status, status_before_archive="draft" if status == "archived" else None))
    return ws, aid, vid


def _h(ws, role, etag=None):
    return dev_headers(workspace=ws, claims={ws: role}, if_match=etag)


async def _audit(ws, action=None):
    async with tenant_session(ws) as s:
        stmt = select(AuditLog)
        if action:
            stmt = stmt.where(AuditLog.action == action)
        return list((await s.scalars(stmt)).all())


async def _agent(ws, aid):
    async with tenant_session(ws) as s:
        return (await s.execute(select(Agent.status, Agent.active_version_id)
                                .where(Agent.id == uuid.UUID(aid)))).first()


# --- #1 publish ---------------------------------------------------------------------------------------------------
async def test_publish_refuses_an_archived_agent():
    ws, aid, vid = await _seed("archived")
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/publish", headers=_h(ws, "publisher"), json={"version_id": vid})
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "agent.archived"
    assert (await _agent(ws, aid)).status == "archived"
    assert await _audit(ws) == []


async def test_publish_writes_an_agent_publish_audit_row():
    ws, aid, vid = await _seed()
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/publish", headers=_h(ws, "publisher"), json={"version_id": vid})
    assert r.status_code == 200, r.text
    (row,) = await _audit(ws, "agent.publish")
    assert str(row.target_id) == aid and row.meta["version_id"] == vid and row.meta["prior_status"] == "draft"


# --- #2 PATCH status transitions ----------------------------------------------------------------------------------
async def _etag(c, ws, aid):
    return (await c.get(f"/api/v1/agents/{aid}", headers=_h(ws, "admin"))).headers["ETag"]


@pytest.mark.parametrize("start,to", [("draft", "published"), ("published", "draft"), ("eval_failing", "draft")])
async def test_builder_cannot_change_status_via_patch(start, to):
    ws, aid, _ = await _seed(start)
    async with client() as c:
        r = await c.patch(f"/api/v1/agents/{aid}", json={"status": to}, headers=_h(ws, "builder", await _etag(c, ws, aid)))
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "access.denied"
    assert (await _agent(ws, aid)).status == start


async def test_publisher_status_change_is_audited():
    ws, aid, _ = await _seed()
    async with client() as c:
        up = await c.patch(f"/api/v1/agents/{aid}", json={"status": "published"},
                           headers=_h(ws, "publisher", await _etag(c, ws, aid)))
        assert up.status_code == 200, up.text
        down = await c.patch(f"/api/v1/agents/{aid}", json={"status": "draft"},
                             headers=_h(ws, "publisher", up.headers["ETag"]))
        assert down.status_code == 200, down.text
    rows = await _audit(ws)
    assert sorted(r.action for r in rows) == ["agent.publish", "agent.status"]
    by = {r.action: r.meta for r in rows}
    assert by["agent.publish"]["prior_status"] == "draft"
    assert by["agent.status"] == {"from_status": "published", "to_status": "draft"}


async def test_builder_resending_current_status_is_a_noop():
    ws, aid, _ = await _seed()
    async with client() as c:
        r = await c.patch(f"/api/v1/agents/{aid}", json={"status": "draft", "name": "B"},
                          headers=_h(ws, "builder", await _etag(c, ws, aid)))
    assert r.status_code == 200, r.text


# --- #7 eval on archived ------------------------------------------------------------------------------------------
async def test_eval_run_refuses_an_archived_agent(monkeypatch):
    calls = []

    async def _never(*a, **k):
        calls.append(1)
        if False:
            yield None
    monkeypatch.setattr(gateway, "stream", _never)
    ws, aid, vid = await _seed("archived")
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/eval-runs", headers=_h(ws, "publisher"), json=_eval_body(vid))
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "agent.archived"
    assert calls == []
    async with tenant_session(ws) as s:
        assert list((await s.execute(select(Run))).scalars()) == []
