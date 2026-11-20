"""T012 (feature 025, US1, integration) — `runs.retry_count` persisted and exposed as `RunOut.retries`
(contracts/api-and-data.md §2; migration 0014_runs_retry_count; FR-015 disclosure in the OpenAPI descriptions).

Needs Postgres migrated to head (`make up`). Transport seam faked — no live/paid provider call. The process-default
Guard is used (API wiring of a settings-built Guard lands in US2); its few backoff waits are ≤ 1.5 s real time.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select, text

from api.db import tenant_session
from api.main import app
from api.models import Run
from tests._api_helpers import client, dev_headers, parse_sse, seed_workspace
from tests._fault_transport import FaultScript, fail, ok

pytestmark = pytest.mark.integration

_CREATE = {"name": "Retry Bot", "provider": "openai", "model": "gpt-5-mini",
           "system_prompt": "You are a retry bot.",
           "io_contract": {"input_schema": {"type": "object", "properties": {"message": {"type": "string"}},
                                            "required": ["message"]}, "output_format": "text", "output_schema": None}}


async def test_runs_retry_count_column_shape(engine):
    async with engine.connect() as c:
        row = (await c.execute(text(
            "select data_type, is_nullable, column_default from information_schema.columns "
            "where table_schema='public' and table_name='runs' and column_name='retry_count'"))).first()
    assert row is not None, "runs.retry_count missing — migration 0014 not applied"
    data_type, is_nullable, default = row
    assert data_type == "integer" and is_nullable == "NO" and str(default).startswith("0")


async def _invoke_once(script_steps, monkeypatch) -> tuple[str, dict]:
    FaultScript(script_steps).install(monkeypatch)
    ws = await seed_workspace()
    async with client() as c:
        aid = (await c.post("/api/v1/agents", headers=dev_headers(workspace=ws), json=_CREATE)).json()["id"]
        r = await c.post(f"/api/v1/agents/{aid}/invoke", headers=dev_headers(workspace=ws),
                         json={"inputs": {"message": "hi"}})
        assert r.status_code == 200 and parse_sse(r.text)[-1][0] == "done"
        async with tenant_session(ws) as s:
            [run] = list((await s.execute(select(Run))).scalars().all())
        got = await c.get(f"/api/v1/runs/{run.id}", headers=dev_headers(workspace=ws))
    assert got.status_code == 200
    return ws, {"row": run, "body": got.json()}


async def test_playground_retries_persisted_and_returned(monkeypatch):
    _, out = await _invoke_once([fail(503), fail(503), ok("ok")], monkeypatch)
    assert out["row"].outcome == "success"
    assert out["row"].retry_count == 2
    assert out["body"]["retries"] == 2


async def test_clean_run_has_zero_retries(monkeypatch):
    _, out = await _invoke_once([ok("ok")], monkeypatch)
    assert out["row"].retry_count == 0
    assert out["body"]["retries"] == 0


def test_openapi_discloses_reported_usage_cost_basis():
    props = app.openapi()["components"]["schemas"]["RunOut"]["properties"]
    assert props["retries"]["type"] == "integer"
    assert "reported usage" in props["retries"].get("description", "").lower()
    assert "reported usage" in props["cost_usd"].get("description", "").lower()
