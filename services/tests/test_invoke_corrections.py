"""Polish (feature 007): shared correction-budget bound (SC-004), deadline interaction (FR-011), and
cost accounting on fail-closed (FR-012/M2). Gateway mocked; no live calls."""
from __future__ import annotations

import pytest

from runtime.invoke import invoke_result
from runtime.providers import gateway
from runtime.run import Outcome
from tests._invoke_helpers import (
    JSON_IO, make_context, make_gateway, make_snapshot, priced_usage, stop_ev, text_ev, usage_ev,
)

pytestmark = pytest.mark.asyncio

BAD = "{bad"
INPUTS = {"message": "go"}


async def test_adversarial_model_bounded_by_budget(monkeypatch):
    """An always-invalid model never exceeds ctx.max_corrections correction turns, then fails closed (SC-004)."""
    gw = make_gateway([text_ev(BAD), usage_ev(), stop_ev()])        # every turn invalid
    monkeypatch.setattr(gateway, "stream", gw)
    res = await invoke_result(make_snapshot(io_contract=JSON_IO), INPUTS, make_context(max_corrections=2))
    assert res.run.outcome is Outcome.OUTPUT_INVALID
    assert res.run.correction_turns == 2                           # capped at the budget, not unbounded


async def test_zero_budget_fails_closed_without_correction(monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev(BAD), usage_ev(), stop_ev()]))
    res = await invoke_result(make_snapshot(io_contract=JSON_IO), INPUTS, make_context(max_corrections=0))
    assert res.run.outcome is Outcome.OUTPUT_INVALID and res.run.correction_turns == 0


async def test_deadline_enforced_in_validated_mode(monkeypatch):
    """The deadline bounds the whole validated invocation (FR-011) → timeout, exactly one Run."""
    monkeypatch.setattr(gateway, "stream",
                        make_gateway([text_ev(BAD), usage_ev(), stop_ev()], per_event_delay=0.05))
    ctx = make_context(deadline_s=0.02, max_corrections=2)
    res = await invoke_result(make_snapshot(io_contract=JSON_IO), INPUTS, ctx)
    assert res.run.outcome is Outcome.TIMEOUT
    assert len(ctx.record_run.runs) == 1


async def test_failclosed_still_reports_summed_spend(monkeypatch):
    """Usage/cost accrued across all turns is reported even on a fail-closed outcome (FR-012/M2)."""
    monkeypatch.setattr(gateway, "stream",
                        make_gateway([text_ev(BAD), usage_ev(priced_usage(input=100, output=50, cost_usd=0.001)), stop_ev()]))
    res = await invoke_result(make_snapshot(io_contract=JSON_IO), INPUTS, make_context(max_corrections=1))
    assert res.run.outcome is Outcome.OUTPUT_INVALID
    assert res.run.usage.input_tokens == 200                       # 2 turns (original + 1 correction) summed
    assert float(res.run.cost_usd) == pytest.approx(0.002)
    assert res.run.cost_is_estimate is True                        # sticky across turns
