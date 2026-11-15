"""The runtime retrieval step (feature 024, tasks.md T036; contracts/runtime.md §2; spec FR-012..FR-020).

`retrieve(query, refs, ctx) -> RetrievalStepResult` embeds the query once per distinct embedding model (via
gateway.embed), searches each bound KB through the injected `ctx.knowledge` port with that ref's top_k/threshold, merges
by score, trims to the SMALLEST max_ctx_tokens using whole passages, and reports an honest per-binding status. Any
failure degrades the binding — it never raises. Pure: fake port + stubbed gateway.embed, no DB/network.
(The result carries `embeddings`, the per-call EmbeddingResults, instead of mutating a shared state object.)
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from runtime.providers import gateway
from runtime.providers.ir import EmbeddingError, EmbeddingResult, NormalizedUsage
from runtime.retrieval.port import Passage, SearchOutcome
from runtime.retrieval.step import RetrievalStepResult, retrieve
from runtime.snapshot import RetrievalRef
from tests._invoke_helpers import make_context

_CFG = {"top_k": 6, "similarity_threshold": 0.2, "max_ctx_tokens": 2000}


def _ref(kb="kb-a", *, model="text-embedding-3-small", provider="openai", cfg=None, version=7, name=None):
    return RetrievalRef(kb_id=kb, embedding_model=model, retrieval_config=dict(cfg or _CFG), injection="always_on",
                        kb_index_version=version, embedding_provider=provider, kb_name=name or kb.upper())


def _passage(kb, n, score, tokens=100, **meta):
    return Passage(chunk_id=f"{kb}-c{n}", kb_id=kb, document_id=f"{kb}-d", document_name=f"{kb}.md",
                   text=f"passage {n} of {kb}", token_count=tokens, score=score, meta=meta)


class FakeKnowledge:
    def __init__(self, by_kb: dict):
        self.by_kb = by_kb
        self.calls = []

    async def search(self, ref, qvec, *, top_k, threshold):
        self.calls.append((ref.kb_id, len(qvec), top_k, threshold))
        out = self.by_kb.get(ref.kb_id, SearchOutcome("ok", ()))
        if isinstance(out, Exception):
            raise out
        return out


@pytest.fixture
def embeds(monkeypatch):
    calls = []

    async def _embed(texts, *, provider, model, api_key, timeout=None, region=None):
        calls.append({"texts": texts, "provider": provider, "model": model, "api_key": api_key, "region": region})
        return EmbeddingResult(vectors=tuple(tuple([0.1] * 1024) for _ in texts), usage=NormalizedUsage(5, 0),
                               cost_usd=Decimal("0.000001"), cost_is_estimate=True)
    monkeypatch.setattr(gateway, "embed", _embed)
    return calls


def _ctx(knowledge, **kw):
    return make_context(knowledge=knowledge, embedding_api_key=kw.pop("key", "sk-embed"), **kw)


async def test_one_embed_per_distinct_model_and_cost_reported(embeds):
    k = FakeKnowledge({"kb-a": SearchOutcome("ok", (_passage("kb-a", 1, 0.9),)),
                       "kb-b": SearchOutcome("ok", (_passage("kb-b", 1, 0.8),)),
                       "kb-c": SearchOutcome("ok", ())})
    refs = (_ref("kb-a"), _ref("kb-b"), _ref("kb-c", model="other-model"))
    res = await retrieve("How many leave days?", refs, _ctx(k))
    assert isinstance(res, RetrievalStepResult)
    assert sorted(c["model"] for c in embeds) == ["other-model", "text-embedding-3-small"]
    assert all(c["texts"] == ["How many leave days?"] and c["api_key"] == "sk-embed" for c in embeds)
    assert len(res.embeddings) == 2 and all(isinstance(e, EmbeddingResult) for e in res.embeddings)


async def test_search_gets_ref_bounds_and_results_merge_by_score(embeds):
    k = FakeKnowledge({"kb-a": SearchOutcome("ok", (_passage("kb-a", 1, 0.5), _passage("kb-a", 2, 0.4))),
                       "kb-b": SearchOutcome("ok", (_passage("kb-b", 1, 0.9),))})
    refs = (_ref("kb-a", cfg={**_CFG, "top_k": 2, "similarity_threshold": 0.3}), _ref("kb-b"))
    res = await retrieve("q", refs, _ctx(k))
    assert ("kb-a", 1024, 2, 0.3) in k.calls and ("kb-b", 1024, 6, 0.2) in k.calls
    assert [p.chunk_id for p in res.passages_used] == ["kb-b-c1", "kb-a-c1", "kb-a-c2"]
    assert [c.chunk_id for c in res.citations] == ["kb-b-c1", "kb-a-c1", "kb-a-c2"]
    assert [c.id for c in res.citations] == ["c1", "c2", "c3"]


async def test_trim_to_smallest_max_ctx_with_whole_passages(embeds):
    k = FakeKnowledge({"kb-a": SearchOutcome("ok", tuple(_passage("kb-a", i, 0.9 - i / 100, tokens=120) for i in range(5))),
                       "kb-b": SearchOutcome("ok", ())})
    refs = (_ref("kb-a", cfg={**_CFG, "max_ctx_tokens": 2000}), _ref("kb-b", cfg={**_CFG, "max_ctx_tokens": 300}))
    res = await retrieve("q", refs, _ctx(k))
    assert len(res.passages_used) == 2                          # 2 × 120 ≤ 300 < 3 × 120
    assert sum(p.token_count for p in res.passages_used) <= 300


async def test_binding_statuses_used_no_match_unavailable_degraded(embeds):
    k = FakeKnowledge({"kb-a": SearchOutcome("ok", (_passage("kb-a", 1, 0.77),)),
                       "kb-b": SearchOutcome("ok", ()),
                       "kb-c": SearchOutcome("unavailable"),
                       "kb-d": RuntimeError("db down")})
    res = await retrieve("q", (_ref("kb-a", version=3), _ref("kb-b"), _ref("kb-c"), _ref("kb-d")), _ctx(k))
    by = {s.kb_id: s for s in res.summaries}
    assert by["kb-a"].status == "used" and by["kb-a"].passages_used == 1 and by["kb-a"].top_score == pytest.approx(0.77)
    assert by["kb-a"].kb_index_version == 3
    assert by["kb-b"].status == "no_match" and by["kb-c"].status == "unavailable"
    assert by["kb-d"].status == "degraded" and by["kb-d"].degraded_reason == "search_failed"
    assert all(p.kb_id == "kb-a" for p in res.passages_used)


async def test_embedding_failure_degrades_every_binding_without_searching(monkeypatch):
    async def _boom(*a, **k):
        raise EmbeddingError("provider down", type="provider_error", retryable=True)
    monkeypatch.setattr(gateway, "embed", _boom)
    k = FakeKnowledge({})
    res = await retrieve("q", (_ref("kb-a"), _ref("kb-b")), _ctx(k))
    assert k.calls == [] and res.passages_used == () and res.rendered == ""
    assert {(s.status, s.degraded_reason) for s in res.summaries} == {("degraded", "embedding_failed")}


async def test_no_knowledge_port_degrades_without_embedding(embeds):
    res = await retrieve("q", (_ref("kb-a"),), make_context())        # ctx.knowledge is None
    assert embeds == []
    assert [(s.status, s.degraded_reason) for s in res.summaries] == [("degraded", "no_knowledge_access")]


async def test_missing_embedding_key_degrades_key_based_provider(embeds):
    res = await retrieve("q", (_ref("kb-a"),), _ctx(FakeKnowledge({}), key=None))
    assert embeds == []
    assert [(s.status, s.degraded_reason) for s in res.summaries] == [("degraded", "embedding_key_missing")]


async def test_bedrock_needs_no_key_and_gets_the_region(embeds):
    k = FakeKnowledge({"kb-a": SearchOutcome("ok", (_passage("kb-a", 1, 0.9),))})
    ref = _ref("kb-a", model="amazon.titan-embed-text-v2:0", provider="bedrock")
    res = await retrieve("q", (ref,), _ctx(k, key=None, embedding_region="eu-west-1"))
    assert embeds[0]["provider"] == "bedrock" and embeds[0]["api_key"] is None and embeds[0]["region"] == "eu-west-1"
    assert res.summaries[0].status == "used"


async def test_p2_levers_are_dropped_with_one_substitution_each(embeds):
    k = FakeKnowledge({})
    cfg = {**_CFG, "hybrid": True, "reranker": "bge", "metadata_filters": {"a": 1}}
    res = await retrieve("q", (_ref("kb-a", cfg=cfg), _ref("kb-b", cfg=cfg)), _ctx(k))
    feats = sorted((s.feature, s.action) for s in res.substitutions)
    assert feats == [("retrieval_hybrid", "dropped"), ("retrieval_metadata_filters", "dropped"),
                     ("retrieval_reranker", "dropped")]


async def test_citations_carry_page_and_heading_and_no_text(embeds):
    k = FakeKnowledge({"kb-a": SearchOutcome("ok", (_passage("kb-a", 1, 0.9, page=4, heading="Leave"),))})
    res = await retrieve("q", (_ref("kb-a"),), _ctx(k))
    (c,) = res.citations
    assert (c.page, c.heading, c.document_name, c.kb_id) == (4, "Leave", "kb-a.md", "kb-a")
    assert not hasattr(c, "text")
    assert "passage 1 of kb-a" in res.rendered and 'kb="KB-A"' in res.rendered
