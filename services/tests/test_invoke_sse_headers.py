"""Feature 026 US2 (T039 live finding) — the invoke SSE response must not be compressed or buffered by a proxy.

Live check L10 caught it: through the console's same-origin Next rewrite, the stream came back `Content-Encoding:
gzip` and NO frame reached the browser until the body ended (gzip holds bytes until its buffer flushes) — so a slow
answer looked frozen and a 30 s silent stream was cut with zero frames. `Cache-Control: no-transform` is the standard
signal compliant intermediaries (Next's bundled `compression`, CDNs) honour to leave the body alone; `X-Accel-Buffering:
no` does the same for nginx-style buffering. Both planes share one response builder, so both must carry them.
"""
from __future__ import annotations

import uuid

import pytest

from runtime.providers import gateway
from tests._api_helpers import client, dev_headers, seed_api_token, seed_workspace
from tests._invoke_helpers import stop_ev, text_ev, usage_ev

pytestmark = pytest.mark.integration

IO = {"input_schema": {"type": "object", "properties": {"message": {"type": "string"}}}, "output_format": "text"}


async def _stream(req, *, api_key, profile=None, timeout=None, **_resilience):
    for ev in (text_ev("ok"), usage_ev(), stop_ev()):
        yield ev


def _assert_unbufferable(r):
    assert r.status_code == 200, r.text
    cc = [p.strip() for p in r.headers.get("cache-control", "").split(",")]
    assert "no-transform" in cc and "no-cache" in cc, r.headers.get("cache-control")
    assert r.headers.get("x-accel-buffering") == "no"


async def test_console_and_published_invoke_streams_forbid_proxy_transform_and_buffering(monkeypatch):
    monkeypatch.setattr(gateway, "stream", _stream)
    ws = await seed_workspace()
    secret = "ac_live_" + uuid.uuid4().hex
    async with client() as c:
        a = (await c.post("/api/v1/agents", json={"name": "A", "provider": "openai", "model": "gpt-5-mini",
                                                  "system_prompt": "x", "io_contract": IO},
                          headers=dev_headers(workspace=ws))).json()
        await seed_api_token(ws, a["id"], secret)
        console = await c.post(f"/api/v1/agents/{a['id']}/invoke", headers=dev_headers(workspace=ws),
                               json={"inputs": {"message": "hi"}})
        published = await c.post(f"/v1/agents/{a['id']}/invoke", headers={"Authorization": f"Bearer {secret}"},
                                 json={"inputs": {"message": "hi"}})
    _assert_unbufferable(console)
    _assert_unbufferable(published)
