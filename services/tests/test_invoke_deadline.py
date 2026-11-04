"""US3 — work is bounded by an explicit deadline (M2a). Mocked gateway; no live calls (SC-008)."""
from __future__ import annotations

import asyncio

from runtime import invoke as invoke_mod
from runtime.providers import gateway
from runtime.run import Outcome
from tests._invoke_helpers import make_context, make_gateway, make_snapshot, text_ev, usage_ev


def test_slow_stream_stops_at_deadline_with_partial(monkeypatch):
    # US3/AC1-2 (FR-009, SC-003): a stream slower than the deadline → timeout outcome + best-effort partial.
    async def slow_stream(req, *, api_key, profile=None, timeout=None):
        yield text_ev("partial")
        await asyncio.sleep(1.0)      # far beyond the deadline
        yield usage_ev()

    monkeypatch.setattr(gateway, "stream", slow_stream)
    snap = make_snapshot()
    ctx = make_context(deadline_s=0.05)
    res = asyncio.run(invoke_mod.invoke_result(snap, {"message": "hi"}, ctx))
    assert res.run.outcome is Outcome.TIMEOUT
    assert res.text == "partial"            # partial work recorded
    assert res.run.usage is None            # usage never arrived before the deadline
    assert len(ctx.record_run.runs) == 1
    assert res.run.latency_ms >= 0


def test_deadline_already_elapsed_makes_no_call(monkeypatch):
    # Edge case: deadline_s <= 0 → immediate timeout, gateway NEVER called.
    def boom(*a, **k):
        raise AssertionError("gateway.stream must not be called when the deadline is already elapsed")

    monkeypatch.setattr(gateway, "stream", boom)
    snap = make_snapshot()
    ctx = make_context(deadline_s=0.0)
    res = asyncio.run(invoke_mod.invoke_result(snap, {"message": "hi"}, ctx))
    assert res.run.outcome is Outcome.TIMEOUT
    assert len(ctx.record_run.runs) == 1


def test_max_tool_calls_carried_not_enforced(monkeypatch):
    # US3/AC3 (FR-010, D6): max_tool_calls is carried onto the Run; single-turn behavior is unchanged.
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("done"), usage_ev()]))
    snap = make_snapshot()
    ctx = make_context(max_tool_calls=3)
    res = asyncio.run(invoke_mod.invoke_result(snap, {"message": "hi"}, ctx))
    assert res.run.max_tool_calls == 3
    assert res.text == "done"
    assert res.run.outcome is Outcome.SUCCESS
