"""Feature 026 US3 T040 — publish returns an ADVISORY verdict read from persisted eval runs (research D5).

`POST /agents/{id}/publish` → `advisory: {verdict, reason_code, eval_run_id, suite_id}` = the newest COMPLETED eval run
(by finished_at, any suite) of the version being published. No such run → low_confidence / no_eval_run / nulls.
Zero provider calls, never blocks; `agent.publish` audited in the SAME transaction; builder → 403.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from api.db import tenant_session
from api.models import Agent, AuditLog
from runtime.providers import gateway
from tests._api_helpers import (client, dev_headers, seed_agent, seed_eval_run, seed_suite, seed_version,
                                seed_workspace)

pytestmark = pytest.mark.integration

T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)


async def _seed():
    ws = await seed_workspace()
    aid = await seed_agent(ws)
    v1 = await seed_version(ws, aid, label="v1")
    v2 = await seed_version(ws, aid, label="v2")
    suite, _ = await seed_suite(ws, aid, cases=[{}])
    return ws, aid, v1, v2, suite


async def _publish(ws, aid, vid, role="publisher"):
    async with client() as c:
        return await c.post(f"/api/v1/agents/{aid}/publish", json={"version_id": vid},
                            headers=dev_headers(workspace=ws, claims={ws: role}))


@pytest.fixture(autouse=True)
def _no_provider_calls(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("publish must make no provider call (D5)")
    monkeypatch.setattr(gateway, "stream", _boom)


async def test_advisory_is_the_latest_completed_run_of_the_published_version():
    ws, aid, v1, v2, suite = await _seed()
    other_suite, _ = await seed_suite(ws, aid, name="suite-b", cases=[{}])
    await seed_eval_run(ws, aid, suite, v1, verdict="pass", reason_code="ok", finished_at=T0)
    want = await seed_eval_run(ws, aid, other_suite, v1, verdict="fail", reason_code="quality_regression",
                               finished_at=T0 + timedelta(hours=1))                    # newest completed, any suite
    await seed_eval_run(ws, aid, suite, v1, status="failed", reason_code="provider_error",
                        finished_at=T0 + timedelta(hours=2))                           # newer but FAILED → ignored
    await seed_eval_run(ws, aid, suite, v2, verdict="pass", reason_code="ok",
                        finished_at=T0 + timedelta(hours=3))                           # other version → ignored
    r = await _publish(ws, aid, v1)
    assert r.status_code == 200, r.text
    assert r.json()["advisory"] == {"verdict": "fail", "reason_code": "quality_regression",
                                    "eval_run_id": want, "suite_id": other_suite}


async def test_no_completed_run_is_low_confidence_no_eval_run():
    ws, aid, v1, v2, suite = await _seed()
    await seed_eval_run(ws, aid, suite, v1, status="failed", reason_code="provider_error", finished_at=T0)
    await seed_eval_run(ws, aid, suite, v2, verdict="pass", finished_at=T0)
    r = await _publish(ws, aid, v1)
    assert r.status_code == 200, r.text
    assert r.json()["advisory"] == {"verdict": "low_confidence", "reason_code": "no_eval_run",
                                    "eval_run_id": None, "suite_id": None}


async def test_a_fail_advisory_never_blocks_publish():
    ws, aid, v1, _v2, suite = await _seed()
    await seed_eval_run(ws, aid, suite, v1, verdict="fail", reason_code="quality_regression", finished_at=T0)
    r = await _publish(ws, aid, v1)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "published" and body["advisory"]["verdict"] == "fail"
    async with tenant_session(ws) as s:
        a = await s.get(Agent, uuid.UUID(aid))
    assert a.status == "published" and str(a.active_version_id) == v1


async def test_publish_writes_one_agent_publish_audit_row():
    ws, aid, v1, _v2, suite = await _seed()
    run = await seed_eval_run(ws, aid, suite, v1, verdict="pass", finished_at=T0)
    r = await _publish(ws, aid, v1)
    assert r.status_code == 200, r.text
    async with tenant_session(ws) as s:
        rows = list((await s.scalars(select(AuditLog).where(AuditLog.action == "agent.publish"))).all())
    assert len(rows) == 1
    (row,) = rows
    assert row.target_type == "agent" and str(row.target_id) == aid
    assert row.meta["version_id"] == v1 and row.meta["prior_status"] == "draft"
    assert row.meta["eval_run_id"] == run      # the advisory's source run is on the record (D10: ids only, no verdict text)


async def test_publish_rolls_back_the_audit_row_with_the_change(monkeypatch):
    """Same transaction: a failure after the audit write leaves neither the publish nor the audit row."""
    import api.routers.evals as evals_router
    from api.errors import InputInvalid
    ws, aid, v1, v2, _suite = await _seed()             # v2 is active (seed_version re-points)
    real = evals_router.PublishOut

    def _fail(*a, **k):
        raise InputInvalid("boom", detail={"test": "rollback"})
    monkeypatch.setattr(evals_router, "PublishOut", _fail)
    r = await _publish(ws, aid, v1)
    monkeypatch.setattr(evals_router, "PublishOut", real)
    assert r.status_code == 400, r.text
    async with tenant_session(ws) as s:
        a = await s.get(Agent, uuid.UUID(aid))
        rows = list((await s.scalars(select(AuditLog).where(AuditLog.action == "agent.publish"))).all())
    assert str(a.active_version_id) == v2 and a.status == "draft"
    assert rows == []


async def test_builder_cannot_publish():
    ws, aid, v1, v2, _suite = await _seed()
    r = await _publish(ws, aid, v1, role="builder")
    assert r.status_code == 403, r.text
    assert r.json()["error"]["detail"]["required_permission"] == "agent.publish"
    async with tenant_session(ws) as s:
        a = await s.get(Agent, uuid.UUID(aid))
        rows = list((await s.scalars(select(AuditLog))).all())
    assert str(a.active_version_id) == v2 and rows == []
