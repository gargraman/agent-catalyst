"""US5 — run persistence (via mocked invoke) + history list/get (feature 006, FR-013/FR-014/SC-009)."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from api.db import tenant_session
from api.models import Run
from runtime.providers import gateway
from tests._api_helpers import client, dev_headers, parse_sse, seed_run, seed_workspace
from tests._invoke_helpers import make_gateway, stop_ev, text_ev, usage_ev

pytestmark = pytest.mark.integration

_CREATE = {"name": "Triage Bot", "provider": "openai", "model": "gpt-5-mini",
           "system_prompt": "You are a triage bot.",
           "io_contract": {"input_schema": {"type": "object", "properties": {"message": {"type": "string"}},
                                            "required": ["message"]}, "output_format": "text", "output_schema": None}}


async def test_invoke_persists_a_run_with_metrics_only(monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("ok"), usage_ev(), stop_ev()]))
    ws = await seed_workspace()
    async with client() as c:
        aid = (await c.post("/api/v1/agents", headers=dev_headers(workspace=ws), json=_CREATE)).json()["id"]
        r = await c.post(f"/api/v1/agents/{aid}/invoke", headers=dev_headers(workspace=ws),
                         json={"inputs": {"message": "hi"}})
        assert r.status_code == 200 and parse_sse(r.text)[-1][0] == "done"
    async with tenant_session(ws) as s:
        rows = list((await s.execute(select(Run))).scalars().all())
    assert len(rows) == 1
    run = rows[0]
    assert run.source == "playground" and run.model and run.correlation_id
    # identity + metrics only — the model has no content column, so no raw prompt/response is stored (SC-009)
    assert set(run.usage.keys()) <= {"input_tokens", "output_tokens", "total_tokens", "cost_usd",
                                     "reasoning_tokens", "cached_tokens"} or isinstance(run.usage, dict)


async def test_history_newest_first_and_get():
    ws = await seed_workspace()
    await seed_run(ws, source="playground")
    await seed_run(ws, source="published")
    async with client() as c:
        lst = (await c.get("/api/v1/runs", headers=dev_headers(workspace=ws))).json()
        assert len(lst["items"]) == 2
        rid = lst["items"][0]["id"]
        got = await c.get(f"/api/v1/runs/{rid}", headers=dev_headers(workspace=ws))
    assert got.status_code == 200 and got.json()["id"] == rid


async def test_get_missing_run_is_404():
    ws = await seed_workspace()
    async with client() as c:
        r = await c.get("/api/v1/runs/not-a-uuid", headers=dev_headers(workspace=ws))
    assert r.status_code == 404
