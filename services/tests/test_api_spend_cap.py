"""Feature 009 (M6 durable spend cap) — US1 enforcement + US2 allow-side (T004/T008).

Integration (compose Postgres); gateway mocked (no live/paid call). The gate must refuse an over-cap
workspace with 402 budget.exceeded BEFORE the provider is called and BEFORE any SSE frame — on both the
invoke and eval-run paths — and must not disturb under-cap work. Authz (403) precedes budget (402).
"""
from __future__ import annotations

import json
import uuid as _uuid
import datetime

import pytest
from sqlalchemy import func, select

from api.db import tenant_session
from api.models import Run
from runtime.providers import gateway
from tests._api_helpers import (
    client, dev_headers, seed_agent, seed_limits, seed_run, seed_version, seed_workspace,
)
from tests._invoke_helpers import stop_ev, text_ev, usage_ev
from tests._evals_helpers import VALID_OUTPUT, make_routed_gateway

pytestmark = pytest.mark.integration


def _tracking_stream():
    """A gateway.stream stand-in that records whether it was ever called (it must NOT be, on a refusal)."""
    calls = {"n": 0}

    def _stream(*a, **k):
        calls["n"] += 1

        async def _gen():
            yield text_ev("should-not-run")
            yield usage_ev()
            yield stop_ev()

        return _gen()

    return _stream, calls


def _eval_body(vid: str, n: int = 3) -> dict:
    cases = [{"id": f"c{i}", "input": "an XDR alert fired",
              "rubric": [{"name": "accuracy", "weight": 1.0}]} for i in range(n)]
    baseline = {"quality": 90.0, "cost_usd": 0.003, "latency_ms": 100,
                "per_case": {f"c{i}": json.dumps(VALID_OUTPUT) for i in range(n)}}
    return {"version_id": vid, "cases": cases, "baseline": baseline,
            "policy": {"min_cases": 3, "quality_threshold": 10.0, "efficiency_budget_pct": 10.0}}


async def _run_count(ws: str) -> int:
    async with tenant_session(ws) as s:
        return int((await s.execute(
            select(func.count()).select_from(Run).where(Run.workspace_id == _uuid.UUID(ws)))).scalar_one())


async def _seed_over_cap(cost: float = 15.0, cap: float = 10.0):
    """A workspace whose month-to-date spend (one seeded run) is >= a low cap."""
    ws = await seed_workspace()
    aid = await seed_agent(ws)
    vid = await seed_version(ws, aid)
    await seed_limits(ws, cap)
    await seed_run(ws, cost_usd=cost)
    return ws, aid, vid


# ---------------------------------------------------------------- US1: over-cap is blocked (T004)

async def test_over_cap_invoke_refused_402_before_provider_and_stream(monkeypatch):
    stream, calls = _tracking_stream()
    monkeypatch.setattr(gateway, "stream", stream)
    ws, aid, vid = await _seed_over_cap()
    before = await _run_count(ws)
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/invoke", headers=dev_headers(workspace=ws),
                         json={"inputs": {"message": "an alert fired"}})
    assert r.status_code == 402
    body = r.json()
    assert body["error"]["code"] == "budget.exceeded"
    assert "cap_usd" in body["error"]["detail"] and "month_to_date_usd" in body["error"]["detail"]
    assert calls["n"] == 0                         # provider never reached
    assert "event:" not in r.text                  # no SSE stream opened
    assert await _run_count(ws) == before          # no new run row for the refused call


async def test_over_cap_eval_run_refused_402_before_provider(monkeypatch):
    stream, calls = _tracking_stream()
    monkeypatch.setattr(gateway, "stream", stream)
    ws, aid, vid = await _seed_over_cap()
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/eval-runs",
                         headers=dev_headers(workspace=ws, claims={ws: "publisher"}), json=_eval_body(vid))
    assert r.status_code == 402
    assert r.json()["error"]["code"] == "budget.exceeded"
    assert calls["n"] == 0                          # eval never ran the engine/provider


async def test_authz_precedes_budget_insufficient_role_is_403_not_402(monkeypatch):
    stream, calls = _tracking_stream()
    monkeypatch.setattr(gateway, "stream", stream)
    ws, aid, vid = await _seed_over_cap()
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/invoke",
                         # an unknown role has no permissions (fail-closed, feature 008) → lacks playground.use
                         headers=dev_headers(workspace=ws, claims={ws: "norole"}),
                         json={"inputs": {"message": "x"}})
    assert r.status_code == 403                      # role check fires before the budget check
    assert r.json()["error"]["code"] == "access.denied"
    assert calls["n"] == 0


async def test_unheld_workspace_is_403_not_402(monkeypatch):
    stream, calls = _tracking_stream()
    monkeypatch.setattr(gateway, "stream", stream)
    ws, aid, vid = await _seed_over_cap()
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/invoke",
                         headers=dev_headers(workspace=ws, claims={"some-other-ws": "builder"}),
                         json={"inputs": {"message": "x"}})
    assert r.status_code == 403                      # membership (resolve_workspace) precedes budget


async def test_fail_closed_when_spend_indeterminate(monkeypatch):
    """FR-009: if month-to-date spend cannot be computed, refuse (no paid call) — never silently proceed."""
    stream, calls = _tracking_stream()
    monkeypatch.setattr(gateway, "stream", stream)
    ws = await seed_workspace()
    aid = await seed_agent(ws)
    vid = await seed_version(ws, aid)

    async def _boom(*a, **k):
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr("api.spend.month_to_date_spend", _boom)
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/invoke", headers=dev_headers(workspace=ws),
                         json={"inputs": {"message": "x"}})
    assert r.status_code == 500                             # fail-closed: controlled refusal, not a success
    assert r.json()["error"]["detail"]["reason"] == "spend_indeterminate"
    assert r.json()["error"]["code"] != "budget.exceeded"  # does not falsely claim the budget is spent
    assert calls["n"] == 0                                  # and the provider was never called


# ---------------------------------------------------------------- US2: under-cap unaffected (T008)

async def test_under_cap_invoke_proceeds_and_records(monkeypatch):
    monkeypatch.setattr(gateway, "stream",
                        lambda *a, **k: _ok_stream())
    ws = await seed_workspace()
    aid = await seed_agent(ws)
    vid = await seed_version(ws, aid)
    await seed_limits(ws, 2000)
    await seed_run(ws, cost_usd=1.0)                 # well under cap
    before = await _run_count(ws)
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/invoke", headers=dev_headers(workspace=ws),
                         json={"inputs": {"message": "an alert fired"}})
    assert r.status_code == 200
    assert "event: done" in r.text                  # streamed normally
    assert await _run_count(ws) == before + 1       # the invocation was recorded as usual


async def test_previous_month_spend_does_not_count(monkeypatch):
    monkeypatch.setattr(gateway, "stream", lambda *a, **k: _ok_stream())
    ws = await seed_workspace()
    aid = await seed_agent(ws)
    vid = await seed_version(ws, aid)
    await seed_limits(ws, 10)
    await seed_run(ws, cost_usd=1000.0, created_at=datetime.datetime(2000, 1, 1))  # prior month, over cap
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/invoke", headers=dev_headers(workspace=ws),
                         json={"inputs": {"message": "x"}})
    assert r.status_code == 200                      # this month's spend is 0 → under cap


async def test_unset_cap_uses_default_and_allows_small_spend(monkeypatch):
    monkeypatch.setattr(gateway, "stream", lambda *a, **k: _ok_stream())
    ws = await seed_workspace()
    aid = await seed_agent(ws)
    vid = await seed_version(ws, aid)
    await seed_run(ws, cost_usd=5.0)                 # no settings row → default 2000 cap applies
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/invoke", headers=dev_headers(workspace=ws),
                         json={"inputs": {"message": "x"}})
    assert r.status_code == 200


async def test_cross_tenant_spend_never_counts(monkeypatch):
    """P5: workspace B being over its cap must not block workspace A (spend is per-tenant)."""
    monkeypatch.setattr(gateway, "stream", lambda *a, **k: _ok_stream())
    ws_b = await seed_workspace()
    await seed_limits(ws_b, 10)
    await seed_run(ws_b, cost_usd=1000.0)           # B is way over its cap
    ws_a = await seed_workspace()
    aid = await seed_agent(ws_a)
    vid = await seed_version(ws_a, aid)
    await seed_limits(ws_a, 2000)
    await seed_run(ws_a, cost_usd=1.0)              # A is under cap
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/invoke", headers=dev_headers(workspace=ws_a),
                         json={"inputs": {"message": "x"}})
    assert r.status_code == 200                      # B's spend does not count toward A


def _ok_stream():
    async def _gen():
        yield text_ev("ok")
        yield usage_ev()
        yield stop_ev()
    return _gen()


# ---------------------------------------------------------------- feature 024 (T025): ingestion cost counts
async def test_ingestion_cost_alone_can_push_workspace_over_cap(monkeypatch):
    from tests._api_helpers import seed_ingestion_cost
    ws = await seed_workspace()
    aid = await seed_agent(ws)
    await seed_version(ws, aid)
    await seed_limits(ws, 10)
    await seed_ingestion_cost(ws, 12.0)                 # no runs at all — embedding spend alone exceeds the cap
    stream, calls = _tracking_stream()
    monkeypatch.setattr(gateway, "stream", stream)
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/invoke", headers=dev_headers(workspace=ws),
                         json={"inputs": {"message": "hi"}})
    assert r.status_code == 402 and r.json()["error"]["code"] == "budget.exceeded"
    assert calls["n"] == 0
