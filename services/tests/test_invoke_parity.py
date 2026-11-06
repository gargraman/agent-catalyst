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
