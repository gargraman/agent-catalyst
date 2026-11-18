"""US4 — eval-run (inline, via the M3 engine over the shared runtime) + advisory publish (M4, feature 005).
Integration (seeds a version) + mocked routed gateway (no live call). Publish never raises the eval gate."""
from __future__ import annotations

import json

import pytest

from runtime.providers import gateway
from tests._api_helpers import client, dev_headers, eval_run_body, seed_agent, seed_version, seed_workspace
from tests._evals_helpers import VALID_OUTPUT, make_routed_gateway

pytestmark = pytest.mark.integration


async def _seed():
    ws = await seed_workspace()
    aid = await seed_agent(ws)
    vid = await seed_version(ws, aid)
    return ws, aid, vid


async def _eval_body(ws: str, aid: str, vid: str, n: int = 3) -> dict:
    """026 T050: the suite lives on the server now; the body is only {version_id, suite_id}."""
    return await eval_run_body(ws, aid, vid, n)


async def test_eval_run_executes_engine_and_returns_verdict(monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_routed_gateway(
        candidate_cost=0.001,
        judge_body={"rubric": {"accuracy": 5.0}, "pairwise": "candidate", "rationale": "great"}))
    ws, aid, vid = await _seed()
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/eval-runs",
                         headers=dev_headers(workspace=ws, claims={ws: "publisher"}), json=await _eval_body(ws, aid, vid))
    assert r.status_code == 201
    body = r.json()
    assert body["verdict"] in ("pass", "fail", "low_confidence")
    assert "quality" in body and "dimensions" in body and "pairwise_rate" in body


async def test_eval_persists_only_candidate_runs_not_judge(monkeypatch):
    """F1 — an N-case eval run persists exactly N source='eval' runs (the candidate), NOT the judge calls."""
    from sqlalchemy import select
    from api.db import tenant_session
    from api.models import Run
    monkeypatch.setattr(gateway, "stream", make_routed_gateway(
        candidate_cost=0.001,
        judge_body={"rubric": {"accuracy": 5.0}, "pairwise": "candidate", "rationale": "ok"}))
    ws, aid, vid = await _seed()
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/eval-runs",
                         headers=dev_headers(workspace=ws, claims={ws: "publisher"}), json=await _eval_body(ws, aid, vid, n=3))
    assert r.status_code == 201
    async with tenant_session(ws) as s:
        rows = list((await s.execute(select(Run))).scalars().all())
    assert len(rows) == 3                                   # 3 candidate runs, judge runs NOT recorded
    assert all(str(row.version_id) == vid for row in rows)
    assert all(row.source == "eval" for row in rows)


async def test_publish_is_advisory_not_gated():
    ws, aid, vid = await _seed()
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/publish",
                         headers=dev_headers(workspace=ws, claims={ws: "publisher"}), json={"version_id": vid})
    assert r.status_code == 200                       # NOT 409 publish.eval_gate, despite no passing eval
    body = r.json()
    assert body["status"] == "published"
    assert body["agent_id"] == aid and body["version_id"] == vid
    assert "advisory" in body["advisory_note"].lower()


# ---- post-review hardening (code-review 2026-11-05, F2) -------------------------------------------

async def test_publish_malformed_version_is_400():
    """F2 — a malformed version_id → typed 400, not an unhandled 500."""
    ws, aid, vid = await _seed()
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/publish",
                         headers=dev_headers(workspace=ws, claims={ws: "publisher"}), json={"version_id": "not-a-uuid"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "input.invalid"


async def test_publish_foreign_version_is_404_no_cross_agent_mispublish():
    """F2 — publishing a version that belongs to ANOTHER agent is rejected (no cross-agent mispublish)."""
    ws = await seed_workspace()
    aid1 = await seed_agent(ws)
    await seed_version(ws, aid1)
    aid2 = await seed_agent(ws)
    v2 = await seed_version(ws, aid2)                    # v2 belongs to aid2
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid1}/publish",
                         headers=dev_headers(workspace=ws, claims={ws: "publisher"}), json={"version_id": v2})
    assert r.status_code == 404                          # v2 is not aid1's version
