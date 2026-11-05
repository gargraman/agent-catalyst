"""US4 — eval-run (inline, via the M3 engine over the shared runtime) + advisory publish (M4, feature 005).
Integration (seeds a version) + mocked routed gateway (no live call). Publish never raises the eval gate."""
from __future__ import annotations

import json

import pytest

from runtime.providers import gateway
from tests._api_helpers import client, dev_headers, seed_agent, seed_version, seed_workspace
from tests._evals_helpers import VALID_OUTPUT, make_routed_gateway

pytestmark = pytest.mark.integration


async def _seed():
    ws = await seed_workspace()
    aid = await seed_agent(ws)
    vid = await seed_version(ws, aid)
    return ws, aid, vid


def _eval_body(vid: str, n: int = 3) -> dict:
    cases = [{"id": f"c{i}", "input": "an XDR alert fired",
              "rubric": [{"name": "accuracy", "weight": 1.0}]} for i in range(n)]
    baseline = {"quality": 90.0, "cost_usd": 0.003, "latency_ms": 100,
                "per_case": {f"c{i}": json.dumps(VALID_OUTPUT) for i in range(n)}}
    return {"version_id": vid, "cases": cases, "baseline": baseline,
            "policy": {"min_cases": 3, "quality_threshold": 10.0, "efficiency_budget_pct": 10.0}}


async def test_eval_run_executes_engine_and_returns_verdict(monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_routed_gateway(
        candidate_cost=0.001,
        judge_body={"rubric": {"accuracy": 5.0}, "pairwise": "candidate", "rationale": "great"}))
    ws, aid, vid = await _seed()
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/eval-runs",
                         headers=dev_headers(workspace=ws), json=_eval_body(vid))
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] in ("pass", "fail", "low_confidence")
    assert "quality" in body and "dimensions" in body and "pairwise_rate" in body


async def test_publish_is_advisory_not_gated():
    ws, aid, vid = await _seed()
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/publish",
                         headers=dev_headers(workspace=ws), json={"version_id": vid})
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
                         headers=dev_headers(workspace=ws), json={"version_id": "not-a-uuid"})
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
                         headers=dev_headers(workspace=ws), json={"version_id": v2})
    assert r.status_code == 404                          # v2 is not aid1's version
