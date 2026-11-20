"""US1 (feature 007): json-mode output validation + one bounded repair re-ask + fail-closed output_invalid.

Gateway fully mocked (no live calls). RED against today's pass-through pipeline (which surfaces raw text as
`success`); GREEN after T008 wires buffered validated mode into runtime/invoke.py."""
from __future__ import annotations

import pytest

from runtime.invoke import invoke as runtime_invoke
from runtime.invoke import invoke_result
from runtime.providers import gateway
from runtime.providers.ir import EventType
from runtime.run import Outcome
from tests._invoke_helpers import (
    JSON_IO, make_context, make_gateway, make_multi_turn_gateway,
    make_snapshot, priced_usage, stop_ev, text_ev, usage_ev,
)

pytestmark = pytest.mark.asyncio

VALID = '{"severity":"high","score":8}'
BAD = "{not valid json"
SCHEMA_BAD = '{"severity":"high"}'              # valid JSON, missing required "score"
INPUTS = {"message": "an alert fired"}


def _json_snap():
    return make_snapshot(io_contract=JSON_IO)


async def test_valid_json_first_try_passes_through(monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev(VALID), usage_ev(), stop_ev()]))
    res = await invoke_result(_json_snap(), INPUTS, make_context())
    assert res.run.outcome is Outcome.SUCCESS
    assert res.run.correction_turns == 0
    assert res.text == VALID                      # valid structured output surfaced unchanged


async def test_malformed_then_valid_repairs_once_and_sums_usage(monkeypatch):
    gw = make_multi_turn_gateway([
        [text_ev(BAD), usage_ev(priced_usage(input=100, output=50, cost_usd=0.001)), stop_ev()],
        [text_ev(VALID), usage_ev(priced_usage(input=100, output=50, cost_usd=0.001)), stop_ev()],
    ])
    monkeypatch.setattr(gateway, "stream", gw)
    res = await invoke_result(_json_snap(), INPUTS, make_context())
    assert res.run.outcome is Outcome.SUCCESS
    assert res.run.correction_turns == 1                      # exactly one repair turn
    assert gw.calls["n"] == 2                                 # original + one repair
    assert res.run.usage.input_tokens == 200                 # summed across BOTH turns (SC-005)
    assert float(res.run.cost_usd) == pytest.approx(0.002)
    assert res.text == VALID


async def test_schema_violation_triggers_repair(monkeypatch):
    gw = make_multi_turn_gateway([
        [text_ev(SCHEMA_BAD), usage_ev(), stop_ev()],
        [text_ev(VALID), usage_ev(), stop_ev()],
    ])
    monkeypatch.setattr(gateway, "stream", gw)
    res = await invoke_result(_json_snap(), INPUTS, make_context())
    assert res.run.outcome is Outcome.SUCCESS and res.run.correction_turns == 1


async def test_invalid_on_both_turns_fails_closed_output_invalid(monkeypatch):
    gw = make_multi_turn_gateway([
        [text_ev(BAD), usage_ev(), stop_ev()],
        [text_ev(BAD), usage_ev(), stop_ev()],
    ])
    monkeypatch.setattr(gateway, "stream", gw)
    ctx = make_context(max_corrections=1)                     # one retry, then fail closed
    res = await invoke_result(_json_snap(), INPUTS, ctx)
    assert res.run.outcome is Outcome.OUTPUT_INVALID
    assert res.run.correction_turns == 1
    assert res.text == ""                                     # invalid output NOT surfaced as valid
    assert ctx.record_run.runs and len(ctx.record_run.runs) == 1   # exactly one Run


async def test_text_mode_unchanged_no_repair(monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("hello "), text_ev("world"), usage_ev(), stop_ev()]))
    res = await invoke_result(make_snapshot(), INPUTS, make_context())    # default io_contract = text
    assert res.run.outcome is Outcome.SUCCESS
    assert res.run.correction_turns == 0
    assert res.text == "hello world"


async def test_repair_turn_deltas_suppressed_from_stream(monkeypatch):
    """FR-015a: the intermediate (invalid) turn's content never reaches the caller's stream."""
    gw = make_multi_turn_gateway([
        [text_ev(BAD), usage_ev(), stop_ev()],
        [text_ev(VALID), usage_ev(), stop_ev()],
    ])
    monkeypatch.setattr(gateway, "stream", gw)
    texts = []
    async for ev in runtime_invoke(_json_snap(), INPUTS, make_context()):
        if ev.type is EventType.TEXT_DELTA:
            texts.append(ev.data.get("text", ""))
    joined = "".join(texts)
    assert BAD not in joined and joined == VALID             # only the validated result is streamed
