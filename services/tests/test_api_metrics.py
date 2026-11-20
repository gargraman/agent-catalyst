"""US5 — dashboard/runs/eval metrics over the workspace's runs (feature 006, FR-015/SC-009)."""
from __future__ import annotations

import pytest

from tests._api_helpers import client, dev_headers, seed_run, seed_workspace

pytestmark = pytest.mark.integration


async def test_empty_window_zero_filled():
    ws = await seed_workspace()
    async with client() as c:
        d = (await c.get("/api/v1/metrics/dashboard", headers=dev_headers(workspace=ws))).json()
    assert d["window_days"] == 30 and len(d["buckets"]) == 30 and len(d["spend"]) == 30
    assert d["total_calls"] == 0 and d["total_cost_usd"] == 0.0     # zero-filled, not an error


async def test_dashboard_excludes_eval_from_call_volume_and_sums_spend():
    ws = await seed_workspace()
    await seed_run(ws, source="playground", cost_usd=0.002)
    await seed_run(ws, source="published", cost_usd=0.003)
    await seed_run(ws, source="eval", cost_usd=0.001)             # excluded from call volume
    async with client() as c:
        d = (await c.get("/api/v1/metrics/dashboard", headers=dev_headers(workspace=ws))).json()
    assert d["total_calls"] == 2                                  # playground + published only
    assert abs(d["total_cost_usd"] - 0.006) < 1e-9               # all three contribute to spend


async def test_runs_and_eval_stats_reconcile():
    ws = await seed_workspace()
    await seed_run(ws, source="playground", outcome="ok")
    await seed_run(ws, source="eval", outcome="ok")
    await seed_run(ws, source="eval", outcome="error")
    async with client() as c:
        rs = (await c.get("/api/v1/metrics/runs", headers=dev_headers(workspace=ws))).json()
        es = (await c.get("/api/v1/metrics/evals", headers=dev_headers(workspace=ws))).json()
    assert rs["total"] == 3 and rs["by_source"].get("eval") == 2
    assert es["total_eval_runs"] == 2 and es["by_outcome"].get("error") == 1
