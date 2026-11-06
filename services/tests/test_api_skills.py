"""US1 — skills catalog CRUD + publish (unsigned stub) + archive/restore + published-edit→draft (feature 006)."""
from __future__ import annotations

import uuid as _uuid

import pytest
from sqlalchemy import select

from api.db import tenant_session
from api.models import Agent
from tests._api_helpers import (
    client, dev_headers, seed_agent, seed_skill_binding, seed_version, seed_workspace,
)

pytestmark = pytest.mark.integration

_SKILL = {"slug": "triage-skill", "name": "Triage Skill", "instructions": "triage carefully", "resources": []}


async def test_crud_and_list():
    ws = await seed_workspace()
    async with client() as c:
        r = await c.post("/api/v1/skills", headers=dev_headers(workspace=ws), json=_SKILL)
        assert r.status_code == 201 and r.json()["content_hash"] and r.json()["status"] == "draft"
        sid = r.json()["id"]
        lst = (await c.get("/api/v1/skills", headers=dev_headers(workspace=ws))).json()
    assert any(s["id"] == sid for s in lst["items"])


async def test_publish_stamps_unsigned_provenance():
    ws = await seed_workspace()
    async with client() as c:
        sid = (await c.post("/api/v1/skills", headers=dev_headers(workspace=ws), json=_SKILL)).json()["id"]
        r = await c.post(f"/api/v1/skills/{sid}/publish", headers=dev_headers(workspace=ws))
    body = r.json()
    assert r.status_code == 200 and body["status"] == "published"
    assert body["provenance"] and body["provenance"]["signed"] is False and "by" in body["provenance"]


async def test_published_content_edit_reverts_to_draft_and_flags_bound_agent():
    ws = await seed_workspace()
    agent = await seed_agent(ws)
    v = await seed_version(ws, agent)
    async with client() as c:
        created = await c.post("/api/v1/skills", headers=dev_headers(workspace=ws), json=_SKILL)
        sid, etag = created.json()["id"], created.headers["ETag"]
        await seed_skill_binding(ws, v, sid)
        await c.post(f"/api/v1/skills/{sid}/publish", headers=dev_headers(workspace=ws))
        # content edit on a published skill → back to draft + drift flag
        get = await c.get(f"/api/v1/skills/{sid}", headers=dev_headers(workspace=ws))
        r = await c.patch(f"/api/v1/skills/{sid}", headers=dev_headers(workspace=ws, if_match=get.headers["ETag"]),
                          json={"instructions": "changed body"})
    assert r.status_code == 200 and r.json()["status"] == "draft"
    assert await _needs_reeval(ws, agent) is True


async def test_cosmetic_edit_keeps_published():
    ws = await seed_workspace()
    async with client() as c:
        created = await c.post("/api/v1/skills", headers=dev_headers(workspace=ws), json=_SKILL)
        sid = created.json()["id"]
        await c.post(f"/api/v1/skills/{sid}/publish", headers=dev_headers(workspace=ws))
        get = await c.get(f"/api/v1/skills/{sid}", headers=dev_headers(workspace=ws))
        r = await c.patch(f"/api/v1/skills/{sid}", headers=dev_headers(workspace=ws, if_match=get.headers["ETag"]),
                          json={"description": "just a description"})
    assert r.status_code == 200 and r.json()["status"] == "published"


async def test_archive_restore():
    ws = await seed_workspace()
    async with client() as c:
        sid = (await c.post("/api/v1/skills", headers=dev_headers(workspace=ws), json=_SKILL)).json()["id"]
        await c.post(f"/api/v1/skills/{sid}/archive", headers=dev_headers(workspace=ws))
        hidden = (await c.get("/api/v1/skills", headers=dev_headers(workspace=ws))).json()
        await c.post(f"/api/v1/skills/{sid}/restore", headers=dev_headers(workspace=ws))
        shown = (await c.get("/api/v1/skills", headers=dev_headers(workspace=ws))).json()
    assert all(s["id"] != sid for s in hidden["items"])
    assert any(s["id"] == sid for s in shown["items"])


async def test_duplicate_slug_is_409():
    """F3 — duplicate skill slug → typed 409, not 500."""
    ws = await seed_workspace()
    async with client() as c:
        await c.post("/api/v1/skills", headers=dev_headers(workspace=ws), json=_SKILL)
        dup = await c.post("/api/v1/skills", headers=dev_headers(workspace=ws), json=_SKILL)
    assert dup.status_code == 409 and dup.json()["error"]["code"].startswith("conflict")


async def test_cannot_publish_archived_skill():
    """F5 — publishing an archived skill is rejected (restore first)."""
    ws = await seed_workspace()
    async with client() as c:
        sid = (await c.post("/api/v1/skills", headers=dev_headers(workspace=ws), json=_SKILL)).json()["id"]
        await c.post(f"/api/v1/skills/{sid}/archive", headers=dev_headers(workspace=ws))
        r = await c.post(f"/api/v1/skills/{sid}/publish", headers=dev_headers(workspace=ws))
    assert r.status_code == 400 and r.json()["error"]["code"] == "input.invalid"


async def _needs_reeval(ws: str, agent_id: str) -> bool:
    async with tenant_session(ws) as s:
        return (await s.execute(select(Agent).where(Agent.id == _uuid.UUID(agent_id)))).scalar_one().needs_reeval
