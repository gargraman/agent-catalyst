"""Feature 025 US4 — data-layer integrity refusals surface as TYPED API errors (contract api §1, research D12, FR-023).

The routes pre-check the common cases (a foreign version → 404 via `_version_belongs`, an unknown KB → 404 via
`_build_bindings`); these tests pin the RACE path where a pre-check passed but the database refuses at write time, so
the caller still gets a typed 4xx (never a raw 500), plus the ingestion and create-guard paths.

Integration (compose Postgres, local storage). No live provider calls: the embedding transport is stubbed.
"""
from __future__ import annotations

import uuid as _uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from api.db import tenant_session
from api.ingest_executor import get_executor
from api.models import IngestionJob
from api.routers import agents as agents_router
from api.settings import get_settings
from api.storage import get_storage
from runtime.providers import transport
from tests._api_helpers import client, dev_headers, seed_agent, seed_kb, seed_version, seed_workspace

pytestmark = pytest.mark.integration

def _hdr(ws, role="publisher"):
    return dev_headers(workspace=ws, claims={ws: role})


@pytest.fixture(autouse=True)
def _local_storage(monkeypatch):
    monkeypatch.setenv("AC_STORAGE_PROVIDER", "local")
    get_settings.cache_clear()
    get_storage.cache_clear()
    yield
    get_settings.cache_clear()
    get_storage.cache_clear()


@pytest.fixture
def embed(monkeypatch):
    async def stub(**kwargs):
        inp = kwargs["input"]
        return SimpleNamespace(data=[{"index": i, "embedding": [0.01] * 1024} for i, _ in enumerate(inp)],
                               usage=SimpleNamespace(prompt_tokens=5, total_tokens=5),
                               _hidden_params={"additional_headers": {}})
    monkeypatch.setattr(transport, "embed", stub)


async def _etag(c, ws, aid) -> str:
    r = await c.get(f"/api/v1/agents/{aid}", headers=_hdr(ws))
    assert r.status_code == 200, r.text
    return r.headers["ETag"]


# --------------------------------------------------------------------------- version create: KB vanishes mid-request


async def test_create_version_kb_deleted_after_precheck_is_404_not_500(monkeypatch):
    ws = await seed_workspace()
    aid = await seed_agent(ws)
    await seed_version(ws, aid)
    kb = await seed_kb(ws, slug=f"kb-{_uuid.uuid4().hex[:6]}")
    real = agents_router._build_bindings

    async def racing(session, raw, ap, w):
        out = await real(session, raw, ap, w)              # pre-check sees the KB …
        async with tenant_session(ws) as s:                # … then another transaction hard-deletes it
            await s.execute(text("delete from knowledge_bases where id = :k"), {"k": kb})
        return out

    monkeypatch.setattr(agents_router, "_build_bindings", racing)
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/versions", headers=_hdr(ws, "builder"),
                         json={"retrieval_bindings": [{"kb_id": kb}]})
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "not_found"


# --------------------------------------------------------------------------- foreign active version


async def test_publish_foreign_version_precheck_stays_404():
    """Existing contract (F2): the route pre-check answers 404 for a version of another agent (unchanged by 025)."""
    ws = await seed_workspace()
    a, b = await seed_agent(ws), await seed_agent(ws, name="other")
    await seed_version(ws, a)
    vb = await seed_version(ws, b)
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{a}/publish", headers=_hdr(ws), json={"version_id": vb})
    assert r.status_code == 404, r.text


async def test_publish_foreign_version_when_precheck_bypassed_is_400(monkeypatch):
    """Race/bypass path: the database's deferred same-agent FK refuses and the route maps it to 400 input.invalid."""
    from api.routers import evals as evals_router
    ws = await seed_workspace()
    a, b = await seed_agent(ws), await seed_agent(ws, name="other")
    await seed_version(ws, a)
    vb = await seed_version(ws, b)

    async def no_check(session, agent_id, version_id):
        return _uuid.UUID(version_id)

    monkeypatch.setattr(evals_router, "_version_belongs", no_check)
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{a}/publish", headers=_hdr(ws), json={"version_id": vb})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "input.invalid"
    async with tenant_session(ws) as s:                    # nothing was re-pointed
        av = (await s.execute(text("select active_version_id from agents where id = :a"), {"a": a})).scalar_one()
    assert str(av) != vb


async def test_patch_foreign_active_version_when_precheck_bypassed_is_400(monkeypatch):
    ws = await seed_workspace()
    a, b = await seed_agent(ws), await seed_agent(ws, name="other")
    await seed_version(ws, a)
    vb = await seed_version(ws, b)

    async def no_check(session, agent_id, version_id):
        return _uuid.UUID(str(version_id))

    monkeypatch.setattr(agents_router, "_version_belongs", no_check)
    async with client() as c:
        etag = await _etag(c, ws, a)
        r = await c.patch(f"/api/v1/agents/{a}", headers={**_hdr(ws), "If-Match": etag},
                          json={"active_version_id": vb})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "input.invalid"


# --------------------------------------------------------------------------- frozen binding via a real session


async def test_frozen_binding_update_maps_to_version_frozen():
    from api.db_errors import map_integrity_error
    ws = await seed_workspace()
    aid = await seed_agent(ws)
    await seed_version(ws, aid)
    kb = await seed_kb(ws, slug=f"kb-{_uuid.uuid4().hex[:6]}")
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/versions", headers=_hdr(ws, "builder"),
                         json={"retrieval_bindings": [{"kb_id": kb}]})
        assert r.status_code == 201, r.text
        vid = r.json()["id"]
    with pytest.raises(IntegrityError) as ei:
        async with tenant_session(ws) as s:
            await s.execute(text("update retrieval_bindings set kb_index_version = 9 where version_id = :v"),
                            {"v": vid})
    err = map_integrity_error(ei.value)
    assert err is not None and err.http_status == 409 and err.code == "version.frozen"


# --------------------------------------------------------------------------- ingestion: chunk dimension mismatch


async def test_ingest_into_kb_with_wrong_declared_dim_fails_job_typed(embed):
    ws = await seed_workspace()
    kb = await seed_kb(ws, slug=f"kb-{_uuid.uuid4().hex[:6]}")
    async with tenant_session(ws) as s:                    # legacy/dirty row: the API create guard now refuses this
        await s.execute(text("update knowledge_bases set embedding_dim = 768 where id = :k"), {"k": kb})
    async with client() as c:
        r = await c.post(f"/api/v1/knowledge-bases/{kb}/documents", headers=_hdr(ws, "builder"),
                         files={"file": ("a.txt", b"hello world. " * 40, "application/octet-stream")})
        assert r.status_code == 202, r.text
        await get_executor().drain()
    async with tenant_session(ws) as s:
        job = (await s.execute(select(IngestionJob).where(IngestionJob.kb_id == _uuid.UUID(kb)))).scalar_one()
    assert job.status == "failed"
    assert job.error["code"] == "chunk_dim_mismatch" and job.error["retryable"] is False


async def test_kb_create_with_non_deployment_dim_is_400():
    ws = await seed_workspace()
    async with client() as c:
        r = await c.post("/api/v1/knowledge-bases", headers=_hdr(ws, "builder"),
                         json={"slug": f"kb-{_uuid.uuid4().hex[:6]}", "name": "K",
                               "embedding_model": "text-embedding-3-small", "embedding_dim": 768})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "input.invalid"
