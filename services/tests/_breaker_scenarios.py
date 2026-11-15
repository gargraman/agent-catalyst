"""Shared breaker-store scenarios (feature 025, contract runtime-resilience §3 B1–B8 + trial lease).

One body of assertions, run against every `BreakerStore` implementation: `LocalBreakerStore` with a fake clock
(T017, `test_resilience_breaker.py`) and `RedisBreakerStore` over fakeredis with real-time TTLs (T018,
`test_breaker_store.py`). Each scenario takes `(store, advance, cooldown)`: `advance(seconds)` moves the store's notion
of time (fake clock bump, or a real `asyncio.sleep` for Redis TTLs); `cooldown` is the store's configured cooldown.
The store is built with `failure_threshold=5`.
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from runtime.providers.resilience import Admission, BreakerKey

Advance = Callable[[float], Awaitable[None]]

THRESHOLD = 5
LIVE_A = BreakerKey("openai", "gpt-5-mini", "live")
LIVE_B = BreakerKey("openai", "gpt-5", "live")
BATCH_A = BreakerKey("openai", "gpt-5-mini", "batch")


def ttl(cooldown: float) -> float:
    """Trial lease used by the scenarios — comfortably longer than the cooldown so it never expires mid-scenario."""
    return cooldown * 4


async def _fail(store, key, n: int, *, was_trial: bool = False) -> None:
    for _ in range(n):
        await store.record_failure(key, was_trial=was_trial)


async def _open(store, key) -> None:
    await _fail(store, key, THRESHOLD)


async def b1_threshold_opens(store, advance: Advance, cooldown: float) -> None:
    await _fail(store, LIVE_A, THRESHOLD - 1)
    assert await store.admit(LIVE_A, trial_ttl_s=ttl(cooldown)) is Admission.CLOSED   # N−1 failures: still closed
    await _fail(store, LIVE_A, 1)
    assert await store.admit(LIVE_A, trial_ttl_s=ttl(cooldown)) is Admission.REFUSED  # N-th failure opens


async def b2_single_trial_after_cooldown(store, advance: Advance, cooldown: float) -> None:
    await _open(store, LIVE_A)
    await advance(cooldown * 1.5)
    results = await asyncio.gather(*(store.admit(LIVE_A, trial_ttl_s=ttl(cooldown)) for _ in range(10)))
    assert sorted(r.value for r in results) == ["refused"] * 9 + ["trial"]


async def b3_trial_success_closes(store, advance: Advance, cooldown: float) -> None:
    await _open(store, LIVE_A)
    await advance(cooldown * 1.5)
    assert await store.admit(LIVE_A, trial_ttl_s=ttl(cooldown)) is Admission.TRIAL
    await store.record_success(LIVE_A, was_trial=True)
    assert await store.admit(LIVE_A, trial_ttl_s=ttl(cooldown)) is Admission.CLOSED
    assert await store.admit(LIVE_A, trial_ttl_s=ttl(cooldown)) is Admission.CLOSED
    await _fail(store, LIVE_A, THRESHOLD - 1)                                         # count was reset by the heal
    assert await store.admit(LIVE_A, trial_ttl_s=ttl(cooldown)) is Admission.CLOSED


async def b4_trial_failure_reopens(store, advance: Advance, cooldown: float) -> None:
    await _open(store, LIVE_A)
    await advance(cooldown * 1.5)
    assert await store.admit(LIVE_A, trial_ttl_s=ttl(cooldown)) is Admission.TRIAL
    await store.record_failure(LIVE_A, was_trial=True)
    assert await store.admit(LIVE_A, trial_ttl_s=ttl(cooldown)) is Admission.REFUSED  # open for another cooldown
    await advance(cooldown * 1.5)
    assert await store.admit(LIVE_A, trial_ttl_s=ttl(cooldown)) is Admission.TRIAL    # …then a fresh single trial


async def b5_models_are_independent(store, advance: Advance, cooldown: float) -> None:
    await _open(store, LIVE_A)
    assert await store.admit(LIVE_A, trial_ttl_s=ttl(cooldown)) is Admission.REFUSED
    assert await store.admit(LIVE_B, trial_ttl_s=ttl(cooldown)) is Admission.CLOSED


async def b6_live_open_refuses_batch(store, advance: Advance, cooldown: float) -> None:
    await _open(store, LIVE_A)
    assert await store.admit(BATCH_A, also_check=LIVE_A, trial_ttl_s=ttl(cooldown)) is Admission.REFUSED


async def b7_batch_open_admits_live(store, advance: Advance, cooldown: float) -> None:
    await _open(store, BATCH_A)
    assert await store.admit(BATCH_A, also_check=LIVE_A, trial_ttl_s=ttl(cooldown)) is Admission.REFUSED
    assert await store.admit(LIVE_A, trial_ttl_s=ttl(cooldown)) is Admission.CLOSED


async def b8_success_resets_consecutive_count(store, advance: Advance, cooldown: float) -> None:
    await _fail(store, LIVE_A, THRESHOLD - 1)
    await store.record_success(LIVE_A, was_trial=False)
    await _fail(store, LIVE_A, THRESHOLD - 1)
    assert await store.admit(LIVE_A, trial_ttl_s=ttl(cooldown)) is Admission.CLOSED


async def trial_lease_expires(store, advance: Advance, cooldown: float) -> None:
    """A trial holder that never reports (crashed) frees the lease after `trial_ttl_s` (D5: deadline + 5 s)."""
    await _open(store, LIVE_A)
    await advance(cooldown * 1.5)
    short = cooldown                                                                  # lease shorter than next advance
    assert await store.admit(LIVE_A, trial_ttl_s=short) is Admission.TRIAL
    assert await store.admit(LIVE_A, trial_ttl_s=short) is Admission.REFUSED          # lease held
    await advance(short * 1.5)
    assert await store.admit(LIVE_A, trial_ttl_s=short) is Admission.TRIAL            # lease expired → new trial


SCENARIOS = {
    "B1": b1_threshold_opens,
    "B2": b2_single_trial_after_cooldown,
    "B3": b3_trial_success_closes,
    "B4": b4_trial_failure_reopens,
    "B5": b5_models_are_independent,
    "B6": b6_live_open_refuses_batch,
    "B7": b7_batch_open_admits_live,
    "B8": b8_success_resets_consecutive_count,
    "trial_lease": trial_lease_expires,
}
