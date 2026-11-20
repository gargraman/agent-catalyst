"""US1 + US2 (feature 008): per-role allow/deny matrix for server-side RBAC enforcement.

RED against today's membership-only API (every role allowed); GREEN once routes declare require_permission.
Gateway mocked for the invoke case (no live call). Integration (compose Postgres; RLS still applies)."""
from __future__ import annotations

import uuid as _uuid

import pytest
from sqlalchemy import select

from api.db import tenant_session
from api.models import Agent
from runtime.providers import gateway
from tests._api_helpers import client, dev_headers, seed_agent, seed_version, seed_workspace
from tests._invoke_helpers import make_gateway, stop_ev, text_ev, usage_ev

pytestmark = pytest.mark.integration

_CREATE = {"name": "RBAC Bot", "provider": "openai", "model": "gpt-5-mini",
           "system_prompt": "x",
           "io_contract": {"input_schema": {"type": "object", "properties": {"message": {"type": "string"}},
                                            "required": ["message"]}, "output_format": "text", "output_schema": None}}


def _h(ws, role):
    return dev_headers(workspace=ws, claims={ws: role})


async def _seed():
    ws = await seed_workspace()
    aid = await seed_agent(ws)
    vid = await seed_version(ws, aid)
    return ws, aid, vid


# ---- US1: privileged operations gated by role -------------------------------------------------

async def test_viewer_cannot_create_agent(monkeypatch):
    ws, _, _ = await _seed()
    async with client() as c:
        r = await c.post("/api/v1/agents", headers=_h(ws, "viewer"), json=_CREATE)
    assert r.status_code == 403
    body = r.json()
    assert body["error"]["code"] == "access.denied"                       # A1: typed envelope
    assert body["error"]["detail"]["required_permission"] == "agent.create"
    # no row created
    async with tenant_session(ws) as s:
        n = len(list((await s.execute(select(Agent).where(Agent.name == "RBAC Bot"))).scalars().all()))
    assert n == 0


async def test_builder_cannot_publish_or_manage_admin_plane(monkeypatch):
    ws, aid, vid = await _seed()
    async with client() as c:
        pub = await c.post(f"/api/v1/agents/{aid}/publish", headers=_h(ws, "builder"), json={"version_id": vid})
        mem = await c.post("/api/v1/members", headers=_h(ws, "builder"), json={"subject": "x"})
        sett = await c.patch("/api/v1/workspace/settings", headers=_h(ws, "builder"), json={"default_model": "x"})
    assert pub.status_code == 403 and pub.json()["error"]["detail"]["required_permission"] == "agent.publish"
    assert mem.status_code == 403 and sett.status_code == 403


async def test_publisher_can_publish_but_not_manage_members(monkeypatch):
    ws, aid, vid = await _seed()
    async with client() as c:
        pub = await c.post(f"/api/v1/agents/{aid}/publish", headers=_h(ws, "publisher"), json={"version_id": vid})
        mem = await c.post("/api/v1/members", headers=_h(ws, "publisher"), json={"subject": "x"})
    assert pub.status_code == 200          # advisory publish authorized for publisher
    assert mem.status_code == 403 and mem.json()["error"]["detail"]["required_permission"] == "member.manage"


async def test_admin_can_manage_members(monkeypatch):
    ws, _, _ = await _seed()
    async with client() as c:
        mem = await c.post("/api/v1/members", headers=_h(ws, "admin"), json={"subject": "alice"})
    assert mem.status_code == 201


async def test_builder_can_create_agent(monkeypatch):
    ws, _, _ = await _seed()
    async with client() as c:
        r = await c.post("/api/v1/agents", headers=_h(ws, "builder"), json=_CREATE)
    assert r.status_code == 201


async def test_under_privileged_caller_gets_403_before_404(monkeypatch):
    """A2/FR-015: a viewer PATCHing a NON-existent agent gets 403, not 404 — role check precedes resource load."""
    ws, _, _ = await _seed()
    missing = str(_uuid.uuid4())
    async with client() as c:
        r = await c.patch(f"/api/v1/agents/{missing}", headers=_h(ws, "viewer"), json={"name": "x"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "access.denied"


# ---- US2: least-privilege reads; unknown role fail-closed ------------------------------------

async def test_viewer_can_read_and_use_playground(monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("hi"), usage_ev(), stop_ev()]))
    ws, aid, _ = await _seed()
    async with client() as c:
        lst = await c.get("/api/v1/agents", headers=_h(ws, "viewer"))
        runs = await c.get("/api/v1/runs", headers=_h(ws, "viewer"))
        metrics = await c.get("/api/v1/metrics/dashboard", headers=_h(ws, "viewer"))
        inv = await c.post(f"/api/v1/agents/{aid}/invoke", headers=_h(ws, "viewer"),
                           json={"inputs": {"message": "hi"}})
    assert lst.status_code == 200 and runs.status_code == 200 and metrics.status_code == 200
    assert inv.status_code == 200                                         # playground.use granted to viewer


async def test_unknown_role_denied_reads_and_writes(monkeypatch):
    """SC-003: an unrecognized role has zero permissions — denied everything, incl. reads (fail-closed)."""
    ws, _, _ = await _seed()
    async with client() as c:
        rd = await c.get("/api/v1/agents", headers=_h(ws, "superadmin"))
        wr = await c.post("/api/v1/agents", headers=_h(ws, "superadmin"), json=_CREATE)
    assert rd.status_code == 403 and wr.status_code == 403


async def test_valid_role_unheld_workspace_still_403(monkeypatch):
    ws, _, _ = await _seed()
    other = str(_uuid.uuid4())
    async with client() as c:               # admin on a DIFFERENT ws, selecting ws → membership 403
        r = await c.get("/api/v1/agents", headers=dev_headers(workspace=ws, claims={other: "admin"}))
    assert r.status_code == 403


async def test_invoke_denied_before_stream(monkeypatch):
    """A3: a denied invoke returns a plain JSON 403 BEFORE any SSE stream begins (not text/event-stream)."""
    ws, aid, _ = await _seed()
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/invoke", headers=_h(ws, "superadmin"),
                         json={"inputs": {"message": "hi"}})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "access.denied"
    assert "text/event-stream" not in r.headers.get("content-type", "")
