"""US1 — SSE invoke through the shared runtime (M4, feature 005). Integration (seeds a version) + mocked
gateway (no live call). Proves the API wraps runtime.invoke and re-encodes its events as SSE frames."""
from __future__ import annotations

import pytest

from runtime.providers import gateway
from tests._api_helpers import client, dev_headers, parse_sse, seed_agent, seed_version, seed_workspace
from tests._invoke_helpers import error_ev, make_gateway, stop_ev, text_ev, usage_ev

pytestmark = pytest.mark.integration


async def _seed():
    ws = await seed_workspace()
    aid = await seed_agent(ws)
    vid = await seed_version(ws, aid)
    return ws, aid, vid


async def test_invoke_streams_ordered_sse(monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("Hello "), text_ev("world"), usage_ev(), stop_ev()]))
    ws, aid, vid = await _seed()
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/invoke",
                         headers=dev_headers(workspace=ws), json={"inputs": {"message": "hi"}})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    names = [n for n, _ in parse_sse(r.text)]
    assert names[0] == "run_start"
    assert "text_delta" in names and "usage" in names
    assert names[-1] == "done"


async def test_runtime_error_becomes_error_frame_then_done(monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_gateway([error_ev({"type": "provider_error", "message": "boom"})]))
    ws, aid, vid = await _seed()
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/invoke",
                         headers=dev_headers(workspace=ws), json={"inputs": {"message": "hi"}})
    assert r.status_code == 200
    frames = parse_sse(r.text)
    names = [n for n, _ in frames]
    assert "error" in names and names[-1] == "done"
    err = next(d for n, d in frames if n == "error")
    assert err["code"] == "provider_error" and "boom" in err["message"]


async def test_invalid_input_rejected_without_provider_call(monkeypatch):
    async def _boom(*a, **k):
        raise AssertionError("gateway must not be called on invalid input")
        yield  # pragma: no cover
    monkeypatch.setattr(gateway, "stream", _boom)
    ws, aid, vid = await _seed()
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/invoke",
                         headers=dev_headers(workspace=ws), json={"inputs": {"wrong_field": 1}})
    assert r.status_code == 200
    frames = parse_sse(r.text)
    names = [n for n, _ in frames]
    assert "error" in names and names[-1] == "done"
    done = next(d for n, d in frames if n == "done")
    assert done["status"] == "invalid_input"            # runtime rejected; zero provider spend


async def test_unknown_version_is_404(monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("x"), usage_ev(), stop_ev()]))
    ws = await seed_workspace()
    aid = await seed_agent(ws)                           # agent with NO version
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/invoke",
                         headers=dev_headers(workspace=ws), json={"inputs": {"message": "hi"}})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
