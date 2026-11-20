"""Feature 026 (live finding, decision #6) — the run id a caller sees IS the durable run's id.

Live check L8 found the stream's `run_id` (minted by the runtime, also stamped on every call-log line) was never
stored: `runs.id` was a fresh server default, so a playground turn couldn't open its Runs row and support couldn't
trace a customer's response to its record. The persisted row now takes the RunRecord's own id.
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


async def _stream(req, *, api_key, profile=None, timeout=None, **_resilience):
    for ev in (text_ev("ok"), usage_ev(), stop_ev()):
        yield ev


def _ids(r) -> tuple[str, str]:
    frames = dict((n, d) for n, d in parse_sse(r.text) if n in ("run_start", "done"))
    return frames["run_start"]["run_id"], frames["done"]["run_id"]


async def test_streamed_run_id_is_the_persisted_runs_id_on_both_planes(monkeypatch):
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
        seen = []
        for r in (console, published):
            assert r.status_code == 200, r.text
            start_id, done_id = _ids(r)
            assert start_id == done_id
            seen.append(uuid.UUID(done_id))
        detail = await c.get(f"/api/v1/runs/{seen[0]}", headers=dev_headers(workspace=ws))
    assert detail.status_code == 200, detail.text                       # the turn can open its Runs row
    async with tenant_session(ws) as s:
        rows = set((await s.execute(select(Run.id).where(Run.agent_id == uuid.UUID(a["id"])))).scalars())
    assert rows == set(seen)
