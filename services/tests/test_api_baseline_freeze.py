"""Feature 026 US4 (T049) — admin-only, audited baseline freeze/clear (contracts/api.md §D; owner O2; FR-023).

Integration (compose Postgres). Freeze pins a completed run of the suite as its baseline (overrides the automatic
rule, E2); clear returns the suite to the automatic rule. Both write an `audit_logs` row in the SAME transaction
(meta = ids only, D10). `eval.govern` is admin-only, refused by `require_permission` BEFORE any lookup.
Audit target pinned here (026): `target_type="eval_suite"`, `target_id=<suite id>`.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select

from api.db import tenant_session
from api.models import Agent, AuditLog, EvalBaseline, EvalRun
from runtime.providers import gateway
from tests._api_helpers import (
    case_content_hash, client, dev_headers, freeze, seed_agent, seed_eval_run, seed_suite, seed_version,
    seed_workspace,
)
from tests._evals_helpers import VALID_OUTPUT, make_routed_gateway

pytestmark = pytest.mark.integration

RUBRIC = [{"name": "accuracy", "weight": 1.0}]
CASES = [{"input": {"message": f"alert {i}"}, "rubric": RUBRIC} for i in range(2)]


def _h(ws, role="admin", subject="admin-1"):
    return dev_headers(workspace=ws, subject=subject, claims={ws: role})


async def _seed():
    ws = await seed_workspace()
    aid = await seed_agent(ws)
    v1 = await seed_version(ws, aid, "v1")
    sid, cids = await seed_suite(ws, aid, cases=CASES)
    results = [{"case_id": cid, "case_hash": case_content_hash(c["input"], c["rubric"]), "quality": 75.0,
                "cost_usd": 0.001, "latency_ms": 100, "candidate_output": json.dumps(VALID_OUTPUT)}
               for cid, c in zip(cids, CASES)]
    r1 = await seed_eval_run(ws, aid, sid, v1, results=results)
    r2 = await seed_eval_run(ws, aid, sid, v1, results=results)
    return dict(ws=ws, aid=aid, v1=v1, sid=sid, cids=cids, r1=r1, r2=r2, results=results)


async def _audit(ws, action):
    async with tenant_session(ws) as s:
        return list((await s.execute(select(AuditLog).where(AuditLog.action == action)
                                     .order_by(AuditLog.created_at))).scalars())


async def _frozen(ws, sid):
    async with tenant_session(ws) as s:
        return await s.get(EvalBaseline, uuid.UUID(sid))


# ---- freeze --------------------------------------------------------------------------------------

async def test_admin_freeze_returns_a_frozen_baseline_and_audits_it():
    s = await _seed()
    async with client() as c:
        r = await c.put(f"/api/v1/eval-suites/{s['sid']}/baseline", headers=_h(s["ws"]), json={"eval_run_id": s["r1"]})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["origin"] == "frozen" and b["eval_run_id"] == s["r1"] and b["version_id"] == s["v1"]
    assert b["frozen_by"] == "admin-1" and b["frozen_at"]
    row = await _frozen(s["ws"], s["sid"])
    assert str(row.eval_run_id) == s["r1"] and row.frozen_by == "admin-1"
    (a,) = await _audit(s["ws"], "baseline.freeze")
    assert a.actor == "admin-1" and a.target_type == "eval_suite" and str(a.target_id) == s["sid"]
    assert a.meta == {"previous_run_id": None, "new_run_id": s["r1"]}


async def test_refreeze_replaces_the_pointer_and_audits_previous_and_new():
    s = await _seed()
    async with client() as c:
        await c.put(f"/api/v1/eval-suites/{s['sid']}/baseline", headers=_h(s["ws"]), json={"eval_run_id": s["r1"]})
        r = await c.put(f"/api/v1/eval-suites/{s['sid']}/baseline", headers=_h(s["ws"]), json={"eval_run_id": s["r2"]})
    assert r.status_code == 200 and r.json()["eval_run_id"] == s["r2"]
    assert str((await _frozen(s["ws"], s["sid"])).eval_run_id) == s["r2"]
    audits = await _audit(s["ws"], "baseline.freeze")
    assert [a.meta for a in audits] == [{"previous_run_id": None, "new_run_id": s["r1"]},
                                        {"previous_run_id": s["r1"], "new_run_id": s["r2"]}]


async def test_suite_list_reflects_the_frozen_baseline():
    s = await _seed()
    async with client() as c:
        await c.put(f"/api/v1/eval-suites/{s['sid']}/baseline", headers=_h(s["ws"]), json={"eval_run_id": s["r1"]})
        lst = await c.get(f"/api/v1/agents/{s['aid']}/eval-suites", headers=_h(s["ws"], "viewer"))
    b = lst.json()[0]["baseline"]
    assert b["origin"] == "frozen" and b["eval_run_id"] == s["r1"] and b["frozen_by"] == "admin-1"


# ---- clear ---------------------------------------------------------------------------------------

async def test_clear_is_204_audits_previous_and_returns_to_automatic():
    s = await _seed()
    await freeze(s["ws"], s["sid"], s["r1"])
    async with client() as c:
        r = await c.delete(f"/api/v1/eval-suites/{s['sid']}/baseline", headers=_h(s["ws"]))
        lst = await c.get(f"/api/v1/agents/{s['aid']}/eval-suites", headers=_h(s["ws"], "viewer"))
    assert r.status_code == 204
    assert await _frozen(s["ws"], s["sid"]) is None
    (a,) = await _audit(s["ws"], "baseline.clear")
    assert a.meta == {"previous_run_id": s["r1"]} and str(a.target_id) == s["sid"]
    assert lst.json()[0]["baseline"]["origin"] == "automatic"


async def test_clear_is_idempotent_and_audits_only_when_something_was_cleared():
    s = await _seed()
    async with client() as c:
        r1 = await c.delete(f"/api/v1/eval-suites/{s['sid']}/baseline", headers=_h(s["ws"]))
        r2 = await c.delete(f"/api/v1/eval-suites/{s['sid']}/baseline", headers=_h(s["ws"]))
    assert (r1.status_code, r2.status_code) == (204, 204)
    assert await _audit(s["ws"], "baseline.clear") == []


# ---- E2: frozen wins after a re-point ------------------------------------------------------------------

async def test_e2_after_a_repoint_the_next_run_compares_to_the_frozen_run(monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_routed_gateway(
        judge_body={"rubric": {"accuracy": 4.0}, "pairwise": "candidate", "rationale": "ok"}))
    s = await _seed()
    v2 = await seed_version(s["ws"], s["aid"], "v2")       # becomes active (re-point)
    newer_v2 = await seed_eval_run(s["ws"], s["aid"], s["sid"], v2, results=s["results"])   # automatic would pick this
    async with client() as c:
        f = await c.put(f"/api/v1/eval-suites/{s['sid']}/baseline", headers=_h(s["ws"]), json={"eval_run_id": s["r1"]})
        assert f.status_code == 200
        r = await c.post(f"/api/v1/agents/{s['aid']}/eval-runs", headers=_h(s["ws"]),
                         json={"version_id": v2, "suite_id": s["sid"]})
    assert r.status_code == 201, r.text
    assert r.json()["baseline"] == {"origin": "frozen", "eval_run_id": s["r1"], "version_label": "v1"}
    assert r.json()["baseline"]["eval_run_id"] != newer_v2


# ---- permissions: admin only, refused before any lookup ------------------------------------------------

@pytest.mark.parametrize("role", ["publisher", "builder", "viewer"])
async def test_non_admin_is_403_for_freeze_and_clear(role):
    s = await _seed()
    await freeze(s["ws"], s["sid"], s["r1"])
    async with client() as c:
        put = await c.put(f"/api/v1/eval-suites/{s['sid']}/baseline", headers=_h(s["ws"], role),
                          json={"eval_run_id": s["r2"]})
        dele = await c.delete(f"/api/v1/eval-suites/{s['sid']}/baseline", headers=_h(s["ws"], role))
    for r in (put, dele):
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "access.denied"
        assert r.json()["error"]["detail"]["required_permission"] == "eval.govern"
    assert str((await _frozen(s["ws"], s["sid"])).eval_run_id) == s["r1"]       # unchanged
    assert await _audit(s["ws"], "baseline.freeze") == [] and await _audit(s["ws"], "baseline.clear") == []


async def test_publisher_is_refused_before_any_lookup_even_for_a_missing_suite():
    s = await _seed()
    async with client() as c:
        r = await c.put(f"/api/v1/eval-suites/{uuid.uuid4()}/baseline", headers=_h(s["ws"], "publisher"),
                        json={"eval_run_id": s["r1"]})
    assert r.status_code == 403          # not 404: RBAC first, so existence never leaks to a non-admin


# ---- invalid runs ----------------------------------------------------------------------------------

async def test_freezing_a_failed_run_is_400():
    s = await _seed()
    failed = await seed_eval_run(s["ws"], s["aid"], s["sid"], s["v1"], status="failed", reason_code="timeout")
    async with client() as c:
        r = await c.put(f"/api/v1/eval-suites/{s['sid']}/baseline", headers=_h(s["ws"]), json={"eval_run_id": failed})
    assert r.status_code == 400 and r.json()["error"]["code"] == "input.invalid"
    assert await _frozen(s["ws"], s["sid"]) is None and await _audit(s["ws"], "baseline.freeze") == []


async def test_freezing_another_suites_run_is_400():
    s = await _seed()
    other_sid, ocids = await seed_suite(s["ws"], s["aid"], name="other", cases=CASES)
    other_run = await seed_eval_run(s["ws"], s["aid"], other_sid, s["v1"], results=[{"case_id": ocids[0]}])
    async with client() as c:
        r = await c.put(f"/api/v1/eval-suites/{s['sid']}/baseline", headers=_h(s["ws"]),
                        json={"eval_run_id": other_run})
    assert r.status_code == 400 and r.json()["error"]["code"] == "input.invalid"
    assert await _frozen(s["ws"], s["sid"]) is None


async def test_freezing_another_workspaces_run_is_400():
    s = await _seed()
    t = await _seed()                                   # a second workspace with its own run
    async with client() as c:
        r = await c.put(f"/api/v1/eval-suites/{s['sid']}/baseline", headers=_h(s["ws"]), json={"eval_run_id": t["r1"]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "input.invalid"
    assert await _frozen(s["ws"], s["sid"]) is None


async def test_malformed_run_id_is_400():
    s = await _seed()
    async with client() as c:
        r = await c.put(f"/api/v1/eval-suites/{s['sid']}/baseline", headers=_h(s["ws"]), json={"eval_run_id": "nope"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "input.invalid"


async def test_trigger_backs_up_the_route_check_when_bypassed():
    """The DB refuses a failed-run baseline even when no route check runs (defence in depth, 23514)."""
    s = await _seed()
    failed = await seed_eval_run(s["ws"], s["aid"], s["sid"], s["v1"], status="failed", reason_code="timeout")
    with pytest.raises(Exception, match="eval_baselines_run_check"):
        await freeze(s["ws"], s["sid"], failed)


# ---- isolation ---------------------------------------------------------------------------------------

async def test_another_workspace_admin_gets_404():
    s = await _seed()
    other = await seed_workspace()
    h = dev_headers(workspace=other, claims={other: "admin"})
    async with client() as c:
        put = await c.put(f"/api/v1/eval-suites/{s['sid']}/baseline", headers=h, json={"eval_run_id": s["r1"]})
        dele = await c.delete(f"/api/v1/eval-suites/{s['sid']}/baseline", headers=h)
    assert (put.status_code, dele.status_code) == (404, 404)
