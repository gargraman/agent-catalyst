"""Feature 026 US2 (T032, research D4 / G10) — an archived agent stops answering.

- Console invoke → 409 `agent.archived` (the console knows the agent; it can offer "restore").
- Published invoke with a VALID token → 404 `not_found` (a caller can't tell archived from absent — P5).
- Both are refused before any provider work, and no run row is written.
- After a restore, both planes answer again.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from api.db import tenant_session
from api.models import Run
from runtime.providers import gateway
from tests._api_helpers import client, dev_headers, parse_sse, seed_api_token, seed_workspace
from tests._invoke_helpers import stop_ev, text_ev, usage_ev

pytestmark = pytest.mark.integration

IO = {"input_schema": {"type": "object", "properties": {"message": {"type": "string"}}}, "output_format": "text"}
INPUTS = {"message": "hi"}


def _counting_gateway():
    calls = {"n": 0}

    async def _stream(req, *, api_key, profile=None, timeout=None, **_resilience):
        calls["n"] += 1
        for ev in (text_ev("ok"), usage_ev(), stop_ev()):
            yield ev
    return _stream, calls


async def _archived_agent_with_token(c, ws) -> tuple[dict, str, str]:
    r = await c.post("/api/v1/agents", json={"name": "A", "provider": "openai", "model": "gpt-5-mini",
                                             "system_prompt": "You are helpful.", "io_contract": IO},
                     headers=dev_headers(workspace=ws))
    assert r.status_code == 201, r.text
    secret = "ac_live_arch_" + uuid.uuid4().hex[:10]
    await seed_api_token(ws, r.json()["id"], secret=secret)
    p = await c.patch(f"/api/v1/agents/{r.json()['id']}", json={"status": "archived"},
                      headers=dev_headers(workspace=ws, if_match=r.headers["ETag"]))
    assert p.status_code == 200, p.text
    return p.json(), p.headers["ETag"], secret


async def _run_count(ws, aid) -> int:
    async with tenant_session(ws) as s:
        return len((await s.execute(select(Run.id).where(Run.agent_id == uuid.UUID(aid)))).all())


async def test_archived_agent_console_invoke_is_409_agent_archived(monkeypatch):
    stream, calls = _counting_gateway()
    monkeypatch.setattr(gateway, "stream", stream)
    ws = await seed_workspace()
    async with client() as c:
        a, _, _ = await _archived_agent_with_token(c, ws)
        r = await c.post(f"/api/v1/agents/{a['id']}/invoke", headers=dev_headers(workspace=ws), json={"inputs": INPUTS})
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "agent.archived"
    assert calls["n"] == 0 and await _run_count(ws, a["id"]) == 0


@pytest.mark.parametrize("target", ["draft", "active"])
async def test_archived_agent_console_invoke_refuses_every_target(monkeypatch, target):
    stream, calls = _counting_gateway()
    monkeypatch.setattr(gateway, "stream", stream)
    ws = await seed_workspace()
    async with client() as c:
        a, _, _ = await _archived_agent_with_token(c, ws)
        r = await c.post(f"/api/v1/agents/{a['id']}/invoke", headers=dev_headers(workspace=ws),
                         json={"inputs": INPUTS, "target": target})
    assert r.status_code == 409 and r.json()["error"]["code"] == "agent.archived"
    assert calls["n"] == 0


async def test_archived_agent_published_invoke_is_404_not_found(monkeypatch):
    stream, calls = _counting_gateway()
    monkeypatch.setattr(gateway, "stream", stream)
    ws = await seed_workspace()
    async with client() as c:
        a, _, secret = await _archived_agent_with_token(c, ws)
        r = await c.post(f"/v1/agents/{a['id']}/invoke", headers={"Authorization": f"Bearer {secret}"},
                         json={"inputs": INPUTS})
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "not_found"                      # indistinguishable from an absent agent
    assert calls["n"] == 0 and await _run_count(ws, a["id"]) == 0


async def test_restored_agent_answers_on_both_planes(monkeypatch):
    stream, calls = _counting_gateway()
    monkeypatch.setattr(gateway, "stream", stream)
    ws = await seed_workspace()
    async with client() as c:
        a, etag, secret = await _archived_agent_with_token(c, ws)
        rs = await c.patch(f"/api/v1/agents/{a['id']}", json={"status": a["restore_status"]},
                           headers=dev_headers(workspace=ws, if_match=etag))
        assert rs.status_code == 200, rs.text
        console = await c.post(f"/api/v1/agents/{a['id']}/invoke", headers=dev_headers(workspace=ws),
                               json={"inputs": INPUTS})
        published = await c.post(f"/v1/agents/{a['id']}/invoke", headers={"Authorization": f"Bearer {secret}"},
                                 json={"inputs": INPUTS})
    for r in (console, published):
        assert r.status_code == 200, r.text
        name, done = parse_sse(r.text)[-1]
        assert name == "done" and done["status"] == "success"
    assert calls["n"] == 2 and await _run_count(ws, a["id"]) == 2
