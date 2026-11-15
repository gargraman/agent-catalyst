"""T029 (feature 025, US3) — resilience parity across the callers (spec FR-014, constitution P6; quickstart §2).

ONE fault script (transient ×1, then success) through every caller of `runtime.invoke` — playground, published
(`source="api"`), channel, and an eval case — must give the identical RunRecord `retries`, outcome, cost and tokens.
The only permitted difference is the traffic class (eval ⇒ batch, else live), observed via the guard's `retry` event.
Both the streaming `invoke` (API planes) and the collecting `invoke_result` (evals) are exercised.

Expected GREEN on first run (verification of US1/US2 code, constitution §9): retry lives in the gateway, below every
caller, so there is no per-caller code path to diverge. The test exists to keep it that way.
"""
from __future__ import annotations

import random

import pytest

from runtime.invoke import invoke as runtime_invoke
from runtime.invoke import invoke_result
from runtime.providers import resilience
from runtime.run import Outcome
from tests._fault_transport import FaultScript, fail, ok
from tests._invoke_helpers import make_context, make_snapshot

pytestmark = pytest.mark.asyncio

INPUTS = {"message": "go"}
CALLERS = [("playground", "live"), ("api", "live"), ("channel", "live"), ("eval", "batch")]


async def _instant(_s: float) -> None:
    return None


def _ctx(source: str, seen: list):
    guard = resilience.Guard(resilience.ResiliencePolicy(), sleep=_instant, rng=random.Random(5),
                             observer=lambda name, labels: seen.append((name, labels)))
    return make_context(source=source, guard=guard)


async def _run(monkeypatch, source: str, *, streaming: bool):
    FaultScript([fail(503), ok("hello", prompt=10, completion=3)]).install(monkeypatch)
    seen: list = []
    ctx = _ctx(source, seen)
    if streaming:
        async for _ in runtime_invoke(make_snapshot(), INPUTS, ctx):
            pass
        run = ctx.record_run.runs[-1]
    else:
        run = (await invoke_result(make_snapshot(), INPUTS, ctx)).run
    retry_classes = [labels["cls"] for name, labels in seen if name == "retry"]
    return run, retry_classes


@pytest.mark.parametrize("streaming", [True, False], ids=["invoke", "invoke_result"])
async def test_same_fault_script_same_record_across_callers(monkeypatch, streaming):
    results = {src: await _run(monkeypatch, src, streaming=streaming) for src, _ in CALLERS}
    runs = {src: r for src, (r, _) in results.items()}
    ref = runs["playground"]
    assert ref.outcome is Outcome.SUCCESS and ref.retries == 1
    for src, run in runs.items():
        assert run.outcome is ref.outcome, src
        assert run.retries == ref.retries, src
        assert run.cost_usd == ref.cost_usd, src
        assert run.usage.input_tokens == ref.usage.input_tokens, src
        assert run.usage.output_tokens == ref.usage.output_tokens, src
    for src, expected in CALLERS:                                      # the ONLY difference: the traffic class
        assert results[src][1] == [expected], src


async def test_streaming_and_collecting_agree_per_caller(monkeypatch):
    for src, _ in CALLERS:
        s_run, _ = await _run(monkeypatch, src, streaming=True)
        c_run, _ = await _run(monkeypatch, src, streaming=False)
        assert (s_run.outcome, s_run.retries, s_run.cost_usd) == (c_run.outcome, c_run.retries, c_run.cost_usd), src
