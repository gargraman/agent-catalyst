"""Feature 026 — second /code-review, finding #5: a disconnected stream must still leave its Run row.

`persist_run_record` ran only after the runtime loop finished. A client disconnect (the new playground Stop button,
a proxy idle cut, a closed tab) abandons the SSE generator before that line — the provider still bills, but no Run
row is written, so the Runs page and the month-to-date spend cap (summed from runs) miss it: repeatedly aborting
just before `done` sidesteps the cap. The interrupted run is now recorded honestly as `cancelled`.

Two disconnect shapes are covered: the generator is aclose()d at a yield (ASGI ≥2.4: send() raises, the iterator
is finalized), and CancelledError lands mid-await (anyio task-group cancel). Also the late-finalizer case where the
request session is already gone.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select

from api.access_policy import PolicyPrincipal
from api.db import tenant_session
from api.models import Run
from api.routers.invoke import _run_invoke
from api.schemas.invoke import InvokeIn
from runtime import invoke as invoke_mod
from runtime.providers import gateway
from runtime.run import Outcome
from tests._api_helpers import seed_agent, seed_version, seed_workspace
from tests._invoke_helpers import make_context, make_snapshot, text_ev, usage_ev

pytestmark = pytest.mark.integration


def _hanging_gateway(started: asyncio.Event, *, with_usage: bool = False):
    """Yields one delta (+ optionally the usage event), then blocks forever — like a slow model mid-answer."""
    async def _stream(req, *, api_key, profile=None, timeout=None, **_resilience):
        yield text_ev("partial ")
        if with_usage:
            yield usage_ev()
        started.set()
        await asyncio.Event().wait()
    return _stream


async def _open_stream(ws, aid, monkeypatch, session, *, with_usage: bool = False, return_started: bool = False):
    started = asyncio.Event()
    monkeypatch.setattr(gateway, "stream", _hanging_gateway(started, with_usage=with_usage))
    ap = PolicyPrincipal(role="builder", teams=(), subject="u1")
    resp = await _run_invoke(session=session, ws=ws, ap=ap, agent_id=aid, body=InvokeIn(inputs={"message": "hi"}),
                             corr=uuid.uuid4().hex, provider_corr=uuid.uuid4().hex, source="playground")
    it = resp.body_iterator
    first = await it.__anext__()                      # run_start
    assert b"run_start" in (first if isinstance(first, bytes) else first.encode())
    await it.__anext__()                              # the partial text delta
    return (it, started) if return_started else it


async def _blocked_next(it, started: asyncio.Event) -> asyncio.Future:
    """Return a pending `__anext__` task that is parked inside the provider await (frames emitted before the hang,
    e.g. a `usage` frame, are drained first) — deterministic, no sleeps."""
    for _ in range(5):
        task = asyncio.ensure_future(it.__anext__())
        waiter = asyncio.ensure_future(started.wait())
        done, _ = await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
        if task not in done:
            return task
        waiter.cancel()
    raise AssertionError("stream never blocked in the provider")


async def _runs(ws):
    async with tenant_session(ws) as s:
        return list((await s.execute(select(Run))).scalars())


async def _seed():
    ws = await seed_workspace()
    aid = await seed_agent(ws)
    await seed_version(ws, aid)
    return ws, aid


async def test_aclose_at_a_yield_still_persists_a_cancelled_run(monkeypatch):
    ws, aid = await _seed()
    async with tenant_session(ws) as s:
        it = await _open_stream(ws, aid, monkeypatch, s)
        await it.aclose()                              # the client went away between frames
    (run,) = await _runs(ws)
    assert run.outcome == "cancelled" and run.source == "playground"


async def test_cancelled_error_mid_await_still_persists_a_cancelled_run(monkeypatch):
    ws, aid = await _seed()
    async with tenant_session(ws) as s:
        it, started = await _open_stream(ws, aid, monkeypatch, s, return_started=True)
        task = await _blocked_next(it, started)        # deterministically parked inside the provider await
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    (run,) = await _runs(ws)
    assert run.outcome == "cancelled"


async def test_cancelled_run_keeps_the_billed_cost_so_it_counts_toward_spend(monkeypatch):
    """Red review: the provider already reported usage (billed), then the client went away. The cancelled run must
    carry that cost — otherwise aborting just before `done` sidesteps the month-to-date spend cap."""
    ws, aid = await _seed()
    async with tenant_session(ws) as s:
        it, started = await _open_stream(ws, aid, monkeypatch, s, with_usage=True, return_started=True)
        task = await _blocked_next(it, started)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    (run,) = await _runs(ws)
    assert run.outcome == "cancelled" and run.cost_usd is not None and run.cost_usd > 0


async def test_late_finalizer_after_the_request_session_closed_still_persists(monkeypatch):
    ws, aid = await _seed()
    async with tenant_session(ws) as s:
        it = await _open_stream(ws, aid, monkeypatch, s)
    await it.aclose()                                  # GC/asyncgen finalizer runs after deps teardown
    (run,) = await _runs(ws)
    assert run.outcome == "cancelled"


# --- runtime (pure) -----------------------------------------------------------------------------------------------
async def test_runtime_emits_a_cancelled_run_when_cancelled_mid_provider_await(monkeypatch):
    started = asyncio.Event()
    monkeypatch.setattr(gateway, "stream", _hanging_gateway(started))
    ctx = make_context(source="playground")

    async def consume():
        async for _ in invoke_mod.invoke(make_snapshot(), {"message": "hi"}, ctx):
            pass
    task = asyncio.ensure_future(consume())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert [r.outcome for r in ctx.record_run.runs] == [Outcome.CANCELLED]


async def test_runtime_early_close_is_recorded_as_cancelled_not_success(monkeypatch):
    started = asyncio.Event()
    monkeypatch.setattr(gateway, "stream", _hanging_gateway(started))
    ctx = make_context(source="playground")
    gen = invoke_mod.invoke(make_snapshot(), {"message": "hi"}, ctx)
    await gen.__anext__()
    await gen.aclose()
    assert [r.outcome for r in ctx.record_run.runs] == [Outcome.CANCELLED]
