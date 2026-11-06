"""Polish (feature 007, research D1 regression guard): a text-mode, no-tools snapshot keeps the M2a
pass-through behavior — events stream LIVE (not buffered into one), no correction, no new outcomes."""
from __future__ import annotations

import pytest

from runtime.invoke import invoke as runtime_invoke
from runtime.providers import gateway
from runtime.providers.ir import EventType
from runtime.run import Outcome
from tests._invoke_helpers import make_context, make_gateway, make_snapshot, stop_ev, text_ev, usage_ev

pytestmark = pytest.mark.asyncio


async def test_text_mode_streams_deltas_live_not_buffered(monkeypatch):
    monkeypatch.setattr(gateway, "stream",
                        make_gateway([text_ev("hel"), text_ev("lo"), text_ev("!"), usage_ev(), stop_ev()]))
    deltas = []
    ctx = make_context()
    async for ev in runtime_invoke(make_snapshot(), {"message": "hi"}, ctx):
        if ev.type is EventType.TEXT_DELTA:
            deltas.append(ev.data.get("text", ""))
    # three separate deltas preserved in order (live), NOT merged into one buffered delta
    assert deltas == ["hel", "lo", "!"]
    run = ctx.record_run.runs[-1]
    assert run.outcome is Outcome.SUCCESS and run.correction_turns == 0
