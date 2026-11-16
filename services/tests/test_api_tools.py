"""US1 — tool catalog CRUD + archive/restore + concurrency/idempotency + drift + no-secret (feature 006)."""
from __future__ import annotations

import uuid as _uuid

import pytest
from sqlalchemy import select

from api import deps
from api.db import tenant_session
from api.models import Agent
from tests._api_helpers import (
    client, dev_headers, seed_agent, seed_version, seed_workspace,
)

pytestmark = pytest.mark.integration

_TOOL = {"name": "Triage HTTP", "description": "calls triage",
         "http_config": {"endpoint": "https://x.example", "authMode": "shared_service"}, "risk_tier": "read"}


async def test_create_lists_and_scopes_to_workspace():
    ws = await seed_workspace()
    async with client() as c:
        r = await c.post("/api/v1/tools", headers=dev_headers(workspace=ws), json=_TOOL)
        assert r.status_code == 201 and r.headers.get("ETag")
        tid = r.json()["id"]
        lst = (await c.get("/api/v1/tools", headers=dev_headers(workspace=ws))).json()
    assert any(t["id"] == tid for t in lst["items"]) and "next_cursor" in lst


async def test_patch_if_match_optimistic_concurrency():
    ws = await seed_workspace()
    async with client() as c:
        created = await c.post("/api/v1/tools", headers=dev_headers(workspace=ws), json=_TOOL)
        etag = created.headers["ETag"]; tid = created.json()["id"]
        ok = await c.patch(f"/api/v1/tools/{tid}", headers=dev_headers(workspace=ws, if_match=etag),
                           json={"description": "renamed"})
        assert ok.status_code == 200 and ok.json()["description"] == "renamed"
        stale = await c.patch(f"/api/v1/tools/{tid}", headers=dev_headers(workspace=ws, if_match=etag),
                              json={"description": "again"})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "conflict.stale"


async def test_archive_hides_from_default_list_but_fetchable():
    ws = await seed_workspace()
    async with client() as c:
        tid = (await c.post("/api/v1/tools", headers=dev_headers(workspace=ws), json=_TOOL)).json()["id"]
        await c.post(f"/api/v1/tools/{tid}/archive", headers=dev_headers(workspace=ws))
        default = (await c.get("/api/v1/tools", headers=dev_headers(workspace=ws))).json()
        incl = (await c.get("/api/v1/tools?include_archived=true", headers=dev_headers(workspace=ws))).json()
        byid = await c.get(f"/api/v1/tools/{tid}", headers=dev_headers(workspace=ws))
    assert all(t["id"] != tid for t in default["items"])          # hidden by default
    assert any(t["id"] == tid for t in incl["items"])             # shown with filter
    assert byid.status_code == 200 and byid.json()["archived"] is True  # still fetchable by id


async def test_restore_returns_to_default_list():
    ws = await seed_workspace()
    async with client() as c:
        tid = (await c.post("/api/v1/tools", headers=dev_headers(workspace=ws), json=_TOOL)).json()["id"]
        await c.post(f"/api/v1/tools/{tid}/archive", headers=dev_headers(workspace=ws))
        await c.post(f"/api/v1/tools/{tid}/restore", headers=dev_headers(workspace=ws))
        default = (await c.get("/api/v1/tools", headers=dev_headers(workspace=ws))).json()
    assert any(t["id"] == tid for t in default["items"])


async def test_auth_mode_edit_flags_bound_agent_but_cosmetic_does_not():
    ws = await seed_workspace()
    agent = await seed_agent(ws)
    async with client() as c:
        created = await c.post("/api/v1/tools", headers=dev_headers(workspace=ws), json=_TOOL)
        tid, etag = created.json()["id"], created.headers["ETag"]
        await seed_version(ws, agent, tool_ids=[tid])   # 025: bindings are written with their version (0015)
        # cosmetic edit → no flag
        r1 = await c.patch(f"/api/v1/tools/{tid}", headers=dev_headers(workspace=ws, if_match=etag),
                           json={"description": "cosmetic"})
        assert await _needs_reeval(ws, agent) is False
        # auth-mode edit → flag
        await c.patch(f"/api/v1/tools/{tid}", headers=dev_headers(workspace=ws, if_match=r1.headers["ETag"]),
                      json={"http_config": {"endpoint": "https://x.example", "authMode": "per_user"}})
    assert await _needs_reeval(ws, agent) is True


async def test_secret_in_http_config_rejected():
    ws = await seed_workspace()
    bad = {**_TOOL, "http_config": {"endpoint": "https://x", "api_key": "sk-leak"}}
    async with client() as c:
        r = await c.post("/api/v1/tools", headers=dev_headers(workspace=ws), json=bad)
    assert r.status_code == 400 and r.json()["error"]["code"] == "input.invalid"


async def test_secret_in_nested_key_or_header_value_rejected():
    """F1 — a nested secret key AND an inlined auth header value are both rejected (not just top-level keys)."""
    ws = await seed_workspace()
    nested = {**_TOOL, "http_config": {"oauthApp": {"clientSecret": "shh"}}}
    header_val = {**_TOOL, "http_config": {"headers": "Authorization: Bearer sk-live-xyz"}}
    async with client() as c:
        r1 = await c.post("/api/v1/tools", headers=dev_headers(workspace=ws), json=nested)
        r2 = await c.post("/api/v1/tools", headers=dev_headers(workspace=ws), json=header_val)
    assert r1.status_code == 400 and r2.status_code == 400


async def test_duplicate_tool_name_is_409():
    """F3 — a duplicate tool name in a workspace → typed 409, not an unhandled 500."""
    ws = await seed_workspace()
    async with client() as c:
        await c.post("/api/v1/tools", headers=dev_headers(workspace=ws), json=_TOOL)
        dup = await c.post("/api/v1/tools", headers=dev_headers(workspace=ws), json=_TOOL)
    assert dup.status_code == 409 and dup.json()["error"]["code"].startswith("conflict")


async def test_create_honors_idempotency_key():
    ws = await seed_workspace()
    key = "tool-key-1"
    deps._INFLIGHT.add((ws, key))
    try:
        async with client() as c:
            r = await c.post("/api/v1/tools", headers=dev_headers(workspace=ws, idempotency_key=key), json=_TOOL)
        assert r.status_code == 409 and r.json()["error"]["code"] == "idempotency.in_progress"
    finally:
        deps._INFLIGHT.discard((ws, key))


async def _needs_reeval(ws: str, agent_id: str) -> bool:
    async with tenant_session(ws) as s:
        return (await s.execute(select(Agent).where(Agent.id == _uuid.UUID(agent_id)))).scalar_one().needs_reeval
