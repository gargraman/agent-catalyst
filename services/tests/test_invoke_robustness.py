"""Regression tests for code-review findings F1/F2/F4/F5 on the invoke spine (M2a). Mocked (SC-008)."""
from __future__ import annotations

import asyncio

from runtime import invoke as invoke_mod
from runtime.providers import gateway
from runtime.providers.profiles import Substitution
from runtime.run import Outcome
from tests._invoke_helpers import (
    error_ev,
    make_context,
    make_gateway,
    make_snapshot,
    text_ev,
    usage_ev,
)


def test_f1_degraded_run_carries_substitution_ledger(monkeypatch):
    # F1/FR-017: the gateway attaches preflight downgrades to a usage-less degraded ERROR; they must land
    # on the structured RunRecord.substitutions, not only buried inside RunRecord.error.
    sub = Substitution("prompt_caching", "dropped", "deferred to M7")
    err = {"type": "degraded", "retryable": False,
           "message": "provider stream ended without a usage report", "substitutions": [sub]}
    snap, ctx = make_snapshot(), make_context()
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("partial"), error_ev(err)]))
    res = asyncio.run(invoke_mod.invoke_result(snap, {"message": "hi"}, ctx))
    assert res.run.outcome is Outcome.DEGRADED
    assert res.run.substitutions == (sub,)


def test_f2_non_dict_messages_do_not_crash_the_run(monkeypatch):
    # F2/FR-011: a snapshot without an input_schema + non-dict messages must not crash the budget guard;
    # exactly one Run is still emitted.
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("ok"), usage_ev()]))
    snap = make_snapshot(io_contract={"output_format": "text", "output_schema": None})  # no input_schema
    ctx = make_context()
    res = asyncio.run(invoke_mod.invoke_result(snap, ["hi", "there"], ctx))  # bare list of strings
    assert len(ctx.record_run.runs) == 1


def test_f4_run_emitted_on_early_consumer_close(monkeypatch):
    # F4/FR-011/P8: a streaming consumer that stops early (then closes the generator) must still get
    # exactly one Run — not zero.
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("a"), text_ev("b"), usage_ev()]))
    snap, ctx = make_snapshot(), make_context()

    async def go():
        gen = invoke_mod.invoke(snap, {"message": "hi"}, ctx)
        async for _ev in gen:
            break  # consume one event, then abandon the stream
        await gen.aclose()

    asyncio.run(go())
    assert len(ctx.record_run.runs) == 1


def test_f5_none_deadline_is_accepted_and_bounded(monkeypatch):
    # F5: deadline_s=None must not be treated as elapsed nor silently disable the bound; it falls back to
    # the default. (The 30s bound itself is not timing-tested here — only that None is handled safely.)
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("ok"), usage_ev()]))
    snap = make_snapshot()
    ctx = make_context(deadline_s=None)
    res = asyncio.run(invoke_mod.invoke_result(snap, {"message": "hi"}, ctx))
    assert res.run.outcome is Outcome.SUCCESS
    assert len(ctx.record_run.runs) == 1
