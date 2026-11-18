"""Feature 026 US4 (T047) — eval suites & cases, server-side (contracts/api.md §C).

Integration (compose Postgres). Suites/cases are authored on the server so a run can never be fed cases by the
client (FR-015). `content_hash` is server-computed (D8 identity). Permission = `eval.author` (builder+) for writes,
`view` + ensure_use for reads; another workspace sees 404.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from api.db import tenant_session
from api.models import EvalCase
from tests._api_helpers import (
    case_content_hash, client, dev_headers, seed_agent, seed_eval_run, seed_suite, seed_version, seed_workspace,
)

pytestmark = pytest.mark.integration

CASE = {"input": {"message": "An XDR alert fired"}, "rubric": [{"name": "accuracy", "weight": 1.0}],
        "required_fields": ["severity"], "expected": None}


async def _seed():
    ws = await seed_workspace()
    aid = await seed_agent(ws)
    return ws, aid


def _h(ws, role="builder", subject="u1"):
    return dev_headers(workspace=ws, subject=subject, claims={ws: role})


async def _create_suite(c, ws, aid, name="triage"):
    r = await c.post(f"/api/v1/agents/{aid}/eval-suites", headers=_h(ws), json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


# ---- suites ------------------------------------------------------------------------------------------

async def test_suite_create_list_get_rename_delete():
    ws, aid = await _seed()
    async with client() as c:
        s = await _create_suite(c, ws, aid)
        assert s["agent_id"] == aid and s["name"] == "triage" and s["case_count"] == 0
        assert {"id", "updated_at", "baseline"} <= set(s)

        lst = await c.get(f"/api/v1/agents/{aid}/eval-suites", headers=_h(ws, "viewer"))
        assert lst.status_code == 200 and [x["id"] for x in lst.json()] == [s["id"]]

        got = await c.get(f"/api/v1/eval-suites/{s['id']}", headers=_h(ws, "viewer"))
        assert got.status_code == 200 and got.json()["cases"] == []

        ren = await c.patch(f"/api/v1/eval-suites/{s['id']}", headers=_h(ws), json={"name": "triage-v2"})
        assert ren.status_code == 200 and ren.json()["name"] == "triage-v2"

        d = await c.delete(f"/api/v1/eval-suites/{s['id']}", headers=_h(ws))
        assert d.status_code == 204
        gone = await c.get(f"/api/v1/eval-suites/{s['id']}", headers=_h(ws))
        assert gone.status_code == 404


@pytest.mark.parametrize("name", ["", "x" * 81])
async def test_suite_name_length_is_1_to_80(name):
    ws, aid = await _seed()
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/eval-suites", headers=_h(ws), json={"name": name})
    assert r.status_code == 422


async def test_suite_name_80_chars_is_accepted():
    ws, aid = await _seed()
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/eval-suites", headers=_h(ws), json={"name": "x" * 80})
    assert r.status_code == 201


async def test_duplicate_suite_name_for_the_same_agent_is_409_conflict():
    ws, aid = await _seed()
    async with client() as c:
        await _create_suite(c, ws, aid, "dup")
        r = await c.post(f"/api/v1/agents/{aid}/eval-suites", headers=_h(ws), json={"name": "dup"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"


async def test_same_suite_name_on_another_agent_is_fine():
    ws, aid = await _seed()
    other = await seed_agent(ws, name="Other")
    async with client() as c:
        await _create_suite(c, ws, aid, "shared")
        r = await c.post(f"/api/v1/agents/{other}/eval-suites", headers=_h(ws), json={"name": "shared"})
    assert r.status_code == 201


async def test_deleting_a_suite_with_runs_is_409_suite_has_runs():
    ws, aid = await _seed()
    vid = await seed_version(ws, aid)
    sid, cids = await seed_suite(ws, aid, cases=[CASE])
    await seed_eval_run(ws, aid, sid, vid, results=[{"case_id": cids[0]}])
    async with client() as c:
        r = await c.delete(f"/api/v1/eval-suites/{sid}", headers=_h(ws))
        assert r.status_code == 409 and r.json()["error"]["code"] == "eval.suite_has_runs"
        still = await c.get(f"/api/v1/eval-suites/{sid}", headers=_h(ws))
    assert still.status_code == 200 and len(still.json()["cases"]) == 1


async def test_deleting_a_suite_without_runs_cascades_its_cases():
    ws, aid = await _seed()
    sid, _ = await seed_suite(ws, aid, cases=[CASE, CASE | {"input": {"message": "two"}}])
    async with client() as c:
        r = await c.delete(f"/api/v1/eval-suites/{sid}", headers=_h(ws))
    assert r.status_code == 204
    async with tenant_session(ws) as s:
        n = (await s.execute(select(func.count()).select_from(EvalCase)
                             .where(EvalCase.suite_id == uuid.UUID(sid)))).scalar_one()
    assert n == 0


# ---- cases: input validation (EvalCaseIn, extra="forbid") --------------------------------------------

@pytest.mark.parametrize("bad", [
    {"rubric": []},                                                                  # 0 dimensions
    {"rubric": [{"name": f"d{i}", "weight": 1.0} for i in range(9)]},                # 9 dimensions
    {"rubric": [{"name": "accuracy", "weight": 0}]},                                 # weight 0
    {"rubric": [{"name": "accuracy", "weight": -1.0}]},                              # weight < 0
    {"surprise": True},                                                              # unknown field
    {"content_hash": "client-chosen"},                                               # server-computed only
])
async def test_case_input_is_validated(bad):
    ws, aid = await _seed()
    async with client() as c:
        s = await _create_suite(c, ws, aid)
        r = await c.post(f"/api/v1/eval-suites/{s['id']}/cases", headers=_h(ws), json=CASE | bad)
    assert r.status_code == 422, r.text


async def test_case_with_8_dimensions_is_accepted():
    ws, aid = await _seed()
    async with client() as c:
        s = await _create_suite(c, ws, aid)
        r = await c.post(f"/api/v1/eval-suites/{s['id']}/cases", headers=_h(ws),
                         json=CASE | {"rubric": [{"name": f"d{i}", "weight": 0.5} for i in range(8)]})
    assert r.status_code == 201


# ---- cases: writes + server-computed content_hash -------------------------------------------------

async def test_case_create_returns_the_server_computed_content_hash():
    ws, aid = await _seed()
    async with client() as c:
        s = await _create_suite(c, ws, aid)
        r = await c.post(f"/api/v1/eval-suites/{s['id']}/cases", headers=_h(ws), json=CASE)
    assert r.status_code == 201
    body = r.json()
    assert body["suite_id"] == s["id"] and {"id", "updated_at"} <= set(body)
    assert body["content_hash"] == case_content_hash(CASE["input"], CASE["rubric"], CASE["required_fields"],
                                                     CASE["expected"])


@pytest.mark.parametrize("change", [
    {"input": {"message": "different"}},
    {"rubric": [{"name": "accuracy", "weight": 2.0}]},
    {"required_fields": ["severity", "summary"]},
    {"expected": {"severity": "high"}},
])
async def test_content_hash_changes_with_each_hashed_field(change):
    ws, aid = await _seed()
    async with client() as c:
        s = await _create_suite(c, ws, aid)
        created = (await c.post(f"/api/v1/eval-suites/{s['id']}/cases", headers=_h(ws), json=CASE)).json()
        r = await c.put(f"/api/v1/eval-suites/{s['id']}/cases/{created['id']}", headers=_h(ws), json=CASE | change)
    assert r.status_code == 200
    assert r.json()["content_hash"] != created["content_hash"]


async def test_identical_put_keeps_the_content_hash():
    ws, aid = await _seed()
    async with client() as c:
        s = await _create_suite(c, ws, aid)
        created = (await c.post(f"/api/v1/eval-suites/{s['id']}/cases", headers=_h(ws), json=CASE)).json()
        r = await c.put(f"/api/v1/eval-suites/{s['id']}/cases/{created['id']}", headers=_h(ws), json=CASE)
    assert r.status_code == 200 and r.json()["content_hash"] == created["content_hash"]


async def test_case_delete_is_204_and_lowers_case_count():
    ws, aid = await _seed()
    async with client() as c:
        s = await _create_suite(c, ws, aid)
        created = (await c.post(f"/api/v1/eval-suites/{s['id']}/cases", headers=_h(ws), json=CASE)).json()
        d = await c.delete(f"/api/v1/eval-suites/{s['id']}/cases/{created['id']}", headers=_h(ws))
        lst = await c.get(f"/api/v1/agents/{aid}/eval-suites", headers=_h(ws))
    assert d.status_code == 204
    assert lst.json()[0]["case_count"] == 0


# ---- list view: case_count + baseline (resolved by api.baseline) ---------------------------------------

async def test_list_shows_case_count_and_the_resolved_baseline():
    ws, aid = await _seed()
    vid = await seed_version(ws, aid)                      # active version
    sid, cids = await seed_suite(ws, aid, cases=[CASE, CASE | {"input": {"message": "two"}}])
    rid = await seed_eval_run(ws, aid, sid, vid, results=[{"case_id": cids[0]}])
    empty_sid, _ = await seed_suite(ws, aid, name="empty")
    async with client() as c:
        r = await c.get(f"/api/v1/agents/{aid}/eval-suites", headers=_h(ws, "viewer"))
    by = {s["id"]: s for s in r.json()}
    assert by[sid]["case_count"] == 2
    assert by[sid]["baseline"]["origin"] == "automatic"
    assert by[sid]["baseline"]["eval_run_id"] == rid and by[sid]["baseline"]["version_id"] == vid
    assert by[empty_sid]["baseline"]["origin"] == "none" and by[empty_sid]["baseline"]["eval_run_id"] is None


# ---- permissions + isolation ---------------------------------------------------------------------

async def test_viewer_cannot_author():
    ws, aid = await _seed()
    sid, cids = await seed_suite(ws, aid, cases=[CASE])
    async with client() as c:
        rs = [
            await c.post(f"/api/v1/agents/{aid}/eval-suites", headers=_h(ws, "viewer"), json={"name": "n"}),
            await c.patch(f"/api/v1/eval-suites/{sid}", headers=_h(ws, "viewer"), json={"name": "n"}),
            await c.delete(f"/api/v1/eval-suites/{sid}", headers=_h(ws, "viewer")),
            await c.post(f"/api/v1/eval-suites/{sid}/cases", headers=_h(ws, "viewer"), json=CASE),
            await c.put(f"/api/v1/eval-suites/{sid}/cases/{cids[0]}", headers=_h(ws, "viewer"), json=CASE),
            await c.delete(f"/api/v1/eval-suites/{sid}/cases/{cids[0]}", headers=_h(ws, "viewer")),
        ]
    assert [r.status_code for r in rs] == [403] * 6
    assert all(r.json()["error"]["detail"]["required_permission"] == "eval.author" for r in rs)


async def test_another_workspace_gets_404():
    ws, aid = await _seed()
    sid, cids = await seed_suite(ws, aid, cases=[CASE])
    other = await seed_workspace()
    h = dev_headers(workspace=other, claims={other: "admin"})
    async with client() as c:
        rs = [
            await c.get(f"/api/v1/agents/{aid}/eval-suites", headers=h),
            await c.post(f"/api/v1/agents/{aid}/eval-suites", headers=h, json={"name": "n"}),
            await c.get(f"/api/v1/eval-suites/{sid}", headers=h),
            await c.patch(f"/api/v1/eval-suites/{sid}", headers=h, json={"name": "n"}),
            await c.delete(f"/api/v1/eval-suites/{sid}", headers=h),
            await c.post(f"/api/v1/eval-suites/{sid}/cases", headers=h, json=CASE),
            await c.put(f"/api/v1/eval-suites/{sid}/cases/{cids[0]}", headers=h, json=CASE),
            await c.delete(f"/api/v1/eval-suites/{sid}/cases/{cids[0]}", headers=h),
        ]
    assert [r.status_code for r in rs] == [404] * 8


# ---- code review 2026-11-18 #6: the 25-case run cap is enforced at authoring time ------------------------------

async def test_a_26th_case_is_refused_so_the_suite_stays_runnable():
    ws, aid = await _seed()
    sid, _ = await seed_suite(ws, aid, cases=[{"input": {"message": f"m{i}"}} for i in range(25)])
    async with client() as c:
        r = await c.post(f"/api/v1/eval-suites/{sid}/cases", headers=_h(ws), json=CASE)
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "input.invalid"
    assert r.json()["error"]["detail"] == {"suite_id": sid, "cases": 25, "max": 25}
    async with tenant_session(ws) as s:
        n = (await s.execute(select(func.count()).select_from(EvalCase)
                             .where(EvalCase.suite_id == uuid.UUID(sid)))).scalar_one()
    assert n == 25
