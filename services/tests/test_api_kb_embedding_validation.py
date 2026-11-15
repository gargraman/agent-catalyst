"""KB embedding-model + chunk_config validation, derived ingestion status, staleness (feature 024, tasks.md T024/T031;
research D3; data-model §1; spec FR-011/FR-031).

* `embedding_model` must name an EMBEDDING profile of the single deployment dimension (1024); `embedding_dim` is derived
  (a disagreeing client value is refused). Refusals list the supported models.
* `chunk_config` is validated by `ChunkConfig.from_dict` (100 ≤ size ≤ 2000, 0 ≤ overlap < size/2).
* Changing embedding_model/chunk_config marks ready documents `stale` (KB `ingestion_status = "stale"`) and keeps the
  existing drift behaviour (bound versions flagged needs_reeval). Content refresh alone does NOT flag (signal parked).
Only one embedding profile ships (text-embedding-3-small), so the stale path is exercised through chunk_config.
"""
from __future__ import annotations

import uuid as _uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from api.db import tenant_session
from api.ingest_executor import get_executor
from api.models import Agent, KbDocument
from api.settings import get_settings
from api.storage import get_storage
from runtime.providers import transport
from tests._api_helpers import client, dev_headers, seed_agent, seed_retrieval_binding, seed_version, seed_workspace

pytestmark = pytest.mark.integration

DOCS = Path(__file__).parent / "fixtures" / "docs"


def _kb(**over) -> dict:
    body = {"slug": f"kb-{_uuid.uuid4().hex[:6]}", "name": "KB", "embedding_model": "text-embedding-3-small",
            "chunk_config": {"size": 800, "overlap": 100}}
    body.update(over)
    return body


@pytest.fixture(autouse=True)
def _stubs(monkeypatch):
    monkeypatch.setenv("AC_STORAGE_PROVIDER", "local")
    get_settings.cache_clear()
    get_storage.cache_clear()

    async def _embed(**kw):
        inp = kw["input"]
        return SimpleNamespace(data=[{"index": i, "embedding": [0.01] * 1024} for i in range(len(inp))],
                               usage=SimpleNamespace(prompt_tokens=10 * len(inp)), _hidden_params={})
    monkeypatch.setattr(transport, "embed", _embed)
    yield
    get_settings.cache_clear()
    get_storage.cache_clear()


async def test_create_with_supported_embedding_model_derives_dim():
    ws = await seed_workspace()
    async with client() as c:
        r = await c.post("/api/v1/knowledge-bases", headers=dev_headers(workspace=ws), json=_kb())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["embedding_dim"] == 1024 and body["embedding_provider"] == "openai"
    assert body["ingestion_status"] == "empty" and body["document_count"] == 0 and body["chunk_count"] == 0
    assert body["last_ingested_at"] is None


@pytest.mark.parametrize("model", ["gpt-5", "nope-embedding"])     # a chat model is not an embedder; unknown model
async def test_create_with_unsupported_model_is_400_listing_supported(model):
    ws = await seed_workspace()
    async with client() as c:
        r = await c.post("/api/v1/knowledge-bases", headers=dev_headers(workspace=ws), json=_kb(embedding_model=model))
    assert r.status_code == 400 and r.json()["error"]["code"] == "input.invalid"
    assert r.json()["error"]["detail"]["supported_models"] == ["amazon.titan-embed-text-v2:0", "text-embedding-3-small"]


async def test_create_with_disagreeing_dim_is_400():
    ws = await seed_workspace()
    async with client() as c:
        r = await c.post("/api/v1/knowledge-bases", headers=dev_headers(workspace=ws), json=_kb(embedding_dim=1536))
    assert r.status_code == 400


@pytest.mark.parametrize("cfg", [{"size": 50}, {"size": 3000}, {"size": 800, "overlap": 500}])
async def test_create_with_invalid_chunk_config_is_400(cfg):
    ws = await seed_workspace()
    async with client() as c:
        r = await c.post("/api/v1/knowledge-bases", headers=dev_headers(workspace=ws), json=_kb(chunk_config=cfg))
    assert r.status_code == 400


async def test_patch_to_unsupported_model_is_400():
    ws = await seed_workspace()
    async with client() as c:
        created = await c.post("/api/v1/knowledge-bases", headers=dev_headers(workspace=ws), json=_kb())
        r = await c.patch(f"/api/v1/knowledge-bases/{created.json()['id']}",
                          headers=dev_headers(workspace=ws, if_match=created.headers["ETag"]),
                          json={"embedding_model": "gpt-5"})
    assert r.status_code == 400 and r.json()["error"]["detail"]["supported_models"] == ["amazon.titan-embed-text-v2:0", "text-embedding-3-small"]


async def test_chunk_config_change_marks_ready_docs_stale_and_flags_bound_versions():
    ws = await seed_workspace()
    agent = await seed_agent(ws)
    v = await seed_version(ws, agent)
    async with client() as c:
        created = await c.post("/api/v1/knowledge-bases", headers=dev_headers(workspace=ws), json=_kb())
        kid = created.json()["id"]
        await seed_retrieval_binding(ws, v, kid)
        up = await c.post(f"/api/v1/knowledge-bases/{kid}/documents", headers=dev_headers(workspace=ws),
                          files={"file": ("faq.txt", (DOCS / "faq.txt").read_bytes(), "text/plain")})
        assert up.status_code == 202
        await get_executor().drain()
        ready = (await c.get(f"/api/v1/knowledge-bases/{kid}", headers=dev_headers(workspace=ws)))
        assert ready.json()["ingestion_status"] == "ready"
        assert await _needs_reeval(ws, agent) is False             # content refresh alone never flags (FR-031)
        r = await c.patch(f"/api/v1/knowledge-bases/{kid}", headers=dev_headers(workspace=ws, if_match=ready.headers["ETag"]),
                          json={"chunk_config": {"size": 500, "overlap": 50}})
        assert r.status_code == 200
        after = (await c.get(f"/api/v1/knowledge-bases/{kid}", headers=dev_headers(workspace=ws))).json()
    assert after["ingestion_status"] == "stale"
    async with tenant_session(ws) as s:
        statuses = {st for (st,) in (await s.execute(select(KbDocument.status))).all()}
    assert statuses == {"stale"}
    assert await _needs_reeval(ws, agent) is True


async def _needs_reeval(ws: str, agent_id: str) -> bool:
    async with tenant_session(ws) as s:
        return (await s.execute(select(Agent.needs_reeval).where(Agent.id == _uuid.UUID(agent_id)))).scalar_one()



# ---- decision 2026-11-14 (T083): provider policy enforced in code -------------------------------------------------
async def test_bedrock_only_policy_refuses_openai_and_accepts_titan(monkeypatch):
    monkeypatch.setenv("AC_EMBEDDING_ALLOWED_PROVIDERS", "bedrock")
    get_settings.cache_clear()
    ws = await seed_workspace()
    async with client() as c:
        openai_kb = await c.post("/api/v1/knowledge-bases", headers=dev_headers(workspace=ws), json=_kb())
        titan_kb = await c.post("/api/v1/knowledge-bases", headers=dev_headers(workspace=ws),
                                json=_kb(embedding_model="amazon.titan-embed-text-v2:0"))
    assert openai_kb.status_code == 400
    assert openai_kb.json()["error"]["detail"]["supported_models"] == ["amazon.titan-embed-text-v2:0"]
    assert titan_kb.status_code == 201 and titan_kb.json()["embedding_provider"] == "bedrock"
    assert titan_kb.json()["embedding_dim"] == 1024
