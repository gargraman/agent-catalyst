"""Retrieval through both invoke planes (feature 024, tasks.md T040/T048; contracts/api.md "Invoke"; spec US2, FR-016..
FR-020; analyze H2).

Ingest `handbook.md` through the real upload path, bind it always-on, invoke on the console (playground) plane and the
published caller-token plane: one `citations` frame (between text and usage) naming the "27 days" chunk, identical on
both planes, `knowledge_status` only on the console plane, denied KBs masked, retrieval facts persisted on the run with
NO passage text anywhere, embedding cost inside the run cost, and a missing embedding key degrading (never failing) the
run. NO live calls: chat gateway and embedding transport are stubbed (deterministic bag-of-words vectors).
"""
from __future__ import annotations

import logging
import uuid as _uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from api.db import tenant_session
from api.ingest_executor import get_executor
from api.models import Agent, AgentVersion, KnowledgeBase, RetrievalBinding, Run
from api.settings import get_settings
from api.storage import get_storage
from runtime.providers import gateway, transport
from tests._api_helpers import (
    _IO_CONTRACT, _MODEL_CONFIG, PROFILE_SNAPSHOT,
    client, dev_headers, parse_sse, seed_agent, seed_api_token, seed_kb, seed_provider_key,
    seed_version, seed_workspace,
)
from tests._invoke_helpers import make_gateway, priced_usage, stop_ev, text_ev, usage_ev
from tests.test_retrieval_quality import _embed_text

pytestmark = pytest.mark.integration

DOCS = Path(__file__).parent / "fixtures" / "docs"
QUESTION = "How many days of annual leave do I get per year?"
FACT = "Annual leave is 27 days"
_CFG = {"top_k": 6, "similarity_threshold": 0.0, "max_ctx_tokens": 2000}   # 0.0: BoW fake ≠ real-model cosine scale
_RESTRICTED = {"visibility": "restricted", "useRoles": ["admin"], "manageRoles": ["admin"]}


@pytest.fixture(autouse=True)
def _stubs(monkeypatch):
    monkeypatch.setenv("AC_STORAGE_PROVIDER", "local")
    get_settings.cache_clear()
    get_storage.cache_clear()

    async def _embed(**kw):            # 50k tokens/input: embedding cost stays visible at 6-dp storage precision
        inp = kw["input"]
        return SimpleNamespace(data=[{"index": i, "embedding": list(_embed_text(t))} for i, t in enumerate(inp)],
                               usage=SimpleNamespace(prompt_tokens=50_000 * len(inp)), _hidden_params={})
    monkeypatch.setattr(transport, "embed", _embed)
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("You get 27 days."),
                                                         usage_ev(priced_usage(cost_usd=0.000132)), stop_ev()]))
    yield
    get_settings.cache_clear()
    get_storage.cache_clear()


async def _ingest(c, ws, kb, name="handbook.md", hdr=None):
    r = await c.post(f"/api/v1/knowledge-bases/{kb}/documents", headers=hdr or dev_headers(workspace=ws),
                     files={"file": (name, (DOCS / name).read_bytes(), "application/octet-stream")})
    assert r.status_code == 202, r.text
    await get_executor().drain()


async def _bound_version(c, ws, aid, kbs, hdr=None) -> str:
    h = hdr or dev_headers(workspace=ws)
    v = await c.post(f"/api/v1/agents/{aid}/versions", headers=h,
                     json={"retrieval_bindings": [{"kb_id": k, "injection": "always_on", "retrieval_config": _CFG}
                                                  for k in kbs]})
    assert v.status_code == 201, v.text
    etag = (await c.get(f"/api/v1/agents/{aid}", headers=h)).headers["ETag"]
    rp_h = hdr or dev_headers(workspace=ws, claims={ws: "publisher"})       # re-point = publisher (026 #9)
    act = await c.patch(f"/api/v1/agents/{aid}", headers={**rp_h, "If-Match": etag},
                        json={"active_version_id": v.json()["id"]})
    assert act.status_code == 200, act.text
    return v.json()["id"]


async def _setup():
    ws = await seed_workspace()
    aid = await seed_agent(ws)
    await seed_version(ws, aid)
    kb = await seed_kb(ws, slug="handbook-kb")
    return ws, aid, kb


def _frames(resp):
    return parse_sse(resp.text)


def _one(frames, name):
    hits = [d for e, d in frames if e == name]
    assert len(hits) == 1, f"expected one {name!r} frame, got {len(hits)}: {[e for e, _ in frames]}"
    return hits[0]


async def test_playground_citations_frame_names_the_fact_chunk():
    ws, aid, kb = await _setup()
    async with client() as c:
        await _ingest(c, ws, kb)
        await _bound_version(c, ws, aid, [kb])
        r = await c.post(f"/api/v1/agents/{aid}/invoke", headers=dev_headers(workspace=ws),
                         json={"inputs": {"message": QUESTION}})
    frames = _frames(r)
    names = [e for e, _ in frames]
    assert names[0] == "run_start" and "text_delta" in names
    ci = names.index("citations")
    assert max(i for i, n in enumerate(names) if n == "text_delta") < ci < names.index("usage") < names.index("done")
    cit = _one(frames, "citations")
    assert cit["knowledge_used"] is True and cit["knowledge_status"] == [{"kb_id": kb, "status": "used"}]
    assert any(x["document_name"] == "handbook.md" and x["heading"] == "Leave Policy" for x in cit["citations"])
    assert FACT not in r.text                                    # citations carry references, never passage text


async def test_published_plane_identical_citations_without_knowledge_status():
    ws, aid, kb = await _setup()
    bearer = "ac_" + "live_" + "rag_" + _uuid.uuid4().hex[:10]
    async with client() as c:
        await _ingest(c, ws, kb)
        await _bound_version(c, ws, aid, [kb])
        await seed_api_token(ws, aid, secret=bearer)
        console = await c.post(f"/api/v1/agents/{aid}/invoke", headers=dev_headers(workspace=ws),
                               json={"inputs": {"message": QUESTION}})
        published = await c.post(f"/v1/agents/{aid}/invoke", headers={"Authorization": "Bearer " + bearer},
                                 json={"inputs": {"message": QUESTION}})
    assert published.status_code == 200, published.text
    c_cit, p_cit = _one(_frames(console), "citations"), _one(_frames(published), "citations")
    assert p_cit["citations"] == c_cit["citations"] and p_cit["knowledge_used"] is True
    assert "knowledge_status" not in p_cit


async def test_denied_kb_is_masked_and_contributes_nothing():
    ws, aid, kb = await _setup()
    secret = await seed_kb(ws, slug="payroll-secrets", access_policy=_RESTRICTED)
    adm = dev_headers(workspace=ws, subject="adm", claims={ws: "admin"})
    async with client() as c:
        await _ingest(c, ws, kb)
        await _ingest(c, ws, secret, name="faq.txt", hdr=adm)
        await _bound_version(c, ws, aid, [kb, secret], hdr=adm)
        r = await c.post(f"/api/v1/agents/{aid}/invoke", headers=dev_headers(workspace=ws, subject="b"),
                         json={"inputs": {"message": QUESTION}})
    cit = _one(_frames(r), "citations")
    assert {x["kb_id"] for x in cit["citations"]} == {kb}
    assert {"kb_id": None, "status": "unavailable", "restricted": True} in cit["knowledge_status"]
    assert secret not in r.text and "Payroll-Secrets" not in r.text and "faq.txt" not in r.text
    assert _one(_frames(r), "done")["status"] == "success"


async def test_run_row_carries_retrieval_facts_and_embedding_cost_but_no_text(caplog):
    ws, aid, kb = await _setup()
    caplog.set_level(logging.INFO, logger="ac.call_log")
    async with client() as c:
        await _ingest(c, ws, kb)
        vid = await _bound_version(c, ws, aid, [kb])
        r = await c.post(f"/api/v1/agents/{aid}/invoke", headers=dev_headers(workspace=ws),
                         json={"inputs": {"message": QUESTION}})
    assert _one(_frames(r), "done")["status"] == "success"
    async with tenant_session(ws) as s:          # the sink does not reuse the stream's run_id as the row id
        run = (await s.execute(select(Run).where(Run.agent_id == _uuid.UUID(aid)))).scalar_one()
    retrieval = run.usage["retrieval"]
    assert retrieval["embed_calls"] == 1 and retrieval["bindings"][0]["kb_id"] == kb
    assert retrieval["bindings"][0]["status"] == "used" and retrieval["bindings"][0]["passages_used"] >= 1
    assert FACT not in str(run.usage) and FACT not in caplog.text
    assert Decimal(str(run.cost_usd)) > Decimal("0.000132")   # turn cost + embedding cost
    assert str(run.version_id) == vid


async def test_missing_embedding_key_degrades_but_run_succeeds():
    ws, aid, kb = await _setup()
    async with client() as c:
        await _ingest(c, ws, kb)                                 # ingested while a key still resolves
    # versions are immutable: insert a NEW version that chats on another provider and binds the KB
    async with tenant_session(ws) as s:
        kbrow = await s.get(KnowledgeBase, _uuid.UUID(kb))
        v = AgentVersion(workspace_id=_uuid.UUID(ws), agent_id=_uuid.UUID(aid), label="v-anthropic",
                         system_prompt="You are a test agent.", prompt_hash="ph", snapshot_hash="sh-anthropic",
                         model_config_={**_MODEL_CONFIG, "provider": "anthropic", "model": "claude-haiku-4-5"},
                         profile_snapshot=PROFILE_SNAPSHOT, io_contract=_IO_CONTRACT)
        v.retrieval_bindings = [RetrievalBinding(workspace_id=_uuid.UUID(ws), kb_id=kbrow.id,
                                                 kb_index_version=kbrow.index_version, retrieval_config=dict(_CFG),
                                                 injection="always_on")]
        s.add(v)
        await s.flush()
        (await s.get(Agent, _uuid.UUID(aid))).active_version_id = v.id
    await seed_provider_key(ws, provider="openai", vault_path="local://nowhere/missing/1")
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/invoke", headers=dev_headers(workspace=ws),
                         json={"inputs": {"message": QUESTION}})
    assert r.status_code == 200, r.text
    cit = _one(_frames(r), "citations")
    assert cit["knowledge_used"] is False and cit["knowledge_status"] == [{"kb_id": kb, "status": "degraded"}]
    assert _one(_frames(r), "done")["status"] == "success"


async def test_eval_start_gate_refuses_a_kb_the_caller_cannot_use():
    """analyze H1 (contracts/api.md "Evaluations"): before any case runs, the caller must be able to USE every KB bound
    to the version — else 403 eval.knowledge_forbidden, no run row, KB id not echoed."""
    from tests.test_api_evals_publish import _eval_body
    ws, aid, _ = await _setup()
    secret = await seed_kb(ws, slug="payroll-secrets", access_policy=_RESTRICTED)
    adm = dev_headers(workspace=ws, subject="adm", claims={ws: "admin"})
    async with client() as c:
        vid = await _bound_version(c, ws, aid, [secret], hdr=adm)
        r = await c.post(f"/api/v1/agents/{aid}/eval-runs", json=await _eval_body(ws, aid, vid),
                         headers=dev_headers(workspace=ws, subject="p", claims={ws: "publisher"}))
    assert r.status_code == 403 and r.json()["error"]["code"] == "eval.knowledge_forbidden", r.text
    assert secret not in r.text
    async with tenant_session(ws) as s:
        assert (await s.execute(select(Run).where(Run.source == "eval"))).first() is None
