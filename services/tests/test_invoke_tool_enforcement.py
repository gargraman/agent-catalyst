"""US2 (feature 007): tool-call enforcement + one bounded feed-back re-ask + fail-closed tool_call_rejected.

A valid tool call is surfaced unchanged and NOT executed (execution is P2). Gateway fully mocked.
The enforcement is wired into the shared buffered loop in runtime/invoke.py (built with US1's loop, T008)."""
from __future__ import annotations

import pytest

from runtime.invoke import invoke_result
from runtime.providers import gateway
from runtime.run import Outcome
from tests._invoke_helpers import (
    JSON_IO, make_context, make_gateway, make_multi_turn_gateway, make_snapshot,
    make_tool_ref, stop_ev, text_ev, tool_end_ev, usage_ev,
)

pytestmark = pytest.mark.asyncio

SCHEMA = {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "number"}},
          "required": ["query"]}
INPUTS = {"message": "search the runbook"}


def _tool_snap():
    return make_snapshot(tools=(make_tool_ref("search_runbook", SCHEMA),))


async def test_valid_tool_call_surfaced_and_not_executed(monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_gateway(
        [tool_end_ev("c1", "search_runbook", {"query": "failed logins", "limit": 5}), usage_ev(), stop_ev()]))
    res = await invoke_result(_tool_snap(), INPUTS, make_context())
    assert res.run.outcome is Outcome.SUCCESS
    assert res.run.correction_turns == 0
    assert len(res.tool_calls) == 1 and res.tool_calls[0]["name"] == "search_runbook"   # surfaced, not executed


async def test_unbound_tool_name_fed_back_then_corrected(monkeypatch):
    gw = make_multi_turn_gateway([
        [tool_end_ev("c1", "delete_everything", {}), usage_ev(), stop_ev()],
        [tool_end_ev("c2", "search_runbook", {"query": "x", "limit": 5}), usage_ev(), stop_ev()],
    ])
    monkeypatch.setattr(gateway, "stream", gw)
    res = await invoke_result(_tool_snap(), INPUTS, make_context())
    assert res.run.outcome is Outcome.SUCCESS
    assert res.run.correction_turns == 1 and gw.calls["n"] == 2
    assert [tc["name"] for tc in res.tool_calls] == ["search_runbook"]      # rejected call never surfaced


async def test_bad_args_both_turns_fail_closed_tool_call_rejected(monkeypatch):
    gw = make_multi_turn_gateway([
        [tool_end_ev("c1", "search_runbook", {"limit": 5}), usage_ev(), stop_ev()],   # missing required "query"
        [tool_end_ev("c2", "search_runbook", {"limit": 7}), usage_ev(), stop_ev()],
    ])
    monkeypatch.setattr(gateway, "stream", gw)
    ctx = make_context(max_corrections=1)
    res = await invoke_result(_tool_snap(), INPUTS, ctx)
    assert res.run.outcome is Outcome.TOOL_CALL_REJECTED
    assert res.run.correction_turns == 1
    assert res.tool_calls == ()                                             # rejected call NOT surfaced
    assert len(ctx.record_run.runs) == 1                                   # exactly one Run


async def test_json_mode_valid_tool_call_not_flagged_output_invalid(monkeypatch):
    """Convergence F1 (FR-009/FR-001): a json-mode agent that emits a VALID tool call (no final text) must be
    surfaced unchanged, NOT mis-validated as invalid JSON. A tool-call turn is not a final message."""
    snap = make_snapshot(io_contract=JSON_IO, tools=(make_tool_ref("search_runbook", SCHEMA),))
    monkeypatch.setattr(gateway, "stream", make_gateway(
        [tool_end_ev("c1", "search_runbook", {"query": "x"}), usage_ev(), stop_ev()]))
    res = await invoke_result(snap, INPUTS, make_context())
    assert res.run.outcome is Outcome.SUCCESS
    assert res.run.correction_turns == 0
    assert [tc["name"] for tc in res.tool_calls] == ["search_runbook"]


async def test_json_mode_final_text_still_validated_with_tools_bound(monkeypatch):
    """Guard must NOT over-skip: a json-mode turn with NO tool call (a final answer) is still validated."""
    snap = make_snapshot(io_contract=JSON_IO, tools=(make_tool_ref("search_runbook", SCHEMA),))
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("{bad"), usage_ev(), stop_ev()]))
    res = await invoke_result(snap, INPUTS, make_context(max_corrections=0))
    assert res.run.outcome is Outcome.OUTPUT_INVALID        # final text still schema/JSON-validated


async def test_unknown_tool_rejected_when_no_budget(monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_gateway(
        [tool_end_ev("c1", "nope", {}), usage_ev(), stop_ev()]))
    ctx = make_context(max_corrections=0)                                   # no feed-back allowed
    res = await invoke_result(_tool_snap(), INPUTS, ctx)
    assert res.run.outcome is Outcome.TOOL_CALL_REJECTED and res.run.correction_turns == 0
