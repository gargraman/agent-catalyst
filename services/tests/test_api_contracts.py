"""Cross-cutting API contract tests (M4, feature 005) — error envelope, OpenAPI 3.1, auth. No DB, no mocks."""
from __future__ import annotations

from tests._api_helpers import client, dev_headers


async def test_no_principal_returns_401_envelope():
    async with client() as c:
        r = await c.get("/api/v1/agents", headers={"X-Workspace-Id": "ws_1"})
    assert r.status_code == 401
    body = r.json()
    assert body["error"]["code"] == "auth.required"
    assert set(body["error"]) >= {"code", "message"}            # FR-011 envelope shape


async def test_unclaimed_workspace_returns_403():
    async with client() as c:
        r = await c.get("/api/v1/agents", headers=dev_headers(workspace="ws_x", claims={"ws_y": "builder"}))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "access.denied"         # FR-007


async def test_missing_workspace_selection_is_400():
    async with client() as c:
        r = await c.get("/api/v1/agents", headers={"X-Dev-Principal": '{"subject":"u","workspace_claims":{}}'})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "input.invalid"


async def test_openapi_is_3_1_and_lists_routes():
    async with client() as c:
        r = await c.get("/openapi.json")
    doc = r.json()
    assert doc["openapi"].startswith("3.1")                     # SC-004
    paths = doc["paths"]
    assert "/api/v1/agents" in paths
    assert "/api/v1/agents/{agent_id}/invoke" in paths
    assert "/api/v1/agents/{agent_id}/eval-runs" in paths


async def test_healthz_ok():
    async with client() as c:
        r = await c.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok"
