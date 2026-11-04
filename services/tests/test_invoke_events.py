"""US4 — the full provider stream is surfaced to the caller (M2a, feature 003, T013). Mocked (SC-008).

The invoke pipeline already re-yields every event (streaming consumers see them); what US4 adds is
*accumulation*: reasoning text, completed tool calls, and the terminal stop reason must land on the
collected `InvokeResult`/`RunRecord`, and a tool-call-only response must read as a non-empty success —
not empty/degraded (closes review gap C4). These tests fail behaviorally against the pre-T014 pipeline.
"""
from __future__ import annotations

import asyncio

from runtime import invoke as invoke_mod
from runtime.providers import gateway
from runtime.providers.ir import EventType
from runtime.run import Outcome
from tests._invoke_helpers import (
    collect,
    make_context,
    make_gateway,
    make_snapshot,
    reasoning_ev,
    stop_ev,
    text_ev,
    tool_delta_ev,
    tool_end_ev,
    tool_start_ev,
    usage_ev,
)


def test_us4_every_event_class_resurfaced_to_streaming_caller(monkeypatch):
    # US4/AC1, FR-007, SC-004: a streaming consumer sees every event class, none dropped/flattened.
    events = [
        text_ev("the "), reasoning_ev("thinking"),
        tool_start_ev("c1", "search"), tool_delta_ev("c1", '{"q":'), tool_end_ev("c1", "search", {"q": "x"}),
        text_ev("answer"), usage_ev(), stop_ev("tool_use"),
    ]
    monkeypatch.setattr(gateway, "stream", make_gateway(events))
    surfaced = collect(invoke_mod.invoke(make_snapshot(), {"message": "hi"}, make_context()))
    types = {e.type for e in surfaced}
    for expected in (
        EventType.TEXT_DELTA, EventType.REASONING_DELTA,
        EventType.TOOL_CALL_START, EventType.TOOL_CALL_DELTA, EventType.TOOL_CALL_END,
        EventType.USAGE, EventType.STOP,
    ):
        assert expected in types, f"{expected} was dropped from the surfaced stream"


def test_us4_reasoning_toolcalls_stop_accumulated_on_result_and_run(monkeypatch):
    # US4/AC1+AC3, FR-007/008: collected result accumulates reasoning + tool calls + stop reason, and the
    # stop reason is recorded on the Run. (Behavioral RED: pre-T014 drops all three.)
    events = [
        reasoning_ev("step1 "), reasoning_ev("step2"), text_ev("hi"),
        tool_start_ev("c1", "search"), tool_end_ev("c1", "search", {"q": "x"}),
        usage_ev(), stop_ev("tool_use"),
    ]
    monkeypatch.setattr(gateway, "stream", make_gateway(events))
    res = asyncio.run(invoke_mod.invoke_result(make_snapshot(), {"message": "hi"}, make_context()))
    assert res.reasoning == "step1 step2"
    assert res.tool_calls == ({"id": "c1", "name": "search", "args": {"q": "x"}},)
    assert res.stop_reason == "tool_use"
    assert res.run.stop_reason == "tool_use"


def test_us4_tool_call_only_is_nonempty_success(monkeypatch):
    # US4/AC2, SC-004, C4: a response whose only output is a tool call (no text) is a non-empty success —
    # the tool call is surfaced, not dropped, and the outcome is success (not empty/degraded).
    events = [
        tool_start_ev("c1", "lookup"), tool_end_ev("c1", "lookup", {"id": 42}),
        usage_ev(), stop_ev("tool_use"),
    ]
    monkeypatch.setattr(gateway, "stream", make_gateway(events))
    res = asyncio.run(invoke_mod.invoke_result(make_snapshot(), {"message": "hi"}, make_context()))
    assert res.run.outcome is Outcome.SUCCESS
    assert res.text == ""
    assert len(res.tool_calls) == 1 and res.tool_calls[0]["name"] == "lookup"
