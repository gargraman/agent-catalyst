"""Feature 010 (M6) — US1: inbound correlation id honored on invoke + eval and echoed back (T004).

Integration (compose Postgres); gateway mocked. RED until the handlers ingest the inbound id (today they
overwrite it with a fresh uuid).
"""
from __future__ import annotations

import re
import uuid as _uuid

import pytest
from sqlalchemy import select

from api.db import tenant_session
from api.models import Run
from runtime.providers import gateway
from tests._api_helpers import client, dev_headers, parse_sse, seed_agent, seed_version, seed_workspace
from tests._evals_helpers import VALID_OUTPUT, make_routed_gateway
from tests._invoke_helpers import make_gateway, stop_ev, text_ev, usage_ev

pytestmark = pytest.mark.integration

_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_TP = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"


async def _seed():
    ws = await seed_workspace()
    aid = await seed_agent(ws)
    vid = await seed_version(ws, aid)
    return ws, aid, vid


async def _runs(ws: str):
    async with tenant_session(ws) as s:
        return list((await s.execute(select(Run).where(Run.workspace_id == _uuid.UUID(ws)))).scalars().all())


async def _invoke(aid, ws, extra_headers=None):
    headers = dev_headers(workspace=ws)
    if extra_headers:
        headers.update(extra_headers)
    async with client() as c:
        return await c.post(f"/api/v1/agents/{aid}/invoke", headers=headers,
                            json={"inputs": {"message": "x"}})


async def test_invoke_honors_x_correlation_id_and_echoes_it(monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("ok"), usage_ev(), stop_ev()]))
    ws, aid, _ = await _seed()
    r = await _invoke(aid, ws, {"X-Correlation-Id": "my-corr-123"})
    assert r.status_code == 200
    assert r.headers.get("x-correlation-id") == "my-corr-123"
    rows = await _runs(ws)
    assert len(rows) == 1 and rows[0].correlation_id == "my-corr-123"


async def test_run_start_and_done_report_the_same_run_id(monkeypatch):
    """Code-review fix: the SSE run_start and done frames must name the SAME run id (previously run_start
    reported the correlation id and done the run id — a mismatch). Correlation stays a separate concept."""
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("ok"), usage_ev(), stop_ev()]))
    ws, aid, _ = await _seed()
    r = await _invoke(aid, ws, {"X-Correlation-Id": "corr-xyz"})
    assert r.status_code == 200
    frames = parse_sse(r.text)
    run_start = next(d for e, d in frames if e == "run_start")
    done = next(d for e, d in frames if e == "done")
    assert run_start["run_id"] == done["run_id"]          # one run id, consistent across frames
    assert run_start["run_id"] != "corr-xyz"              # run id is NOT the correlation id (separate concepts)
    assert r.headers.get("x-correlation-id") == "corr-xyz"


async def test_invoke_honors_traceparent_trace_id(monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("ok"), usage_ev(), stop_ev()]))
    ws, aid, _ = await _seed()
    r = await _invoke(aid, ws, {"traceparent": _TP})
    assert r.status_code == 200
    assert r.headers.get("x-correlation-id") == _TRACE_ID
    rows = await _runs(ws)
    assert rows[0].correlation_id == _TRACE_ID


async def test_invoke_generates_when_absent_and_echoes(monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("ok"), usage_ev(), stop_ev()]))
    ws, aid, _ = await _seed()
    r = await _invoke(aid, ws)
    assert r.status_code == 200
    echoed = r.headers.get("x-correlation-id")
    assert echoed and _HEX32.match(echoed)
    rows = await _runs(ws)
    assert rows[0].correlation_id == echoed


async def test_invoke_ignores_malformed_correlation_id(monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("ok"), usage_ev(), stop_ev()]))
    ws, aid, _ = await _seed()
    r = await _invoke(aid, ws, {"X-Correlation-Id": "bad id!! spaces"})
    assert r.status_code == 200
    rows = await _runs(ws)
    assert rows[0].correlation_id != "bad id!! spaces"
    assert _HEX32.match(rows[0].correlation_id)        # generated fallback


def _eval_body(vid: str, n: int = 3):
    import json
    cases = [{"id": f"c{i}", "input": "an XDR alert fired",
              "rubric": [{"name": "accuracy", "weight": 1.0}]} for i in range(n)]
    baseline = {"quality": 90.0, "cost_usd": 0.003, "latency_ms": 100,
                "per_case": {f"c{i}": json.dumps(VALID_OUTPUT) for i in range(n)}}
    return {"version_id": vid, "cases": cases, "baseline": baseline,
            "policy": {"min_cases": 3, "quality_threshold": 10.0, "efficiency_budget_pct": 10.0}}


async def test_eval_run_honors_correlation_id(monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_routed_gateway(
        candidate_cost=0.001, judge_body={"rubric": {"accuracy": 5.0}, "pairwise": "candidate", "rationale": "ok"}))
    ws, aid, vid = await _seed()
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/eval-runs",
                         headers=dev_headers(workspace=ws, claims={ws: "publisher"},
                                             ) | {"X-Correlation-Id": "eval-corr-9"},
                         json=_eval_body(vid))
    assert r.status_code == 200
    assert r.headers.get("x-correlation-id") == "eval-corr-9"
    rows = await _runs(ws)
    assert rows and all(row.correlation_id == "eval-corr-9" for row in rows)
