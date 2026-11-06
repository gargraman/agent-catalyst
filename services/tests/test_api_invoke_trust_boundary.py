"""US3 (feature 007): new runtime trust-boundary outcomes surface as typed SSE frames (no 500) + the Run
persists correction_turns. Integration (compose Postgres); gateway mocked (no live calls)."""
from __future__ import annotations

import uuid as _uuid

import pytest
from sqlalchemy import select

from api.db import tenant_session
from api.models import Run
from runtime.providers import gateway
from tests._api_helpers import client, dev_headers, seed_agent, seed_version, seed_workspace
from tests._invoke_helpers import JSON_IO, make_gateway, make_multi_turn_gateway, stop_ev, text_ev, usage_ev

pytestmark = pytest.mark.integration

VALID = '{"severity":"high","score":8}'
BAD = "{not json"


async def _seed_json_agent():
    ws = await seed_workspace()
    aid = await seed_agent(ws)
    await seed_version(ws, aid, io_contract=JSON_IO)
    return ws, aid


async def test_output_invalid_surfaces_typed_error_frame_not_500(monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev(BAD), usage_ev(), stop_ev()]))
    ws, aid = await _seed_json_agent()
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/invoke", headers=dev_headers(workspace=ws),
                         json={"inputs": {"message": "an alert fired"}})
    assert r.status_code == 200                                   # SSE transport ok (not a 500)
    body = r.text
    assert "event: error" in body and "output_invalid" in body    # typed error frame, scrubbed code
    assert "event: done" in body                                  # terminal present
    assert BAD not in body                                        # invalid content suppressed from the stream
    # the Run was persisted with the fail-closed outcome + a correction_turns count in usage
    async with tenant_session(ws) as s:
        rows = list((await s.execute(select(Run).where(Run.workspace_id == _uuid.UUID(ws)))).scalars().all())
    assert len(rows) == 1
    assert rows[0].outcome == "output_invalid"
    assert rows[0].usage.get("correction_turns") == 2            # default budget consumed


async def test_repaired_success_over_http_records_correction_turns(monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_multi_turn_gateway([
        [text_ev(BAD), usage_ev(), stop_ev()],
        [text_ev(VALID), usage_ev(), stop_ev()],
    ]))
    ws, aid = await _seed_json_agent()
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/invoke", headers=dev_headers(workspace=ws),
                         json={"inputs": {"message": "an alert fired"}})
    assert r.status_code == 200
    body = r.text
    assert "event: done" in body and "event: error" not in body  # clean success terminal
    # the validated JSON is streamed (as an escaped text_delta payload); the invalid turn is suppressed
    assert "severity" in body and "high" in body and BAD not in body
    async with tenant_session(ws) as s:
        row = (await s.execute(select(Run).where(Run.workspace_id == _uuid.UUID(ws)))).scalar_one()
    assert row.outcome == "success" and row.usage.get("correction_turns") == 1
