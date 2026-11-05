"""F8/FR-021/SC-008 — the eval-fan-out circuit breaker is instance-scoped and independent of any other
breaker. Opens after N consecutive failures, fails fast while open, half-opens after the reset timeout."""
from __future__ import annotations

import asyncio

import pytest

from evals.breaker import CircuitBreaker, CircuitOpenError


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


async def _fail():
    raise RuntimeError("provider down")


def test_opens_after_threshold_and_fails_fast():
    clock = _Clock()
    cb = CircuitBreaker(failure_threshold=2, reset_timeout_s=10.0, clock=clock)
    called = {"n": 0}

    async def ok():
        called["n"] += 1
        return "ok"

    async def go():
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await cb.call(_fail)              # 2 consecutive failures → open
        with pytest.raises(CircuitOpenError):     # now open → fail fast, fn NOT invoked
            await cb.call(ok)

    asyncio.run(go())
    assert called["n"] == 0                        # the ok fn was never called while open


def test_half_open_then_success_closes():
    clock = _Clock()
    cb = CircuitBreaker(failure_threshold=1, reset_timeout_s=10.0, clock=clock)

    async def ok():
        return "ok"

    async def go():
        with pytest.raises(RuntimeError):
            await cb.call(_fail)                   # trips open (threshold 1)
        with pytest.raises(CircuitOpenError):
            await cb.call(ok)
        clock.t += 10.0                            # advance past reset → half-open
        return await cb.call(ok)                   # success closes it

    assert asyncio.run(go()) == "ok"


def test_two_instances_share_no_trip_state():
    """F8 — the whole point: tripping one breaker must leave another (e.g. a future prod breaker) closed."""
    clock = _Clock()
    cb_eval = CircuitBreaker(failure_threshold=1, reset_timeout_s=10.0, clock=clock)
    cb_other = CircuitBreaker(failure_threshold=1, reset_timeout_s=10.0, clock=clock)

    async def ok():
        return "ok"

    async def go():
        with pytest.raises(RuntimeError):
            await cb_eval.call(_fail)              # trip ONLY cb_eval
        # cb_other is untouched → still closed → executes normally
        return await cb_other.call(ok)

    assert asyncio.run(go()) == "ok"
