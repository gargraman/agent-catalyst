"""US1 — MCP server + exposed-tools CRUD + archive (feature 006)."""
from __future__ import annotations

import pytest

from tests._api_helpers import client, dev_headers, seed_workspace

pytestmark = pytest.mark.integration

_SERVER = {"name": "intel-mcp", "url": "https://mcp.example", "transport": "http"}


async def test_server_crud_with_nested_tools_and_archive():
    ws = await seed_workspace()
    async with client() as c:
        created = await c.post("/api/v1/mcp-servers", headers=dev_headers(workspace=ws), json=_SERVER)
        assert created.status_code == 201
        sid = created.json()["id"]
        # add an exposed tool
        mt = await c.post(f"/api/v1/mcp-servers/{sid}/tools", headers=dev_headers(workspace=ws),
                          json={"name": "lookup", "risk_tier": "read"})
        assert mt.status_code == 201 and mt.json()["server_id"] == sid
        # it shows nested on the server
        got = (await c.get(f"/api/v1/mcp-servers/{sid}", headers=dev_headers(workspace=ws))).json()
        assert any(t["name"] == "lookup" for t in got["tools"])
        # patch the exposed tool (If-Match required — F4)
        tid, tool_etag = mt.json()["id"], mt.headers["ETag"]
        pt = await c.patch(f"/api/v1/mcp-tools/{tid}", headers=dev_headers(workspace=ws, if_match=tool_etag),
                           json={"enabled": False})
        assert pt.status_code == 200 and pt.json()["enabled"] is False
        # archive the server → hidden from default list
        await c.post(f"/api/v1/mcp-servers/{sid}/archive", headers=dev_headers(workspace=ws))
        default = (await c.get("/api/v1/mcp-servers", headers=dev_headers(workspace=ws))).json()
    assert all(s["id"] != sid for s in default["items"])


async def test_mcp_tool_patch_requires_if_match_and_rejects_stale():
    """F4 — MCP patch enforces xmin If-Match like tools/skills (no silent lost update)."""
    ws = await seed_workspace()
    async with client() as c:
        sid = (await c.post("/api/v1/mcp-servers", headers=dev_headers(workspace=ws), json=_SERVER)).json()["id"]
        mt = await c.post(f"/api/v1/mcp-servers/{sid}/tools", headers=dev_headers(workspace=ws),
                          json={"name": "lookup"})
        tid, etag = mt.json()["id"], mt.headers["ETag"]
        no_match = await c.patch(f"/api/v1/mcp-tools/{tid}", headers=dev_headers(workspace=ws),
                                 json={"enabled": False})
        assert no_match.status_code == 400                   # If-Match required
        ok = await c.patch(f"/api/v1/mcp-tools/{tid}", headers=dev_headers(workspace=ws, if_match=etag),
                           json={"enabled": False})
        stale = await c.patch(f"/api/v1/mcp-tools/{tid}", headers=dev_headers(workspace=ws, if_match=etag),
                              json={"enabled": True})
    assert ok.status_code == 200 and stale.status_code == 409


async def test_duplicate_mcp_server_name_is_409():
    """F3 — duplicate server name → typed 409, not an unhandled 500."""
    ws = await seed_workspace()
    async with client() as c:
        await c.post("/api/v1/mcp-servers", headers=dev_headers(workspace=ws), json=_SERVER)
        dup = await c.post("/api/v1/mcp-servers", headers=dev_headers(workspace=ws), json=_SERVER)
    assert dup.status_code == 409 and dup.json()["error"]["code"].startswith("conflict")
