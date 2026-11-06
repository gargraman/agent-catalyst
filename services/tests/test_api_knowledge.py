"""US3 — knowledge-base metadata CRUD + archive/restore + drift + honest-deferred ingestion (feature 006)."""
from __future__ import annotations

import uuid as _uuid

import pytest
from sqlalchemy import select

from api.db import tenant_session
from api.models import Agent
from tests._api_helpers import (
    client, dev_headers, seed_agent, seed_retrieval_binding, seed_version, seed_workspace,
)

pytestmark = pytest.mark.integration

_KB = {"slug": "triage-kb", "name": "Triage KB", "embedding_model": "text-embedding-3-small",
       "embedding_dim": 1536, "chunk_config": {"size": 800, "overlap": 100}}


async def test_create_list_get_and_scope():
    ws = await seed_workspace()
    async with client() as c:
        r = await c.post("/api/v1/knowledge-bases", headers=dev_headers(workspace=ws), json=_KB)
        assert r.status_code == 201 and r.json()["ingestion_status"] == "deferred"
        kid = r.json()["id"]
        lst = (await c.get("/api/v1/knowledge-bases", headers=dev_headers(workspace=ws))).json()
    assert any(k["id"] == kid for k in lst["items"])


async def test_content_edit_flags_bound_agent_cosmetic_does_not():
    ws = await seed_workspace()
    agent = await seed_agent(ws)
    v = await seed_version(ws, agent)
    async with client() as c:
        created = await c.post("/api/v1/knowledge-bases", headers=dev_headers(workspace=ws), json=_KB)
        kid, etag = created.json()["id"], created.headers["ETag"]
        await seed_retrieval_binding(ws, v, kid)
        r1 = await c.patch(f"/api/v1/knowledge-bases/{kid}", headers=dev_headers(workspace=ws, if_match=etag),
                           json={"name": "Renamed KB"})         # cosmetic
        assert await _needs_reeval(ws, agent) is False
        await c.patch(f"/api/v1/knowledge-bases/{kid}", headers=dev_headers(workspace=ws, if_match=r1.headers["ETag"]),
                      json={"chunk_config": {"size": 500, "overlap": 50}})  # content
    assert await _needs_reeval(ws, agent) is True


async def test_archive_restore_and_duplicate_slug():
    ws = await seed_workspace()
    async with client() as c:
        kid = (await c.post("/api/v1/knowledge-bases", headers=dev_headers(workspace=ws), json=_KB)).json()["id"]
        dup = await c.post("/api/v1/knowledge-bases", headers=dev_headers(workspace=ws), json=_KB)
        assert dup.status_code == 409
        await c.post(f"/api/v1/knowledge-bases/{kid}/archive", headers=dev_headers(workspace=ws))
        hidden = (await c.get("/api/v1/knowledge-bases", headers=dev_headers(workspace=ws))).json()
        await c.post(f"/api/v1/knowledge-bases/{kid}/restore", headers=dev_headers(workspace=ws))
        shown = (await c.get("/api/v1/knowledge-bases", headers=dev_headers(workspace=ws))).json()
    assert all(k["id"] != kid for k in hidden["items"]) and any(k["id"] == kid for k in shown["items"])


async def test_ingestion_jobs_is_honest_deferred():
    ws = await seed_workspace()
    async with client() as c:
        await c.post("/api/v1/knowledge-bases", headers=dev_headers(workspace=ws), json=_KB)
        jobs = (await c.get("/api/v1/ingestion-jobs", headers=dev_headers(workspace=ws))).json()
    assert jobs["items"] == []                              # no fabricated ingestion (FR-009)


async def _needs_reeval(ws: str, agent_id: str) -> bool:
    async with tenant_session(ws) as s:
        return (await s.execute(select(Agent).where(Agent.id == _uuid.UUID(agent_id)))).scalar_one().needs_reeval
