"""Feature 009 (M6 spend cap) — US3 spend observability (T009).

`GET /api/v1/workspace/spend` reports month-to-date spend vs the active cap, from the SAME helpers the gate
uses (SC-004). Integration (compose Postgres). RED until the route exists.
"""
from __future__ import annotations

import pytest

from tests._api_helpers import client, dev_headers, seed_limits, seed_run, seed_workspace

pytestmark = pytest.mark.integration


async def test_spend_view_reports_mtd_cap_and_remaining():
    ws = await seed_workspace()
    await seed_limits(ws, 500)
    await seed_run(ws, cost_usd=1.5)
    await seed_run(ws, cost_usd=2.25)
    async with client() as c:
        r = await c.get("/api/v1/workspace/spend", headers=dev_headers(workspace=ws, claims={ws: "viewer"}))
    assert r.status_code == 200
    body = r.json()
    assert body["monthToDateUsd"] == 3.75
    assert body["capUsd"] == 500.0
    assert body["remainingUsd"] == 496.25


async def test_spend_view_matches_enforcement_over_cap():
    """When over cap, the view shows mtd >= cap (remaining <= 0) — consistent with the gate's decision."""
    ws = await seed_workspace()
    await seed_limits(ws, 10)
    await seed_run(ws, cost_usd=15.0)
    async with client() as c:
        r = await c.get("/api/v1/workspace/spend", headers=dev_headers(workspace=ws, claims={ws: "viewer"}))
    assert r.status_code == 200
    body = r.json()
    assert body["monthToDateUsd"] == 15.0 and body["capUsd"] == 10.0
    assert body["remainingUsd"] <= 0


async def test_spend_view_requires_held_workspace_403():
    ws = await seed_workspace()
    async with client() as c:
        r = await c.get("/api/v1/workspace/spend",
                        headers=dev_headers(workspace=ws, claims={"other-ws": "viewer"}))
    assert r.status_code == 403
