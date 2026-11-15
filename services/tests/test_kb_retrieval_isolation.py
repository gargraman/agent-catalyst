"""Tenant-isolated, policy-aware kNN search (feature 024, tasks.md T039/T047; data-model §3; spec FR-014..FR-016, SC-003).

`api.knowledge_access.DbKnowledgeAccess(session, workspace_id, principal, *, bypass_access=False).search(ref, qvec, …)`
runs on the caller's RLS session with an explicit workspace filter (defence in depth), the frozen embedding-model
filter, live generations only, and the KB's CURRENT archive state + access policy (denied/archived → "unavailable",
indistinguishable). `bypass_access=True` (eval, after the H1 start gate) skips only the policy, never tenancy.
Integration (compose Postgres; PgBouncer path when AC_PGBOUNCER_URL is set).
"""
from __future__ import annotations

import datetime
import os
import uuid as _uuid

import pytest
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from api.access_policy import PolicyPrincipal
from api.db import set_workspace_scope, tenant_session
from api.knowledge_access import DbKnowledgeAccess
from api.models import KbChunk, KbDocument, KnowledgeBase
from runtime.snapshot import RetrievalRef
from tests._api_helpers import seed_kb, seed_workspace

pytestmark = pytest.mark.integration

MODEL = "text-embedding-3-small"
BUILDER = PolicyPrincipal(role="builder", teams=(), subject="b")


def _vec(i: int) -> list[float]:
    v = [0.0] * 1024
    v[i] = 1.0
    return v


def _blend(i: int, j: int, wi: float) -> list[float]:
    v = [0.0] * 1024
    v[i], v[j] = wi, (1 - wi * wi) ** 0.5
    return v


async def _seed_chunks(ws: str, kb: str, rows: list[tuple], *, active_generation: int = 1, name: str | None = None) -> str:
    """rows: (text, vector, generation, embedding_model). Returns the document id."""
    async with tenant_session(ws) as s:
        d = KbDocument(workspace_id=_uuid.UUID(ws), kb_id=_uuid.UUID(kb), name=name or f"{_uuid.uuid4().hex[:6]}.txt",
                       media_type="text/plain", size_bytes=1, content_hash="h", storage_key="k", status="ready",
                       active_generation=active_generation, created_by="t")
        s.add(d)
        await s.flush()
        for n, (txt, vec, gen, model) in enumerate(rows):
            s.add(KbChunk(workspace_id=d.workspace_id, kb_id=d.kb_id, document_id=d.id, generation=gen, ordinal=n,
                          text=txt, token_count=len(txt) // 3 + 1, embedding=vec, embedding_model=model,
                          meta={"heading": "H", "page": n + 1}))
        return str(d.id)


def _ref(kb: str, model: str = MODEL) -> RetrievalRef:
    return RetrievalRef(kb_id=kb, embedding_model=model, retrieval_config={}, injection="always_on",
                        kb_index_version=1, embedding_provider="openai", kb_name="KB")


async def _search(ws, kb, qvec, *, principal=BUILDER, bypass=False, top_k=6, threshold=0.0, model=MODEL):
    async with tenant_session(ws) as s:
        ka = DbKnowledgeAccess(s, ws, principal, bypass_access=bypass)
        return await ka.search(_ref(kb, model), qvec, top_k=top_k, threshold=threshold)


async def test_identical_vectors_never_cross_workspaces():
    ws_a, ws_b = await seed_workspace(), await seed_workspace()
    kb_a, kb_b = await seed_kb(ws_a), await seed_kb(ws_b)
    await _seed_chunks(ws_a, kb_a, [("SECRET A", _vec(3), 1, MODEL)])
    await _seed_chunks(ws_b, kb_b, [("own B", _vec(9), 1, MODEL)])
    out = await _search(ws_b, kb_a, _vec(3))                       # ws B probing ws A's KB id with A's exact vector
    assert out.passages == () and out.status == "unavailable"
    own = await _search(ws_b, kb_b, _vec(3))
    assert all("SECRET A" not in p.text for p in own.passages)


async def test_ordered_by_score_threshold_and_top_k():
    ws = await seed_workspace()
    kb = await seed_kb(ws)
    await _seed_chunks(ws, kb, [("best", _blend(1, 2, 0.95), 1, MODEL), ("mid", _blend(1, 2, 0.8), 1, MODEL),
                                ("low", _blend(1, 2, 0.3), 1, MODEL), ("other", _vec(7), 1, MODEL)])
    out = await _search(ws, kb, _vec(1), top_k=2, threshold=0.5)
    assert out.status == "ok"
    assert [p.text for p in out.passages] == ["best", "mid"]
    assert all(p.score >= 0.5 for p in out.passages) and out.passages[0].score > out.passages[1].score
    p = out.passages[0]
    assert p.kb_id == kb and p.document_name.endswith(".txt") and p.meta["heading"] == "H" and p.token_count > 0


async def test_other_embedding_model_and_dead_generations_excluded():
    ws = await seed_workspace()
    kb = await seed_kb(ws)
    await _seed_chunks(ws, kb, [("live", _vec(5), 2, MODEL), ("old-gen", _vec(5), 1, MODEL),
                                ("other-model", _vec(5), 2, "amazon.titan-embed-text-v2:0")], active_generation=2)
    await _seed_chunks(ws, kb, [("never-live", _vec(5), 1, MODEL)], active_generation=0)
    out = await _search(ws, kb, _vec(5))
    assert [p.text for p in out.passages] == ["live"]


async def test_archived_kb_is_unavailable():
    ws = await seed_workspace()
    kb = await seed_kb(ws)
    await _seed_chunks(ws, kb, [("x", _vec(2), 1, MODEL)])
    async with tenant_session(ws) as s:
        await s.execute(update(KnowledgeBase).where(KnowledgeBase.id == _uuid.UUID(kb))
                        .values(archived_at=datetime.datetime.now(datetime.timezone.utc)))
    out = await _search(ws, kb, _vec(2))
    assert out.status == "unavailable" and out.passages == ()


async def test_policy_denied_is_unavailable_and_bypass_only_skips_policy():
    ws, other = await seed_workspace(), await seed_workspace()
    kb = await seed_kb(ws, slug="restricted", access_policy={"visibility": "restricted", "useTeams": ["SOC"]})
    await _seed_chunks(ws, kb, [("restricted fact", _vec(4), 1, MODEL)])
    denied = await _search(ws, kb, _vec(4), principal=PolicyPrincipal(role="builder", teams=("IR",), subject="x"))
    allowed = await _search(ws, kb, _vec(4), principal=PolicyPrincipal(role="builder", teams=("SOC",), subject="y"))
    bypass = await _search(ws, kb, _vec(4), principal=None, bypass=True)
    assert denied.status == "unavailable" and denied.passages == ()
    assert [p.text for p in allowed.passages] == ["restricted fact"]
    assert [p.text for p in bypass.passages] == ["restricted fact"]
    cross = await _search(other, kb, _vec(4), principal=None, bypass=True)   # bypass never crosses tenants
    assert cross.passages == ()


@pytest.mark.skipif(not os.environ.get("AC_PGBOUNCER_URL"), reason="set AC_PGBOUNCER_URL to exercise the pooler path")
async def test_isolation_holds_through_pgbouncer():
    ws_a, ws_b = await seed_workspace(), await seed_workspace()
    kb_a = await seed_kb(ws_a)
    await _seed_chunks(ws_a, kb_a, [("SECRET A", _vec(6), 1, MODEL)])
    eng = create_async_engine(os.environ["AC_PGBOUNCER_URL"], pool_size=1, max_overflow=0)
    try:
        for ws in (ws_a, ws_b, ws_b):                       # same physical connection reused across tenants
            async with AsyncSession(eng) as s, s.begin():
                await set_workspace_scope(s, ws)
                out = await DbKnowledgeAccess(s, ws, None, bypass_access=True).search(
                    _ref(kb_a), _vec(6), top_k=5, threshold=0.0)
                assert (len(out.passages) == 1) is (ws == ws_a)
    finally:
        await eng.dispose()
