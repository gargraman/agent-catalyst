"""Feature 026 (decision #7) — the console invoke is throttled by the `playground` bucket, not `invoke`.

US2 found `settings.rate_limits["playground"]` was dead config: the console route used `rate_limit("invoke")`, so an
interactive builder shared the (larger) published-API budget. The published plane stays on `invoke` (token_auth).
"""
from __future__ import annotations

import uuid

import fakeredis.aioredis
import pytest

from api import redis_client
from api.settings import get_settings
from runtime.providers import gateway
from tests._api_helpers import client, dev_headers, seed_api_token, seed_workspace
from tests._invoke_helpers import stop_ev, text_ev, usage_ev

pytestmark = pytest.mark.integration

IO = {"input_schema": {"type": "object", "properties": {"message": {"type": "string"}}}, "output_format": "text"}


async def _stream(req, *, api_key, profile=None, timeout=None, **_resilience):
    for ev in (text_ev("ok"), usage_ev(), stop_ev()):
        yield ev


async def test_console_invoke_uses_playground_bucket_published_stays_on_invoke(monkeypatch):
    monkeypatch.setattr(gateway, "stream", _stream)
    fr = fakeredis.aioredis.FakeRedis()
    monkeypatch.setattr(redis_client, "get_redis", lambda: fr)
    monkeypatch.setitem(get_settings().rate_limits, "playground", (60, 1))
    monkeypatch.setitem(get_settings().rate_limits, "invoke", (60, 100))
    ws = await seed_workspace()
    secret = "ac_live_" + uuid.uuid4().hex
    body = {"inputs": {"message": "hi"}}
    async with client() as c:
        a = (await c.post("/api/v1/agents", json={"name": "A", "provider": "openai", "model": "gpt-5-mini",
                                                  "system_prompt": "x", "io_contract": IO},
                          headers=dev_headers(workspace=ws))).json()
        await seed_api_token(ws, a["id"], secret)
        url = f"/api/v1/agents/{a['id']}/invoke"
        first = await c.post(url, headers=dev_headers(workspace=ws), json=body)
        second = await c.post(url, headers=dev_headers(workspace=ws), json=body)
        pub = [await c.post(f"/v1/agents/{a['id']}/invoke", headers={"Authorization": f"Bearer {secret}"}, json=body)
               for _ in range(2)]
    assert first.status_code == 200, first.text
    assert second.status_code == 429, second.text                       # playground budget (1) exhausted
    assert second.json()["error"]["code"] == "rate.limited"
    assert [r.status_code for r in pub] == [200, 200]                   # published plane unaffected (invoke=100)
