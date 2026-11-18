"""Feature 026 US4 (T048) — eval runs read the suite + baseline + policy from the SERVER and are persisted
(contracts/api.md §E, behavioural contract E1/E3/E5–E8; research.md D7–D9).

Integration (compose Postgres). Provider boundary: `runtime.providers.gateway.stream` patched with the routed
double from `_evals_helpers` (candidate + stub judge) — no live/paid call.

Decisions pinned here (026, recorded in tasks.md Evidence for owner review):
- `workspace_settings.eval_policy` is stored in the console's camelCase `EvalPolicy` shape (it is written verbatim by
  PUT /workspaces/settings); the run records the policy it APPLIED in engine snake_case on `eval_runs.policy`.
- A run that hits the provider deadline is still created: 201 with `status="failed"` (the failure is the evidence).
"""
from __future__ import annotations

import datetime as dt
import json
import uuid

import pytest
from sqlalchemy import func, select

from api.db import tenant_session
from api.models import Agent, EvalResult, EvalRun, Run, WorkspaceSettings
from runtime.providers import gateway
from tests._api_helpers import (
    case_content_hash, client, dev_headers, seed_agent, seed_eval_run, seed_limits, seed_run, seed_suite,
    seed_version, seed_workspace,
)
from tests._evals_helpers import JUDGE_MODEL, VALID_OUTPUT, make_routed_gateway
from tests._invoke_helpers import priced_usage, stop_ev, text_ev, usage_ev

pytestmark = pytest.mark.integration

RUBRIC = [{"name": "accuracy", "weight": 1.0}]
POLICY = {"minCases": 2, "qualityThreshold": 10.0, "efficiencyBudgetPct": 50.0, "allowLowConfidenceOverride": False}
APPLIED = {"min_cases": 2, "quality_threshold": 10.0, "efficiency_budget_pct": 50.0,
           "allow_low_confidence_override": False}
T0 = dt.datetime(2026, 11, 17, 9, 0, tzinfo=dt.timezone.utc)


def _cases(n=3):
    return [{"input": {"message": f"alert {i}"}, "rubric": RUBRIC} for i in range(n)]


def _hash(case):
    return case_content_hash(case["input"], case["rubric"], case.get("required_fields", []), case.get("expected"))


def _gw(monkeypatch, **kw):
    kw.setdefault("judge_body", {"rubric": {"accuracy": 4.0}, "pairwise": "candidate", "rationale": "ok"})
    monkeypatch.setattr(gateway, "stream", make_routed_gateway(candidate_cost=0.001, **kw))


def _h(ws, role="publisher", subject="u1"):
    return dev_headers(workspace=ws, subject=subject, claims={ws: role})


async def _set_policy(ws, policy=POLICY):
    async with tenant_session(ws) as s:
        row = await s.get(WorkspaceSettings, uuid.UUID(ws))
        if row is None:
            s.add(WorkspaceSettings(workspace_id=uuid.UUID(ws), default_provider="", default_model="", limits={},
                                    eval_policy=policy))
        else:
            row.eval_policy = policy


async def _set_active(ws, aid, vid):
    async with tenant_session(ws) as s:
        (await s.get(Agent, uuid.UUID(aid))).active_version_id = uuid.UUID(vid)


async def _seed(n=3, *, policy=POLICY):
    """Workspace + agent with v1 (active) and v2 (candidate, NOT active) + a suite of n cases."""
    ws = await seed_workspace()
    aid = await seed_agent(ws)
    v1 = await seed_version(ws, aid, "v1")
    v2 = await seed_version(ws, aid, "v2")
    await _set_active(ws, aid, v1)
    cases = _cases(n)
    sid, cids = await seed_suite(ws, aid, cases=cases)
    await _set_policy(ws, policy)
    return dict(ws=ws, aid=aid, v1=v1, v2=v2, sid=sid, cids=cids, cases=cases)


async def _seed_v1_baseline(s, *, quality=75.0, finished_at=T0):
    """A completed run of v1 on the suite whose results match every current case (id + hash)."""
    results = [{"case_id": cid, "case_hash": _hash(c), "quality": quality, "cost_usd": 0.001, "latency_ms": 100,
                "candidate_output": json.dumps(VALID_OUTPUT), "in_comparison": True}
               for cid, c in zip(s["cids"], s["cases"])]
    return await seed_eval_run(s["ws"], s["aid"], s["sid"], s["v1"], finished_at=finished_at, results=results,
                               quality=quality, cost_usd=0.003, latency_ms=100,
                               compared_cases=0, total_cases=len(results))


async def _post(c, s, body=None, role="publisher"):
    body = body if body is not None else {"version_id": s["v2"], "suite_id": s["sid"]}
    return await c.post(f"/api/v1/agents/{s['aid']}/eval-runs", headers=_h(s["ws"], role), json=body)


async def _count(ws, model, *where):
    async with tenant_session(ws) as sess:
        return (await sess.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


# ---- E1: automatic baseline ---------------------------------------------------------------------

async def test_e1_automatic_baseline_is_the_active_versions_latest_completed_run(monkeypatch):
    _gw(monkeypatch)
    s = await _seed()
    base = await _seed_v1_baseline(s)
    async with client() as c:
        r = await _post(c, s)
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["status"] == "completed" and b["version_id"] == s["v2"] and b["suite_id"] == s["sid"]
    assert b["baseline"] == {"origin": "automatic", "eval_run_id": base, "version_label": "v1"}
    assert (b["compared_cases"], b["new_cases"], b["dropped_cases"], b["total_cases"]) == (3, 0, 0, 3)
    assert b["verdict"] in ("pass", "fail") and b["quality_delta"] is not None
    assert all(res["in_comparison"] for res in b["results"])


async def test_e4_edited_case_is_excluded_from_the_comparison(monkeypatch):
    _gw(monkeypatch)
    s = await _seed(policy=POLICY | {"minCases": 3})
    await _seed_v1_baseline(s)
    async with tenant_session(s["ws"]) as sess:                 # edit case 0 after the baseline ran
        from api.models import EvalCase
        row = await sess.get(EvalCase, uuid.UUID(s["cids"][0]))
        row.input = {"message": "edited"}
        row.content_hash = case_content_hash(row.input, row.rubric, row.required_fields, row.expected)
    async with client() as c:
        r = await _post(c, s)
    b = r.json()
    assert (b["compared_cases"], b["new_cases"], b["dropped_cases"], b["total_cases"]) == (2, 1, 1, 3)
    assert (b["verdict"], b["reason_code"]) == ("low_confidence", "baseline_stale")
    flags = {res["case_id"]: res["in_comparison"] for res in b["results"]}
    assert flags[s["cids"][0]] is False and flags[s["cids"][1]] is True


# ---- E3: no baseline -----------------------------------------------------------------------------

async def test_e3_no_baseline_is_low_confidence_and_still_persisted_with_rubric_scores(monkeypatch):
    _gw(monkeypatch)
    s = await _seed()
    async with client() as c:
        r = await _post(c, s)
    assert r.status_code == 201, r.text
    b = r.json()
    assert (b["verdict"], b["reason_code"]) == ("low_confidence", "no_baseline")
    assert b["baseline"] is None and b["quality_delta"] is None and b["cost_delta_pct"] is None
    assert b["quality"] is not None
    async with tenant_session(s["ws"]) as sess:
        run = await sess.get(EvalRun, uuid.UUID(b["id"]))
        res = list((await sess.execute(select(EvalResult).where(EvalResult.eval_run_id == run.id))).scalars())
    assert run.status == "completed" and run.baseline_run_id is None and run.baseline_origin is None
    assert len(res) == 3
    assert all(x.quality is not None and x.rubric_scores == {"accuracy": 4.0} for x in res)
    assert {x.case_hash for x in res} == {_hash(c) for c in s["cases"]}
    assert all(x.candidate_output for x in res)


# ---- E5: spend cap -------------------------------------------------------------------------------

async def test_e5_spend_cap_refuses_before_any_eval_run_row(monkeypatch):
    _gw(monkeypatch)
    s = await _seed()
    await seed_limits(s["ws"], 0.5)
    await seed_run(s["ws"], cost_usd=1.0)
    async with client() as c:
        r = await _post(c, s)
    assert r.status_code == 402
    assert await _count(s["ws"], EvalRun) == 0


# ---- E6: deadline mid-run -------------------------------------------------------------------------

def _timeout_on(marker: str):
    async def _stream(req, *, api_key, profile=None, timeout=None, **_kw):
        if getattr(req, "model", None) == JUDGE_MODEL:
            yield text_ev(json.dumps({"rubric": {"accuracy": 4.0}, "pairwise": "candidate", "rationale": "ok"}))
            yield usage_ev(priced_usage(cost_usd=0.0001))
            yield stop_ev()
            return
        if marker in json.dumps([m for m in (getattr(req, "messages", None) or [])], default=str):
            raise TimeoutError
        yield text_ev(json.dumps(VALID_OUTPUT))
        yield usage_ev(priced_usage(cost_usd=0.001))
        yield stop_ev()
    return _stream


async def test_e6_deadline_mid_run_persists_a_failed_run_without_results(monkeypatch):
    monkeypatch.setattr(gateway, "stream", _timeout_on("alert 1"))
    s = await _seed()
    async with client() as c:
        r = await _post(c, s)
    assert r.status_code == 201, r.text
    b = r.json()
    assert (b["status"], b["verdict"], b["reason_code"]) == ("failed", None, "timeout")
    assert b["results"] == []
    assert await _count(s["ws"], EvalResult) == 0
    async with tenant_session(s["ws"]) as sess:
        run = await sess.get(EvalRun, uuid.UUID(b["id"]))
    assert run.status == "failed" and run.verdict is None
    assert await _count(s["ws"], Run, Run.source == "eval") >= 1     # per-case runs rows kept (cost stays honest)


# ---- E7: the client body cannot influence anything -------------------------------------------------

_VOLATILE = {"id", "finished_at", "started_at", "latency_ms", "latency_delta_ms"}   # wall-clock, not influence


def _stable(d):
    out = {k: v for k, v in d.items() if k not in _VOLATILE}
    out["results"] = [{k: v for k, v in r.items() if k != "latency_ms"} for r in d.get("results", [])]
    return out


async def test_e7_a_flattering_client_baseline_policy_and_cases_change_nothing(monkeypatch):
    _gw(monkeypatch)
    s = await _seed()
    await _seed_v1_baseline(s, quality=95.0)              # strict real baseline → the candidate (q≈75) drifts
    flattering = {
        "version_id": s["v2"], "suite_id": s["sid"],
        "baseline": {"quality": 0.0, "cost_usd": 1000.0, "latency_ms": 99999, "per_case": {}},
        "policy": {"min_cases": 1, "quality_threshold": 100.0, "efficiency_budget_pct": 1000.0},
        "cases": [{"id": "fake", "input": "x", "rubric": RUBRIC}],
    }
    async with client() as c:
        plain = await _post(c, s)
        faked = await _post(c, s, flattering)
    assert plain.status_code == 201 and faked.status_code == 201
    p, f = plain.json(), faked.json()
    assert p["verdict"] == "fail" and p["reason_code"] == "quality_drift"     # the real baseline decided it
    assert _stable(f) == _stable(p)
    assert f["total_cases"] == 3                                               # server suite, not the fake case
    async with tenant_session(s["ws"]) as sess:
        rp = await sess.get(EvalRun, uuid.UUID(p["id"]))
        rf = await sess.get(EvalRun, uuid.UUID(f["id"]))
    cols = ["verdict", "reason_code", "reason", "baseline_run_id", "baseline_origin", "quality", "quality_delta",
            "cost_usd", "cost_delta_pct", "compared_cases", "total_cases", "policy"]
    assert {k: getattr(rf, k) for k in cols} == {k: getattr(rp, k) for k in cols}
    assert rf.policy == APPLIED


# ---- request validation -----------------------------------------------------------------------------

async def test_missing_suite_id_is_422(monkeypatch):
    _gw(monkeypatch)
    s = await _seed()
    async with client() as c:
        r = await _post(c, s, {"version_id": s["v2"]})
    assert r.status_code == 422


async def test_empty_suite_is_400(monkeypatch):
    _gw(monkeypatch)
    s = await _seed()
    empty, _ = await seed_suite(s["ws"], s["aid"], name="empty")
    async with client() as c:
        r = await _post(c, s, {"version_id": s["v2"], "suite_id": empty})
    assert r.status_code == 400 and r.json()["error"]["code"] == "input.invalid"
    assert await _count(s["ws"], EvalRun) == 0


async def test_more_than_25_cases_is_400_never_truncated(monkeypatch):
    _gw(monkeypatch)
    s = await _seed()
    big, _ = await seed_suite(s["ws"], s["aid"], name="big", cases=_cases(26))
    async with client() as c:
        r = await _post(c, s, {"version_id": s["v2"], "suite_id": big})
    assert r.status_code == 400 and r.json()["error"]["code"] == "input.invalid"
    assert await _count(s["ws"], EvalRun) == 0


async def test_exactly_25_cases_runs(monkeypatch):
    _gw(monkeypatch)
    s = await _seed()
    ok, _ = await seed_suite(s["ws"], s["aid"], name="max", cases=_cases(25))
    async with client() as c:
        r = await _post(c, s, {"version_id": s["v2"], "suite_id": ok})
    assert r.status_code == 201 and r.json()["total_cases"] == 25


async def test_another_agents_version_is_404(monkeypatch):
    _gw(monkeypatch)
    s = await _seed()
    other = await seed_agent(s["ws"], name="Other")
    foreign_v = await seed_version(s["ws"], other)
    async with client() as c:
        r = await _post(c, s, {"version_id": foreign_v, "suite_id": s["sid"]})
    assert r.status_code == 404
    assert await _count(s["ws"], EvalRun) == 0


async def test_another_agents_suite_is_404(monkeypatch):
    _gw(monkeypatch)
    s = await _seed()
    other = await seed_agent(s["ws"], name="Other")
    foreign_suite, _ = await seed_suite(s["ws"], other, cases=_cases(3))
    async with client() as c:
        r = await _post(c, s, {"version_id": s["v2"], "suite_id": foreign_suite})
    assert r.status_code == 404
    assert await _count(s["ws"], EvalRun) == 0


async def test_archived_agent_is_409(monkeypatch):
    _gw(monkeypatch)
    s = await _seed()
    async with tenant_session(s["ws"]) as sess:
        (await sess.get(Agent, uuid.UUID(s["aid"]))).status = "archived"
    async with client() as c:
        r = await _post(c, s)
    assert r.status_code == 409 and r.json()["error"]["code"] == "agent.archived"
    assert await _count(s["ws"], EvalRun) == 0


# ---- policy + identity evidence ------------------------------------------------------------------------

async def test_run_records_the_applied_policy_judge_version_and_creator(monkeypatch):
    _gw(monkeypatch)
    s = await _seed()
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{s['aid']}/eval-runs", headers=_h(s["ws"], subject="alice"),
                         json={"version_id": s["v2"], "suite_id": s["sid"]})
    b = r.json()
    assert b["policy"] == APPLIED and b["judge_version"] and b["created_by"] == "alice"
    async with tenant_session(s["ws"]) as sess:
        run = await sess.get(EvalRun, uuid.UUID(b["id"]))
    assert run.policy == APPLIED and run.judge_version == b["judge_version"] and run.created_by == "alice"


async def test_policy_falls_back_to_defaults_when_the_workspace_has_none(monkeypatch):
    _gw(monkeypatch)
    s = await _seed(policy={})
    async with client() as c:
        r = await _post(c, s)
    assert r.json()["policy"] == {"min_cases": 10, "quality_threshold": 90.0, "efficiency_budget_pct": 10.0,
                                  "allow_low_confidence_override": False}


# ---- history ------------------------------------------------------------------------------------------

async def test_agent_history_is_newest_first_with_keyset_pages(monkeypatch):
    s = await _seed()
    ids = [await seed_eval_run(s["ws"], s["aid"], s["sid"], s["v1"], finished_at=T0 + dt.timedelta(minutes=m))
           for m in (0, 10, 20)]
    async with client() as c:
        p1 = await c.get(f"/api/v1/agents/{s['aid']}/eval-runs?limit=2", headers=_h(s["ws"], "viewer"))
        assert p1.status_code == 200
        b1 = p1.json()
        assert [x["id"] for x in b1["items"]] == [ids[2], ids[1]] and b1["next_cursor"]
        p2 = await c.get(f"/api/v1/agents/{s['aid']}/eval-runs?limit=2&cursor={b1['next_cursor']}",
                         headers=_h(s["ws"], "viewer"))
    b2 = p2.json()
    assert [x["id"] for x in b2["items"]] == [ids[0]] and b2["next_cursor"] is None
    item = b1["items"][0]
    assert item["suite_name"] == "suite-a" and item["version_label"] == "v1"


async def test_workspace_history_lists_only_agents_the_caller_can_use(monkeypatch):
    s = await _seed()
    visible = await seed_eval_run(s["ws"], s["aid"], s["sid"], s["v1"])
    hidden_agent = await seed_agent(s["ws"], name="Hidden", access_policy={"visibility": "restricted"})
    hv = await seed_version(s["ws"], hidden_agent)
    hs, _ = await seed_suite(s["ws"], hidden_agent, cases=_cases(1))
    await seed_eval_run(s["ws"], hidden_agent, hs, hv)
    async with client() as c:
        r = await c.get("/api/v1/eval-runs", headers=_h(s["ws"], "builder"))
    assert r.status_code == 200
    assert [x["id"] for x in r.json()["items"]] == [visible]


async def test_run_detail_has_results_but_no_candidate_output(monkeypatch):
    _gw(monkeypatch)
    s = await _seed()
    async with client() as c:
        rid = (await _post(c, s)).json()["id"]
        r = await c.get(f"/api/v1/eval-runs/{rid}", headers=_h(s["ws"], "viewer"))
    assert r.status_code == 200
    b = r.json()
    assert len(b["results"]) == 3 and {"dimensions", "failing_cases", "policy", "judge_version",
                                       "pairwise_rate"} <= set(b)
    assert all(set(x) == {"case_id", "passed_deterministic", "quality", "in_comparison", "failure_reason",
                          "cost_usd", "latency_ms"} for x in b["results"])
    assert "candidate_output" not in json.dumps(b)


# ---- E8: isolation ------------------------------------------------------------------------------------

async def test_e8_another_workspace_sees_nothing(monkeypatch):
    s = await _seed()
    rid = await seed_eval_run(s["ws"], s["aid"], s["sid"], s["v1"])
    other = await seed_workspace()
    h = dev_headers(workspace=other, claims={other: "admin"})
    async with client() as c:
        detail = await c.get(f"/api/v1/eval-runs/{rid}", headers=h)
        agent_hist = await c.get(f"/api/v1/agents/{s['aid']}/eval-runs", headers=h)
        ws_hist = await c.get("/api/v1/eval-runs", headers=h)
        run = await c.post(f"/api/v1/agents/{s['aid']}/eval-runs", headers=h,
                           json={"version_id": s["v2"], "suite_id": s["sid"]})
    assert detail.status_code == 404 and agent_hist.status_code == 404 and run.status_code == 404
    assert ws_hist.status_code == 200 and ws_hist.json()["items"] == []


# ---- live-found (US4 L19): a TEXT agent's prose answer must reach the judge ---------------------------------------

async def test_text_agent_prose_answer_passes_deterministic_and_is_judged(monkeypatch):
    calls = []
    _gw(monkeypatch, candidate_text="The DB is saturated; page the on-call DBA.", on_judge_call=calls.append)
    s = await _seed()                                   # seeded versions are output_format == "text"
    async with client() as c:
        r = await _post(c, s)
    assert r.status_code == 201, r.text
    b = r.json()
    assert all(res["passed_deterministic"] for res in b["results"]), b["results"]
    assert all(res["quality"] is not None for res in b["results"])
    assert len(calls) == 3                              # every case reached the judge
    assert b["reason_code"] != "judge_unavailable"


# ---- code review 2026-11-18 #1: no declared output_format is TEXT (the runtime JSON-checks only "json") ----------

async def test_agent_without_a_declared_output_format_is_evaluated_as_text(monkeypatch):
    calls = []
    _gw(monkeypatch, candidate_text="Restart the service.", on_judge_call=calls.append)
    s = await _seed()
    v3 = await seed_version(s["ws"], s["aid"], "v3", io_contract={
        "input_schema": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]}})
    async with client() as c:
        r = await _post(c, s, body={"version_id": v3, "suite_id": s["sid"]})
    assert r.status_code == 201, r.text
    b = r.json()
    assert all(res["passed_deterministic"] for res in b["results"]), b["results"]
    assert len(calls) == 3                              # prose reached the judge


# ---- code review 2026-11-18 #2: an all-hard-fail run reports hard_fail, never judge_unavailable ------------------

async def test_a_run_where_every_case_hard_fails_is_a_hard_fail_not_judge_unavailable(monkeypatch):
    _gw(monkeypatch, candidate_text="   ")             # empty output → deterministic hard fail, never judged
    s = await _seed()
    await _seed_v1_baseline(s)
    async with client() as c:
        r = await _post(c, s)
    assert r.status_code == 201, r.text
    b = r.json()
    assert (b["verdict"], b["reason_code"]) == ("fail", "hard_fail"), b


# ---- code review 2026-11-18 #4: a run holds the suite against a concurrent delete --------------------------------

async def test_a_run_in_flight_blocks_a_concurrent_suite_delete(monkeypatch):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    s = await _seed()
    inner = make_routed_gateway(candidate_cost=0.001)
    seen: list[str] = []

    async def _probe(req, **kw):
        if not seen:   # first provider call happens mid-run, inside the run's transaction
            try:
                async with tenant_session(s["ws"]) as other:
                    await other.execute(text("SELECT id FROM eval_suites WHERE id = :i FOR UPDATE NOWAIT"),
                                        {"i": s["sid"]})
                seen.append("acquired")
            except DBAPIError:
                seen.append("blocked")
        async for ev in inner(req, **kw):
            yield ev

    monkeypatch.setattr(gateway, "stream", _probe)
    async with client() as c:
        r = await _post(c, s)
    assert r.status_code == 201, r.text
    assert seen == ["blocked"]          # the delete's FOR UPDATE could not take the suite while the run was live
