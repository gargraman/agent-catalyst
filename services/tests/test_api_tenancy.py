"""US2 — fail-closed tenant isolation at the API (M4, feature 005).

Unit: 401 (no principal) / 403 (unclaimed workspace) — reject before any DB access. Integration: cross-tenant
reads return 404 (never another tenant's row), via the real tenant_session + RLS. The ac_app-role + zero-rows
fail-closed guarantees are proven at the session layer by `test_session_tenancy.py` (M0) — this file covers the
API surface over that boundary.
"""
from __future__ import annotations

import pytest

from tests._api_helpers import client, dev_headers, seed_agent, seed_workspace


# ---- unit (no DB) --------------------------------------------------------------------------------
async def test_no_principal_is_401():
    async with client() as c:
        r = await c.get("/api/v1/agents", headers={"X-Workspace-Id": "ws_1"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "auth.required"


async def test_unclaimed_workspace_is_403():
    async with client() as c:
        r = await c.get("/api/v1/agents", headers=dev_headers(workspace="ws_x", claims={"ws_y": "builder"}))
    assert r.status_code == 403 and r.json()["error"]["code"] == "access.denied"


# ---- integration (compose DB) --------------------------------------------------------------------
@pytest.mark.integration
async def test_cross_tenant_get_is_404():
    ws_a = await seed_workspace()
    ws_b = await seed_workspace()
    agent_b = await seed_agent(ws_b, name="B-only")
    # caller authorized for A tries to read B's agent (scoped to A) → RLS hides it → 404, not B's data.
    async with client() as c:
        r = await c.get(f"/api/v1/agents/{agent_b}", headers=dev_headers(workspace=ws_a))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


@pytest.mark.integration
async def test_same_tenant_get_succeeds():
    ws = await seed_workspace()
    agent = await seed_agent(ws, name="mine")
    async with client() as c:
        r = await c.get(f"/api/v1/agents/{agent}", headers=dev_headers(workspace=ws))
    assert r.status_code == 200
    assert r.json()["id"] == agent and r.json()["workspace_id"] == ws
