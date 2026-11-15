"""Always-on retrieval inside runtime.invoke (feature 024, tasks.md T038; contracts/runtime.md §3/§5/§6).

With an always-on binding the runtime retrieves ONCE before the first model turn (query = latest user message), inserts
the rendered context as a user-role message immediately before the latest user message (system prompt untouched),
keeps pass-through streaming live, emits exactly one CITATIONS event after the final content and before USAGE, records
retrieval facts + embedding cost on the RunRecord (cost = Σ turns + Σ embeddings, exactly), and one
ModelCallRecord(role="embedding") per embedding call. Retrieval failures never fail the run (FR-020). Pure: stub chat
gateway capturing the request, fake KnowledgeAccess, stub gateway.embed.
"""
from __future__ import annotations

import dataclasses
from decimal import Decimal

import pytest

from runtime.invoke import invoke, invoke_result
from runtime.providers import gateway
from runtime.providers.ir import EmbeddingError, EmbeddingResult, EventType, NormalizedUsage
from runtime.retrieval.port import Passage, SearchOutcome
from runtime.run import ModelCallRecord, Outcome
from runtime.snapshot import RetrievalRef
from tests._invoke_helpers import make_context, make_snapshot, priced_usage, stop_ev, text_ev, usage_ev

INPUTS = {"message": "How many days of annual leave do I get?"}
EMBED_COST = Decimal("0.000010")
TURN_USAGE = priced_usage(cost_usd=0.000132)


def _ref(kb="kb-h", injection="always_on", version=4):
    return RetrievalRef(kb_id=kb, embedding_model="text-embedding-3-small",
                        retrieval_config={"top_k": 6, "similarity_threshold": 0.2, "max_ctx_tokens": 2000},
                        injection=injection, kb_index_version=version, embedding_provider="openai", kb_name="Handbook")


PASSAGE = Passage(chunk_id="ch-27", kb_id="kb-h", document_id="doc-h", document_name="handbook.md",
                  text="Annual leave is 27 days per calendar year.", token_count=12, score=0.83,
                  meta={"heading": "Leave Policy", "page": None})


class FakeKnowledge:
    def __init__(self, outcome=None, exc=None):
        self.outcome = outcome if outcome is not None else SearchOutcome("ok", (PASSAGE,))
        self.exc = exc

    async def search(self, ref, qvec, *, top_k, threshold):
        if self.exc:
            raise self.exc
        return self.outcome


@pytest.fixture
def captured(monkeypatch):
    reqs: list = []

    def _stream(req, *, api_key, profile=None, timeout=None):
        reqs.append(req)

        async def _gen():
            yield text_ev("You get ")
            yield text_ev("27 days.")
            yield usage_ev(TURN_USAGE)
            yield stop_ev()
        return _gen()

    async def _embed(texts, *, provider, model, api_key, timeout=None, region=None):
        return EmbeddingResult(vectors=tuple(tuple([0.2] * 1024) for _ in texts), usage=NormalizedUsage(9, 0),
                               cost_usd=EMBED_COST, cost_is_estimate=True)
    monkeypatch.setattr(gateway, "stream", _stream)
    monkeypatch.setattr(gateway, "embed", _embed)
    return reqs


def _ctx(knowledge=None, **kw):
    return make_context(knowledge=knowledge if knowledge is not None else FakeKnowledge(),
                        embedding_api_key="sk-embed", **kw)


def _citations(evs) -> dict:
    hits = [e for e in evs if e.type is EventType.CITATIONS]
    assert len(hits) == 1, f"expected exactly one CITATIONS event, got {len(hits)}"
    return hits[0].data


async def _events(snap, ctx):
    return [ev async for ev in invoke(snap, INPUTS, ctx)]


async def test_context_inserted_before_latest_user_message_system_untouched(captured):
    snap = make_snapshot(retrieval=(_ref(),))
    await _events(snap, _ctx())
    (req,) = captured
    assert req.system == "You are a test agent."
    assert req.messages[-1] == {"role": "user", "content": INPUTS["message"]}
    ctx_msg = req.messages[-2]
    assert ctx_msg["role"] == "user" and ctx_msg["content"].startswith("<retrieved_context")
    assert "Annual leave is 27 days" in ctx_msg["content"]


async def test_streaming_stays_live_and_one_citations_event_between_text_and_usage(captured):
    evs = await _events(make_snapshot(retrieval=(_ref(),)), _ctx())
    types = [e.type for e in evs]
    assert types.count(EventType.CITATIONS) == 1
    ci = types.index(EventType.CITATIONS)
    assert max(i for i, t in enumerate(types) if t is EventType.TEXT_DELTA) < ci < types.index(EventType.USAGE)
    data = evs[ci].data
    assert data["knowledge_used"] is True
    assert data["knowledge_status"] == [{"kb_id": "kb-h", "status": "used"}]
    (cit,) = data["citations"]
    assert (cit["chunk_id"], cit["document_name"], cit["heading"], cit["id"]) == ("ch-27", "handbook.md", "Leave Policy", "c1")


async def test_invoke_result_citations_equal_the_event_payload(captured):
    evs = await _events(make_snapshot(retrieval=(_ref(),)), _ctx())
    ev_cits = _citations(evs)["citations"]
    res = await invoke_result(make_snapshot(retrieval=(_ref(),)), INPUTS, _ctx())
    assert [dataclasses.asdict(c) for c in res.citations] == ev_cits


async def test_no_bindings_means_no_citations_and_unchanged_request(captured):
    evs = await _events(make_snapshot(), _ctx())
    assert EventType.CITATIONS not in [e.type for e in evs]
    assert captured[0].messages == [{"role": "user", "content": INPUTS["message"]}]
    res = await invoke_result(make_snapshot(), INPUTS, _ctx())
    assert res.citations == () and res.run.retrieval is None


@pytest.mark.parametrize("knowledge", [FakeKnowledge(SearchOutcome("unavailable")),
                                       FakeKnowledge(exc=RuntimeError("db down"))])
async def test_unavailable_or_degraded_knowledge_never_fails_the_run(captured, knowledge):
    res = await invoke_result(make_snapshot(retrieval=(_ref(),)), INPUTS, _ctx(knowledge))
    assert res.run.outcome is Outcome.SUCCESS and res.citations == ()
    assert len(captured[0].messages) == 1                       # nothing inserted
    assert res.run.retrieval.bindings[0].status in ("unavailable", "degraded")


async def test_embedding_failure_never_fails_the_run(captured, monkeypatch):
    async def _boom(*a, **k):
        raise EmbeddingError("down", type="provider_error", retryable=True)
    monkeypatch.setattr(gateway, "embed", _boom)
    evs = await _events(make_snapshot(retrieval=(_ref(),)), _ctx())
    cit = _citations(evs)
    assert cit["knowledge_used"] is False and cit["knowledge_status"] == [{"kb_id": "kb-h", "status": "degraded"}]


async def test_run_record_retrieval_summary_and_exact_cost_invariant(captured):
    res = await invoke_result(make_snapshot(retrieval=(_ref(version=4),)), INPUTS, _ctx())
    r = res.run.retrieval
    assert r.embed_calls == 1 and r.retrieval_ms >= 0 and r.searches == 1
    (b,) = r.bindings
    assert (b.kb_id, b.kb_index_version, b.status, b.passages_used) == ("kb-h", 4, "used", 1)
    turn_cost = Decimal(str(TURN_USAGE.cost_usd)).quantize(Decimal("0.000001"))
    assert res.run.cost_usd == turn_cost + EMBED_COST
    assert res.run.cost_is_estimate is True


async def test_one_embedding_call_record_with_the_candidate_key_set(captured):
    recs: list[ModelCallRecord] = []
    await invoke_result(make_snapshot(retrieval=(_ref(),)), INPUTS, _ctx(record_call=recs.append))
    emb = [r for r in recs if r.role == "embedding"]
    cand = [r for r in recs if r.role == "candidate"]
    assert len(emb) == 1 and len(cand) == 1
    assert emb[0].output_text == "" and emb[0].input_text == INPUTS["message"]
    assert emb[0].cost_usd == EMBED_COST and emb[0].model == "text-embedding-3-small"
    assert cand[0].call_seq == 0 and emb[0].call_seq == 1     # model-turn numbering unchanged
    assert set(dataclasses.asdict(emb[0])) == set(dataclasses.asdict(cand[0]))
    assert sum(r.cost_usd for r in recs) == (await invoke_result(
        make_snapshot(retrieval=(_ref(),)), INPUTS, _ctx())).run.cost_usd


async def test_overflow_trims_lowest_scoring_passages_and_still_answers(captured):
    """Owner decision 2026-11-14 (analyze M5): retrieved context is trimmed BEFORE failing — drop the lowest-scoring
    passages until the request fits; mark the run `context_trimmed`."""
    limits = dict(make_snapshot().profile_snapshot)
    limits["limits"] = {"context_tokens": 400, "max_output_tokens": 10}
    small = Passage(chunk_id="ch-small", kb_id="kb-h", document_id="d", document_name="handbook.md",
                    text="Annual leave is 27 days.", token_count=8, score=0.9, meta={})
    big = Passage(chunk_id="ch-big", kb_id="kb-h", document_id="d", document_name="big.md", text="word " * 400,
                  token_count=700, score=0.5, meta={})
    res = await invoke_result(make_snapshot(retrieval=(_ref(),), profile_snapshot=limits), INPUTS,
                              _ctx(FakeKnowledge(SearchOutcome("ok", (small, big)))))
    assert res.run.outcome is Outcome.SUCCESS and len(captured) == 1
    assert [c.chunk_id for c in res.citations] == ["ch-small"]
    assert "word word" not in captured[0].messages[-2]["content"]
    assert "context_trimmed" in res.run.retrieval.degraded


async def test_request_too_big_even_without_context_still_fails_closed(captured):
    tiny = dict(make_snapshot().profile_snapshot)
    tiny["limits"] = {"context_tokens": 10, "max_output_tokens": 10}
    res = await invoke_result(make_snapshot(retrieval=(_ref(),), profile_snapshot=tiny), INPUTS, _ctx())
    assert res.run.outcome is Outcome.CONTEXT_OVERFLOW and captured == []


def test_call_log_input_never_carries_retrieved_passages():
    """spec Edge Cases ("never written to logs") — even under the 023 content-logging opt-in, the per-call input text
    withholds the retrieved-context message (customer KB passages); the user's own turn is still rendered."""
    from types import SimpleNamespace
    from runtime.invoke import _render_input
    from runtime.io import insert_context_message
    from runtime.retrieval.port import Passage
    from runtime.retrieval.render import render_context
    ctx = render_context((Passage(chunk_id="x", kb_id="k", document_id="d", document_name="h.md",
                                  text="SECRET PASSAGE", token_count=3, score=0.9, meta={}),), {"k": "KB"})
    req = SimpleNamespace(messages=insert_context_message([{"role": "user", "content": "how many days?"}], ctx))
    out = _render_input(req)
    assert "SECRET PASSAGE" not in out and "how many days?" in out and "retrieved context withheld" in out


async def test_retrieval_timeout_keeps_the_already_billed_query_embedding(captured):
    """Code review 2026-11-15 #5: the embedding call finished (and was billed) before a slow search hit the deadline —
    its cost and per-call record must survive the cancellation (SC-007: embeddings are model calls too)."""
    import asyncio

    class SlowKnowledge:
        async def search(self, ref, qvec, *, top_k, threshold):
            await asyncio.sleep(5)

    calls: list = []
    ctx = make_context(knowledge=SlowKnowledge(), embedding_api_key="sk-embed", deadline_s=0.3,
                       record_call=calls.append)
    res = await invoke_result(make_snapshot(retrieval=(_ref(),)), INPUTS, ctx)
    assert res.run.cost_usd >= EMBED_COST
    assert res.run.retrieval.embed_calls == 1
    assert [c.role for c in calls].count("embedding") == 1
