"""Feature 023 US1 (T006): the runtime emits exactly one ModelCallRecord per model turn via ctx.record_call.

Pure — gateway.stream is mocked (no network, no paid calls). Covers single-turn, correction turns, provider
error, deadline, consumer disconnect, pre-call rejections, a failing sink, judge role, and the FR-005 cost
invariant (Σ per-call cost == run cost, Decimal-exact).
"""
from __future__ import annotations

import asyncio
import re
from decimal import Decimal

import pytest

from runtime.invoke import invoke, invoke_result
from runtime.providers import gateway
from runtime.run import ModelCallRecord, Outcome
from tests._invoke_helpers import (
    JSON_IO, error_ev, make_context, make_gateway, make_multi_turn_gateway, make_snapshot, priced_usage,
    stop_ev, text_ev, usage_ev,
)

pytestmark = pytest.mark.asyncio

INPUTS = {"message": "go"}
GOOD_JSON = '{"severity": "low", "score": 1}'


def _ctx(**kw):
    calls: list[ModelCallRecord] = []
    ctx = make_context(record_call=calls.append, **kw)
    return ctx, calls


async def test_single_turn_success_emits_one_record(monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("hi"), usage_ev(), stop_ev()]))
    ctx, calls = _ctx(correlation_id="corr-1")
    res = await invoke_result(make_snapshot(), INPUTS, ctx)
    assert res.run.outcome is Outcome.SUCCESS
    assert len(calls) == 1
    c = calls[0]
    assert isinstance(c, ModelCallRecord)
    assert c.run_id == res.run.run_id
    assert c.correlation_id == "corr-1"
    assert (c.workspace_id, c.agent_id, c.version_id) == ("ws_1", "agt_1", "ver_1")
    assert (c.source, c.role, c.call_seq, c.correction) == ("eval", "candidate", 0, False)
    assert (c.provider, c.model, c.prompt_version) == ("openai", "gpt-5-mini", "prompt_hash_1")
    assert c.output_text == "hi"
    assert "go" in c.input_text
    assert "You are a test agent." not in c.input_text   # U2: system prompt is identified by prompt_version, not logged
    assert c.success is True and c.error_code is None and c.cost_known is True
    assert c.latency_ms >= 0
    assert re.fullmatch(r"[0-9a-f]{32}", c.provider_correlation_id)


async def test_correction_turns_each_emit_a_record(monkeypatch):
    gw = make_multi_turn_gateway([
        [text_ev("{bad"), usage_ev(), stop_ev()],
        [text_ev(GOOD_JSON), usage_ev(), stop_ev()],
    ])
    monkeypatch.setattr(gateway, "stream", gw)
    ctx, calls = _ctx()
    res = await invoke_result(make_snapshot(io_contract=JSON_IO), INPUTS, ctx)
    assert res.run.outcome is Outcome.SUCCESS and res.run.correction_turns == 1
    assert [c.call_seq for c in calls] == [0, 1]
    assert [c.correction for c in calls] == [False, True]
    assert [c.output_text for c in calls] == ["{bad", GOOD_JSON]
    assert all(c.success for c in calls)             # each model call itself succeeded
    assert len({c.run_id for c in calls}) == 1


async def test_provider_error_record_is_failure(monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_gateway(
        [error_ev({"type": "provider_error", "retryable": True, "message": "boom sk-test-key"})]))
    ctx, calls = _ctx()
    res = await invoke_result(make_snapshot(), INPUTS, ctx)
    assert res.run.outcome is Outcome.PROVIDER_ERROR
    assert len(calls) == 1
    assert calls[0].success is False and calls[0].error_code == "provider_error"
    assert calls[0].cost_usd == Decimal("0") and calls[0].cost_known is False


async def test_deadline_cut_emits_partial_failed_record(monkeypatch):
    monkeypatch.setattr(gateway, "stream",
                        make_gateway([text_ev("a"), text_ev("b"), usage_ev(), stop_ev()], per_event_delay=0.05))
    ctx, calls = _ctx(deadline_s=0.08)
    res = await invoke_result(make_snapshot(), INPUTS, ctx)
    assert res.run.outcome is Outcome.TIMEOUT
    assert len(calls) == 1
    assert calls[0].success is False and calls[0].error_code == "timeout"
    assert calls[0].latency_ms > 0


async def test_consumer_disconnect_emits_one_failed_record(monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("a"), text_ev("b"), usage_ev(), stop_ev()]))
    ctx, calls = _ctx()
    agen = invoke(make_snapshot(), INPUTS, ctx)
    first = await agen.__anext__()
    assert first.data["text"] == "a"
    await agen.aclose()
    assert len(calls) == 1
    assert calls[0].success is False and calls[0].error_code == "interrupted"
    assert len(ctx.record_run.runs) == 1


@pytest.mark.parametrize("snap_kw, inputs", [
    ({}, {"wrong": 1}),                                              # invalid input
    ({"model_config": {"provider": "openai"}}, INPUTS),              # malformed config → config_error
])
async def test_pre_call_rejections_emit_no_records(monkeypatch, snap_kw, inputs):
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("x"), usage_ev(), stop_ev()]))
    ctx, calls = _ctx()
    res = await invoke_result(make_snapshot(**snap_kw), inputs, ctx)
    assert res.run.outcome in (Outcome.INVALID_INPUT, Outcome.CONFIG_ERROR)
    assert calls == []


async def test_retired_model_emits_no_record(monkeypatch):
    async def _retired(req, *, api_key, profile=None, timeout=None):
        raise gateway.ModelRetiredError("retired")
        yield  # pragma: no cover
    monkeypatch.setattr(gateway, "stream", _retired)
    ctx, calls = _ctx()
    res = await invoke_result(make_snapshot(), INPUTS, ctx)
    assert res.run.outcome is Outcome.CONFIG_ERROR
    assert calls == []


@pytest.mark.parametrize("is_async", [False, True])
async def test_failing_sink_never_alters_the_run(monkeypatch, is_async):
    events = [text_ev("hi"), usage_ev(), stop_ev()]
    monkeypatch.setattr(gateway, "stream", make_gateway(events))
    base_ctx = make_context()
    baseline = await invoke_result(make_snapshot(), INPUTS, base_ctx)

    def boom(rec):
        raise RuntimeError("sink down")

    async def aboom(rec):
        await asyncio.sleep(0)
        raise RuntimeError("sink down")

    ctx = make_context(record_call=aboom if is_async else boom)
    streamed = [ev async for ev in invoke(make_snapshot(), INPUTS, ctx)]
    assert [e.type for e in streamed] == [e.type for e in events]
    run = ctx.record_run.runs[0]
    assert run.outcome is baseline.run.outcome is Outcome.SUCCESS
    assert run.cost_usd == baseline.run.cost_usd


async def test_judge_snapshot_role(monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("ok"), usage_ev(), stop_ev()]))
    ctx, calls = _ctx()
    await invoke_result(make_snapshot(version_id="judge", agent_id="judge"), INPUTS, ctx)
    assert calls[0].role == "judge"


async def test_cost_invariant_single_turn(monkeypatch):
    monkeypatch.setattr(gateway, "stream",
                        make_gateway([text_ev("hi"), usage_ev(priced_usage(cost_usd=0.000132)), stop_ev()]))
    ctx, calls = _ctx()
    res = await invoke_result(make_snapshot(), INPUTS, ctx)
    assert sum((c.cost_usd for c in calls), Decimal(0)) == res.run.cost_usd == Decimal("0.000132")


async def test_cost_invariant_correction_turns_decimal_exact(monkeypatch):
    # 0.1 + 0.2 != 0.3 in float — the run cost must be the Decimal sum of the turn costs, not a float sum.
    gw = make_multi_turn_gateway([
        [text_ev("{bad"), usage_ev(priced_usage(cost_usd=0.1)), stop_ev()],
        [text_ev(GOOD_JSON), usage_ev(priced_usage(cost_usd=0.2)), stop_ev()],
    ])
    monkeypatch.setattr(gateway, "stream", gw)
    ctx, calls = _ctx()
    res = await invoke_result(make_snapshot(io_contract=JSON_IO), INPUTS, ctx)
    assert [c.cost_usd for c in calls] == [Decimal("0.1"), Decimal("0.2")]
    assert sum((c.cost_usd for c in calls), Decimal(0)) == res.run.cost_usd == Decimal("0.3")


async def test_cost_invariant_failed_turn(monkeypatch):
    gw = make_multi_turn_gateway([
        [text_ev("{bad"), usage_ev(priced_usage(cost_usd=0.001)), stop_ev()],
        [error_ev({"type": "provider_error", "retryable": False, "message": "x"})],
    ])
    monkeypatch.setattr(gateway, "stream", gw)
    ctx, calls = _ctx()
    res = await invoke_result(make_snapshot(io_contract=JSON_IO), INPUTS, ctx)
    assert res.run.outcome is Outcome.PROVIDER_ERROR
    assert len(calls) == 2
    assert calls[1].cost_usd == Decimal("0") and calls[1].cost_known is False
    assert sum((c.cost_usd for c in calls), Decimal(0)) == res.run.cost_usd == Decimal("0.001")


async def test_no_sink_is_a_noop(monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("hi"), usage_ev(), stop_ev()]))
    res = await invoke_result(make_snapshot(), INPUTS, make_context())
    assert res.run.outcome is Outcome.SUCCESS


async def test_correction_turn_input_includes_repair_messages(monkeypatch):
    """U2: a correction turn's input is the conversation actually sent that turn — incl. the repair message."""
    gw = make_multi_turn_gateway([
        [text_ev("{bad"), usage_ev(), stop_ev()],
        [text_ev(GOOD_JSON), usage_ev(), stop_ev()],
    ])
    monkeypatch.setattr(gateway, "stream", gw)
    ctx, calls = _ctx()
    await invoke_result(make_snapshot(io_contract=JSON_IO), INPUTS, ctx)
    assert len(calls[1].input_text) > len(calls[0].input_text)
    assert "{bad" in calls[1].input_text                 # the prior (rejected) model turn is part of the repair context
