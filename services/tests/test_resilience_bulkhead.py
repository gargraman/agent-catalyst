"""T027 (feature 025, US3) — live/batch isolation + the batch bulkhead (contract runtime-resilience §3 B6/B7, §4 K1–K3,
§7 `batch_wait`; research D6; spec FR-010/FR-011, SC-004).

Real gateway + real adapter; only the transport seam is faked (`_fault_transport`, which tracks `max_in_flight`).
Concurrency tests use small real delays (≤ 1 s total). No live provider calls.

Red/verification split (constitution §9): B6, B7 and the batch-only failure keys exercise US2 code (T022/T023) and are
expected GREEN on first run — they are here so the US3 checkpoint is one file. K1/K2-in-flight/K3/metric are the Red.
"""
from __future__ import annotations

import asyncio
import random
import time

import fakeredis.aioredis
import pytest

from runtime.invoke import invoke_result
from runtime.providers import gateway, resilience
from runtime.providers.ir import EventType, NormalizedRequest
from runtime.providers.resilience import BreakerKey
from runtime.run import Outcome
from tests._fault_transport import FaultScript, fail, ok
from tests._invoke_helpers import make_context, make_snapshot

pytestmark = pytest.mark.asyncio

MODEL_A = "gpt-5-mini"
MODEL_B = "gpt-5"
EMBED_MODEL = "text-embedding-3-small"


def _req(model: str = MODEL_A) -> NormalizedRequest:
    return NormalizedRequest(provider="openai", model=model, api_version=None,
                             messages=[{"role": "user", "content": "hi"}], system=None, params={})


def _guard(*, cap: int = 4, store=None, seen: list | None = None, **policy) -> resilience.Guard:
    observer = (lambda name, labels: seen.append((name, labels))) if seen is not None else None
    return resilience.Guard(resilience.ResiliencePolicy(batch_concurrency=cap, **policy), store=store,
                            observer=observer, rng=random.Random(3))


async def _call(guard, cls: str, model: str = MODEL_A) -> list:
    return [ev async for ev in gateway.stream(_req(model), api_key="k", timeout=5.0, guard=guard, traffic_class=cls)]


def _served(events) -> bool:
    return any(e.type is EventType.USAGE for e in events) and not any(e.type is EventType.ERROR for e in events)


def _refused(events) -> bool:
    return any(e.type is EventType.ERROR and e.data["error"]["type"] == "provider_unavailable" for e in events)


async def _open(guard, key: BreakerKey) -> None:
    for _ in range(guard.policy.failure_threshold):
        await guard.store.record_failure(key, was_trial=False)


# ---- B6 / B7 (verification: US2 admission already cross-checks live for batch) ------------------------------------
async def test_b6_live_open_refuses_batch_with_zero_calls(monkeypatch):
    script = FaultScript([ok("never")]).install(monkeypatch)
    guard = _guard()
    await _open(guard, BreakerKey("openai", MODEL_A, "live"))
    assert _refused(await _call(guard, "batch"))
    assert script.calls == 0


async def test_b7_batch_open_still_admits_live(monkeypatch):
    script = FaultScript([ok("served")]).install(monkeypatch)
    guard = _guard()
    await _open(guard, BreakerKey("openai", MODEL_A, "batch"))
    assert _refused(await _call(guard, "batch"))
    assert _served(await _call(guard, "live"))
    assert script.calls == 1


async def test_batch_failures_increment_only_batch_keys(monkeypatch):
    FaultScript([fail(503)]).install(monkeypatch)
    client = fakeredis.aioredis.FakeRedis()
    from api.breaker_store import RedisBreakerStore
    guard = _guard(store=RedisBreakerStore(client), max_attempts=1)
    await _call(guard, "batch")
    assert int(await client.get(f"ac:cb:openai:{MODEL_A}:batch:fails")) == 1
    assert await client.get(f"ac:cb:openai:{MODEL_A}:live:fails") is None


# ---- K1: at most `cap` batch calls per (provider, model) inside the attempt loop -----------------------------------
async def test_k1_twenty_batch_calls_never_exceed_four_in_flight(monkeypatch):
    script = FaultScript([ok("x", delay_s=0.02)]).install(monkeypatch)
    seen: list = []
    guard = _guard(cap=4, seen=seen)
    results = await asyncio.gather(*(_call(guard, "batch") for _ in range(20)))
    assert all(_served(r) for r in results)                            # queueing, never failure
    assert script.calls == 20
    assert script.max_in_flight == 4
    waits = [labels for name, labels in seen if name == "batch_wait"]
    assert len(waits) == 20                                            # one per batch acquisition (zeros included)
    assert all(set(w) == {"provider", "model", "seconds"} for w in waits)
    assert all(w["provider"] == "openai" and w["model"] == MODEL_A for w in waits)
    assert sum(1 for w in waits if w["seconds"] > 0) >= 16             # 16 of 20 had to queue


async def test_k1_cap_is_per_provider_model(monkeypatch):
    script = FaultScript([ok("x", delay_s=0.05)]).install(monkeypatch)
    guard = _guard(cap=4)
    results = await asyncio.gather(*(_call(guard, "batch", MODEL_A) for _ in range(8)),
                                   _call(guard, "batch", MODEL_B))
    assert all(_served(r) for r in results)
    assert script.max_in_flight == 5                                   # 4 on model A + 1 on model B (not blocked by A)


async def test_k1_batch_embeddings_are_capped_too(monkeypatch):
    script = FaultScript([ok(delay_s=0.02)]).install(monkeypatch)
    guard = _guard(cap=4)

    async def one():
        return await gateway.embed(["chunk"], provider="openai", model=EMBED_MODEL, api_key="k", timeout=5.0,
                                   guard=guard, traffic_class="batch")
    results = await asyncio.gather(*(one() for _ in range(10)))
    assert len(results) == 10 and all(len(r.vectors) == 1 for r in results)
    assert script.max_in_flight == 4


# ---- K2: live never takes the semaphore ----------------------------------------------------------------------------
async def test_k2_live_does_not_wait_behind_saturated_batch(monkeypatch):
    script = FaultScript([ok("x", delay_s=0.1)]).install(monkeypatch)
    seen: list = []
    guard = _guard(cap=1, seen=seen)
    t0 = time.monotonic()
    batch = [asyncio.create_task(_call(guard, "batch")) for _ in range(3)]   # 1 in flight, 2 queued
    await asyncio.sleep(0.01)
    live = await _call(guard, "live")
    live_done = time.monotonic() - t0
    assert _served(live)
    assert live_done < 0.18                                            # did not queue behind the 0.1 s batch calls
    await asyncio.gather(*batch)
    assert script.max_in_flight == 2                                   # 1 batch slot + the live call
    assert sum(1 for name, _ in seen if name == "batch_wait") == 3     # only the batch calls acquired


# ---- K3: a queued batch call whose deadline expires ends as TIMEOUT via invoke — never a hang ----------------------
async def test_k3_queued_batch_call_times_out_through_invoke(monkeypatch):
    # The holder takes the only batch slot for 1 s; the eval run's own call would be instant if it reached the wire.
    script = FaultScript([ok("hold", delay_s=1.0), ok("fast")]).install(monkeypatch)
    guard = _guard(cap=1)
    holder = asyncio.create_task(_call(guard, "batch"))
    await asyncio.sleep(0.01)
    t0 = time.monotonic()
    try:
        res = await invoke_result(make_snapshot(), {"message": "go"},
                                  make_context(source="eval", guard=guard, deadline_s=0.2))
    finally:
        holder.cancel()
        await asyncio.gather(holder, return_exceptions=True)
    assert res.run.outcome is Outcome.TIMEOUT
    assert time.monotonic() - t0 < 0.6
    assert script.calls == 1                                           # the queued call never reached the transport


async def test_k3_cancelled_waiter_releases_nothing_it_never_held(monkeypatch):
    """A waiter cancelled while queued must not leak a slot: after it, the cap is still exactly `cap`."""
    script = FaultScript([ok("hold", delay_s=0.1), ok("x", delay_s=0.03)]).install(monkeypatch)
    guard = _guard(cap=1)
    holder = asyncio.create_task(_call(guard, "batch"))
    await asyncio.sleep(0.01)
    waiter = asyncio.create_task(_call(guard, "batch"))
    await asyncio.sleep(0.01)
    waiter.cancel()
    await asyncio.gather(holder, waiter, return_exceptions=True)
    script.max_in_flight = 0
    results = await asyncio.gather(*(_call(guard, "batch") for _ in range(3)))
    assert all(_served(r) for r in results)
    assert script.max_in_flight == 1


# ---- §7 → API metric: `batch_wait` feeds the `ac_batch_queue_wait_seconds` histogram -------------------------------
async def test_batch_wait_event_feeds_queue_wait_histogram():
    from api import metrics
    hist = metrics.ac_batch_queue_wait_seconds.labels(provider="openai", model=MODEL_A)
    before_sum, before_n = hist._sum.get(), _count(hist)
    metrics.record_resilience_event("batch_wait", {"provider": "openai", "model": MODEL_A, "seconds": 0.25})
    assert hist._sum.get() == pytest.approx(before_sum + 0.25)
    assert _count(hist) == before_n + 1


def _count(hist) -> float:
    return sum(b.get() for b in hist._buckets)
