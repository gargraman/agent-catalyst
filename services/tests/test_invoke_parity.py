"""Polish (feature 007, FR-016/SC-008): `invoke` (streaming) and `invoke_result` (collecting) produce
IDENTICAL outcome + RunRecord for equivalent inputs — json-mode and tool-mode, incl. a corrected run."""
from __future__ import annotations

import pytest

from runtime.invoke import invoke as runtime_invoke
from runtime.invoke import invoke_result
from runtime.providers import gateway
from runtime.providers.ir import EventType
from tests._invoke_helpers import (
    JSON_IO, make_context, make_gateway, make_multi_turn_gateway, make_snapshot, make_tool_ref,
    reasoning_ev, stop_ev, text_ev, tool_end_ev, usage_ev,
)

pytestmark = pytest.mark.asyncio

VALID = '{"severity":"high","score":8}'
BAD = "{bad"
INPUTS = {"message": "go"}


async def _stream_run(snap, ctx):
    texts, tool_calls = [], []
    async for ev in runtime_invoke(snap, INPUTS, ctx):
        if ev.type is EventType.TEXT_DELTA:
            texts.append(ev.data.get("text", ""))
        elif ev.type is EventType.TOOL_CALL_END:
            tool_calls.append(ev.data)
    return "".join(texts), tool_calls, ctx.record_run.runs[-1]


async def test_parity_json_repaired(monkeypatch):
    turns = [[text_ev(BAD), usage_ev(), stop_ev()], [text_ev(VALID), usage_ev(), stop_ev()]]
    monkeypatch.setattr(gateway, "stream", make_multi_turn_gateway(turns))
    s_text, _, s_run = await _stream_run(make_snapshot(io_contract=JSON_IO), make_context())
    monkeypatch.setattr(gateway, "stream", make_multi_turn_gateway(turns))
    res = await invoke_result(make_snapshot(io_contract=JSON_IO), INPUTS, make_context())
    assert s_run.outcome is res.run.outcome
    assert s_run.correction_turns == res.run.correction_turns == 1
    assert s_run.cost_usd == res.run.cost_usd
    assert s_text == res.text == VALID


async def test_validated_streaming_emits_reasoning_like_invoke_result(monkeypatch):
    """code-review F3: in validated mode the streaming path re-emits reasoning, matching invoke_result."""
    evs = [reasoning_ev("thinking hard"), text_ev(VALID), usage_ev(), stop_ev()]
    monkeypatch.setattr(gateway, "stream", make_gateway(evs))
    streamed = []
    async for ev in runtime_invoke(make_snapshot(io_contract=JSON_IO), INPUTS, make_context()):
        if ev.type is EventType.REASONING_DELTA:
            streamed.append(ev.data.get("text", ""))
    monkeypatch.setattr(gateway, "stream", make_gateway(evs))
    res = await invoke_result(make_snapshot(io_contract=JSON_IO), INPUTS, make_context())
    assert "".join(streamed) == "thinking hard" == res.reasoning


async def test_parity_tool_valid(monkeypatch):
    schema = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
    snap = lambda: make_snapshot(tools=(make_tool_ref("search", schema),))
    evs = [[tool_end_ev("c1", "search", {"query": "x"}), usage_ev(), stop_ev()]]
    monkeypatch.setattr(gateway, "stream", make_multi_turn_gateway(evs))
    _, s_tools, s_run = await _stream_run(snap(), make_context())
    monkeypatch.setattr(gateway, "stream", make_multi_turn_gateway(evs))
    res = await invoke_result(snap(), INPUTS, make_context())
    assert s_run.outcome is res.run.outcome
    assert [t["name"] for t in s_tools] == [t["name"] for t in res.tool_calls] == ["search"]


# ---- feature 024 (T042): retrieval parity across entry points (FR-019) ---------------------------------------
async def test_parity_retrieval_summary_and_citations(monkeypatch):
    from decimal import Decimal

    from runtime.providers.ir import EmbeddingResult, NormalizedUsage
    from runtime.retrieval.port import Passage, SearchOutcome
    from runtime.snapshot import RetrievalRef
    from tests._invoke_helpers import make_gateway

    ref = RetrievalRef(kb_id="kb-p", embedding_model="text-embedding-3-small",
                       retrieval_config={"top_k": 6, "similarity_threshold": 0.2, "max_ctx_tokens": 2000},
                       injection="always_on", kb_index_version=2, embedding_provider="openai", kb_name="P")
    passage = Passage(chunk_id="c-p", kb_id="kb-p", document_id="d-p", document_name="p.md", text="fact",
                      token_count=3, score=0.7, meta={"page": 1, "heading": None})

    class K:
        async def search(self, r, qvec, *, top_k, threshold):
            return SearchOutcome("ok", (passage,))

    async def _embed(texts, *, provider, model, api_key, timeout=None, region=None):
        return EmbeddingResult(vectors=((0.1,) * 1024,), usage=NormalizedUsage(3, 0), cost_usd=Decimal("0.000002"),
                               cost_is_estimate=True)
    monkeypatch.setattr(gateway, "embed", _embed)
    events = [text_ev("ok"), usage_ev(), stop_ev()]
    snap = make_snapshot(retrieval=(ref,))

    monkeypatch.setattr(gateway, "stream", make_gateway(events))
    ctx_s = make_context(knowledge=K(), embedding_api_key="k")
    streamed = [ev async for ev in runtime_invoke(snap, INPUTS, ctx_s)]
    s_run = ctx_s.record_run.runs[-1]
    hits = [e for e in streamed if e.type is EventType.CITATIONS]
    assert len(hits) == 1, "expected one CITATIONS event"
    s_cits = hits[0].data["citations"]

    monkeypatch.setattr(gateway, "stream", make_gateway(events))
    res = await invoke_result(snap, INPUTS, make_context(knowledge=K(), embedding_api_key="k"))
    import dataclasses
    assert [dataclasses.asdict(c) for c in res.citations] == s_cits
    strip = lambda r: dataclasses.replace(r, retrieval_ms=0)  # noqa: E731 — wall time legitimately differs
    assert strip(s_run.retrieval) == strip(res.run.retrieval)
    assert s_run.cost_usd == res.run.cost_usd
