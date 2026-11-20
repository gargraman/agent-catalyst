"""Cross-cutting API contract tests (M4, feature 005) — error envelope, OpenAPI 3.1, auth. Mostly DB-free;
the unclaimed-workspace 403 needs the DB (feature 012 resolves an unclaimed role from the membership store),
so that one case is marked integration."""
from __future__ import annotations

import pytest

from tests._api_helpers import client, dev_headers


async def test_no_principal_returns_401_envelope():
    async with client() as c:
        r = await c.get("/api/v1/agents", headers={"X-Workspace-Id": "ws_1"})
    assert r.status_code == 401
    body = r.json()
    assert body["error"]["code"] == "auth.required"
    assert set(body["error"]) >= {"code", "message"}            # FR-011 envelope shape


# Integration: feature 012 made "workspace not in claims" fall through to a membership-store lookup
# (resolve_workspace -> _membership_role -> tenant_session), so confirming the 403 needs the DB to report
# no membership. Pre-012 this was a pure header check; the DB dependency is why it carries the marker.
@pytest.mark.integration
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


async def test_openapi_includes_feature_006_routes():
    """T019 — the generated contract includes every 006 resource route (source of truth for the FE client)."""
    async with client() as c:
        doc = (await c.get("/openapi.json")).json()
    paths = doc["paths"]
    for p in ("/api/v1/tools", "/api/v1/tools/{tool_id}", "/api/v1/mcp-servers", "/api/v1/skills",
              "/api/v1/knowledge-bases", "/api/v1/ingestion-jobs", "/api/v1/workspace/settings",
              "/api/v1/members", "/api/v1/runs", "/api/v1/metrics/dashboard"):
        assert p in paths, f"missing OpenAPI path: {p}"


def test_run_model_stores_no_raw_content():
    """T041/SC-009 — the runs table/model carries identity+metrics only, never raw prompt/response content."""
    from api.models import Run
    cols = set(Run.__table__.columns.keys())
    forbidden = {"prompt", "input", "output", "response", "content", "messages", "text", "completion"}
    assert cols & forbidden == set(), f"runs must not store raw content: {cols & forbidden}"


async def test_healthz_ok():
    async with client() as c:
        r = await c.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok"
