"""Feature 026 (decision #9) — the server enforces the console's lifecycle gates.

The console gated archive/restore on `agent.delete` (admin) and re-point on `agent.publish` (publisher), but the
PATCH route only required `agent.edit` (builder+): a builder could bypass the UI and archive or re-point an agent
with a direct API call. A gate that exists only in the browser isn't a gate.
"""
from __future__ import annotations

import pytest

from tests._api_helpers import client, dev_headers, seed_workspace

pytestmark = pytest.mark.integration

IO = {"input_schema": {"type": "object", "properties": {"message": {"type": "string"}}}, "output_format": "text"}


def _h(ws, role, etag=None):
    return dev_headers(workspace=ws, claims={ws: role}, if_match=etag)


async def _agent_with_two_versions(c, ws):
    a = (await c.post("/api/v1/agents", json={"name": "A", "provider": "openai", "model": "gpt-5-mini",
                                              "system_prompt": "x", "io_contract": IO}, headers=_h(ws, "admin"))).json()
    v2 = (await c.post(f"/api/v1/agents/{a['id']}/versions", json={}, headers=_h(ws, "admin"))).json()
    cur = await c.get(f"/api/v1/agents/{a['id']}", headers=_h(ws, "admin"))
    return a["id"], v2["id"], cur.json()["active_version_id"]


async def _etag(c, ws, aid):
    return (await c.get(f"/api/v1/agents/{aid}", headers=_h(ws, "admin"))).headers["ETag"].strip('"')


async def test_archive_and_restore_require_admin():
    ws = await seed_workspace()
    async with client() as c:
        aid, _, _ = await _agent_with_two_versions(c, ws)
        for role in ("builder", "publisher"):
            r = await c.patch(f"/api/v1/agents/{aid}", json={"status": "archived"},
                              headers=_h(ws, role, await _etag(c, ws, aid)))
            assert r.status_code == 403, (role, r.text)
            assert r.json()["error"]["code"] == "access.denied"
            assert r.json()["error"]["detail"]["required_permission"] == "agent.delete"
            assert (await c.get(f"/api/v1/agents/{aid}", headers=_h(ws, "admin"))).json()["status"] != "archived"
        r = await c.patch(f"/api/v1/agents/{aid}", json={"status": "archived"},
                          headers=_h(ws, "admin", await _etag(c, ws, aid)))
        assert r.status_code == 200, r.text
        restore = r.json()["restore_status"]
        r = await c.patch(f"/api/v1/agents/{aid}", json={"status": restore},
                          headers=_h(ws, "publisher", await _etag(c, ws, aid)))
        assert r.status_code == 403, r.text                              # restore is the same lifecycle gate
        assert r.json()["error"]["detail"]["required_permission"] == "agent.delete"
        assert (await c.get(f"/api/v1/agents/{aid}", headers=_h(ws, "admin"))).json()["status"] == "archived"
        r = await c.patch(f"/api/v1/agents/{aid}", json={"status": restore},
                          headers=_h(ws, "admin", await _etag(c, ws, aid)))
        assert r.status_code == 200, r.text


async def test_repoint_requires_publisher_but_builder_can_still_edit():
    ws = await seed_workspace()
    async with client() as c:
        aid, v2, active = await _agent_with_two_versions(c, ws)
        assert v2 != active
        r = await c.patch(f"/api/v1/agents/{aid}", json={"active_version_id": v2},
                          headers=_h(ws, "builder", await _etag(c, ws, aid)))
        assert r.status_code == 403, r.text
        assert r.json()["error"]["code"] == "access.denied"
        assert r.json()["error"]["detail"]["required_permission"] == "agent.publish"
        assert (await c.get(f"/api/v1/agents/{aid}", headers=_h(ws, "admin"))).json()["active_version_id"] == active
        r = await c.patch(f"/api/v1/agents/{aid}", json={"active_version_id": active},
                          headers=_h(ws, "builder", await _etag(c, ws, aid)))
        assert r.status_code == 200, r.text                              # naming the current version is a no-op
        r = await c.patch(f"/api/v1/agents/{aid}", json={"name": "B"},
                          headers=_h(ws, "builder", await _etag(c, ws, aid)))
        assert r.status_code == 200, r.text                              # ordinary edits stay builder+
        r = await c.patch(f"/api/v1/agents/{aid}", json={"active_version_id": v2},
                          headers=_h(ws, "publisher", await _etag(c, ws, aid)))
        assert r.status_code == 200, r.text
        assert r.json()["active_version_id"] == v2
