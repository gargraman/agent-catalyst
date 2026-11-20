"""T017 (feature 025, US2) — circuit breaker: `LocalBreakerStore` + its wiring into the gateway attempt loop
(contract runtime-resilience §2 S9/S10, §3 B1–B9, §5 embeddings, §7 observer events; research D5).

Store scenarios are shared with the Redis store (`_breaker_scenarios.py`). Gateway tests drive the real gateway with the
fault-injecting transport; the Guard gets a fake clock/sleep so cooldowns and waits cost ~0 wall time. No live calls.
"""
from __future__ import annotations

import dataclasses
import random
import time

import pytest

from runtime.providers import gateway, resilience, transport
from runtime.providers.gateway import ModelRetiredError
from runtime.providers.ir import EmbeddingError, EventType, NormalizedRequest
from runtime.providers.profiles import resolve_profile
from runtime.providers.resilience import Admission, BreakerKey
from runtime.providers.transport import TransportError

from tests import _breaker_scenarios as sc
from tests._fault_transport import FaultScript, fail, ok, partial


class FakeClock:
    def __init__(self) -> None:
        self.t = 5000.0
        self.waits: list[float] = []

    def now(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        self.waits.append(s)
        self.t += s

    async def advance(self, s: float) -> None:
        self.t += s


def _store(clock: FakeClock, **kw) -> "resilience.LocalBreakerStore":
    return resilience.LocalBreakerStore(failure_threshold=kw.pop("failure_threshold", sc.THRESHOLD),
                                        cooldown_s=kw.pop("cooldown_s", 30.0), clock=clock.now)


# --- B1–B8 + trial lease on the in-process store ------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(sc.SCENARIOS))
async def test_local_store_scenarios(name):
    clock = FakeClock()
    await sc.SCENARIOS[name](_store(clock), clock.advance, 30.0)


def test_local_store_satisfies_protocol():
    assert isinstance(_store(FakeClock()), resilience.BreakerStore)


# --- Guard ⇄ gateway wiring ----------------------------------------------------------------------------------------
LIVE = BreakerKey("openai", "gpt-5-mini", "live")
BATCH = BreakerKey("openai", "gpt-5-mini", "batch")


def _guard(clock: FakeClock, *, store=None, local_store=None, events: list | None = None, **policy):
    def observer(name: str, labels: dict) -> None:
        if events is not None:
            events.append((name, labels))
    pol = resilience.ResiliencePolicy(**policy)
    return resilience.Guard(policy=pol,
                            store=store if store is not None else _store(clock, failure_threshold=pol.failure_threshold,
                                                                         cooldown_s=pol.cooldown_s),
                            local_store=local_store, observer=observer, clock=clock.now, sleep=clock.sleep,
                            rng=random.Random(3))


def _req(model: str = "gpt-5-mini") -> NormalizedRequest:
    return NormalizedRequest(provider="openai", model=model, api_version=None,
                             messages=[{"role": "user", "content": "hi"}], system=None, params={})


async def _run(guard, *, traffic_class="live", timeout=30.0, req=None, profile=None):
    return [ev async for ev in gateway.stream(req or _req(), api_key="k", timeout=timeout, guard=guard,
                                              traffic_class=traffic_class, profile=profile)]


def _errors(events) -> list[dict]:
    return [e.data["error"] for e in events if e.type is EventType.ERROR]


async def _open(guard, key=LIVE) -> None:
    for _ in range(guard.policy.failure_threshold):
        await guard.store.record_failure(key, was_trial=False)


# S9: refusal → typed ERROR, attempts 0, zero transport calls, fast
async def test_s9_open_breaker_refuses_with_zero_transport_calls(monkeypatch):
    script = FaultScript([ok("never")]).install(monkeypatch)
    seen: list = []
    clock = FakeClock()
    guard = _guard(clock, events=seen)
    await _open(guard)
    t0 = time.perf_counter()
    events = await _run(guard)
    elapsed = time.perf_counter() - t0
    assert script.calls == 0
    assert [e.type for e in events] == [EventType.ERROR]
    [err] = _errors(events)
    assert err["type"] == "provider_unavailable" and err["retryable"] is True and err["attempts"] == 0
    assert err["message"] == "provider temporarily unavailable (circuit open)"
    assert events[0].data["attempts"] == 0
    assert elapsed < 0.05 and clock.waits == []
    assert ("breaker_refused", {"provider": "openai", "model": "gpt-5-mini", "cls": "live"}) in seen


# The attempt loop feeds the breaker: transient failures count per attempt
async def test_transient_attempt_failures_open_the_breaker(monkeypatch):
    script = FaultScript([fail(503)]).install(monkeypatch)
    guard = _guard(FakeClock(), max_attempts=1)
    for _ in range(5):
        assert _errors(await _run(guard))[0]["type"] == "provider_error"
    assert script.calls == 5
    assert _errors(await _run(guard))[0]["type"] == "provider_unavailable"
    assert script.calls == 5                                          # 6th call refused before the transport


async def test_exhausted_retries_count_every_attempt(monkeypatch):
    # default policy: 3 attempts per call; 2 exhausted calls = 6 transient failures ≥ 5 → third call refused
    script = FaultScript([fail(503)]).install(monkeypatch)
    guard = _guard(FakeClock())
    await _run(guard)
    await _run(guard)
    assert script.calls == 6
    assert _errors(await _run(guard))[0]["type"] == "provider_unavailable" and script.calls == 6


@pytest.mark.parametrize("status", [400, 401, 404, 409])
async def test_non_transient_failures_never_open_the_breaker(monkeypatch, status):
    script = FaultScript([fail(status)] * 10 + [ok("fine")]).install(monkeypatch)
    guard = _guard(FakeClock())
    for _ in range(10):
        await _run(guard)
    events = await _run(guard)
    assert script.calls == 11 and _errors(events) == []


async def test_failure_after_surfaced_event_counts_as_breaker_failure(monkeypatch):
    # S4: not retried, but it IS a transient provider failure → counts toward N
    script = FaultScript([partial("par", "tial")]).install(monkeypatch)
    guard = _guard(FakeClock())
    for _ in range(5):
        await _run(guard)
    assert script.calls == 5
    assert _errors(await _run(guard))[0]["type"] == "provider_unavailable"


async def test_success_resets_the_consecutive_count(monkeypatch):
    script = FaultScript([fail(503)] * 4 + [ok("ok")] + [fail(503)] * 4 + [ok("still admitted")]).install(monkeypatch)
    guard = _guard(FakeClock(), max_attempts=1)
    for _ in range(10):
        await _run(guard)
    assert script.calls == 10                                         # never refused: 4 < N on both sides of the success


# Half-open through the gateway: one trial; success closes, failure re-opens without retrying the trial
async def test_trial_success_closes_breaker(monkeypatch):
    script = FaultScript([ok("trial ok"), ok("normal")]).install(monkeypatch)
    seen: list = []
    clock = FakeClock()
    guard = _guard(clock, events=seen)
    await _open(guard)
    await clock.advance(31.0)
    assert _errors(await _run(guard)) == []
    assert _errors(await _run(guard)) == [] and script.calls == 2
    transitions = [labels["to"] for name, labels in seen if name == "breaker_transition"]
    assert transitions[-2:] == ["half_open", "closed"]


async def test_trial_failure_reopens_and_is_not_retried(monkeypatch):
    script = FaultScript([fail(503), ok("never")]).install(monkeypatch)
    clock = FakeClock()
    guard = _guard(clock)
    await _open(guard)
    await clock.advance(31.0)
    [err] = _errors(await _run(guard))
    assert err["type"] == "provider_error" and err["attempts"] == 1 and script.calls == 1
    assert _errors(await _run(guard))[0]["type"] == "provider_unavailable" and script.calls == 1


async def test_threshold_crossing_emits_open_transition(monkeypatch):
    FaultScript([fail(503)]).install(monkeypatch)
    seen: list = []
    guard = _guard(FakeClock(), events=seen, max_attempts=1)
    for _ in range(5):
        await _run(guard)
    assert ("breaker_transition", {"provider": "openai", "model": "gpt-5-mini", "cls": "live", "to": "open"}) in seen


# B6 / B7 through the gateway's traffic_class
async def test_live_open_refuses_batch_traffic(monkeypatch):
    script = FaultScript([ok("never")]).install(monkeypatch)
    guard = _guard(FakeClock())
    await _open(guard, LIVE)
    assert _errors(await _run(guard, traffic_class="batch"))[0]["type"] == "provider_unavailable"
    assert script.calls == 0


async def test_batch_open_still_admits_live_traffic(monkeypatch):
    script = FaultScript([ok("live ok")]).install(monkeypatch)
    guard = _guard(FakeClock())
    await _open(guard, BATCH)
    assert _errors(await _run(guard, traffic_class="live")) == [] and script.calls == 1


async def test_batch_failures_are_recorded_on_the_batch_key(monkeypatch):
    FaultScript([fail(503)]).install(monkeypatch)
    clock = FakeClock()
    guard = _guard(clock, max_attempts=1)
    for _ in range(5):
        await _run(guard, traffic_class="batch")
    assert await guard.store.admit(BATCH, trial_ttl_s=35.0) is Admission.REFUSED
    assert await guard.store.admit(LIVE, trial_ttl_s=35.0) is Admission.CLOSED


# S10: config errors raise before admission; the store is never touched
class SpyStore:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def admit(self, key, *, also_check=None, trial_ttl_s):
        self.calls.append("admit")
        return Admission.CLOSED

    async def record_success(self, key, *, was_trial):
        self.calls.append("success")

    async def record_failure(self, key, *, was_trial):
        self.calls.append("failure")


async def test_s10_unknown_model_raises_before_admission(monkeypatch):
    FaultScript([ok("never")]).install(monkeypatch)
    spy = SpyStore()
    with pytest.raises(FileNotFoundError):
        await _run(_guard(FakeClock(), store=spy), req=_req("no-such-model"))
    assert spy.calls == []


async def test_s10_retired_model_raises_before_admission(monkeypatch):
    FaultScript([ok("never")]).install(monkeypatch)
    spy = SpyStore()
    retired = dataclasses.replace(resolve_profile("openai", "gpt-5-mini", None), availability="retired")
    with pytest.raises(ModelRetiredError):
        await _run(_guard(FakeClock(), store=spy), profile=retired)
    assert spy.calls == []


# B9: store outage → local store for this call, `breaker_degraded`, never refused because of the outage
class BrokenStore:
    async def admit(self, key, *, also_check=None, trial_ttl_s):
        raise ConnectionError("redis down")

    async def record_success(self, key, *, was_trial):
        raise ConnectionError("redis down")

    async def record_failure(self, key, *, was_trial):
        raise ConnectionError("redis down")


async def test_b9_store_outage_falls_back_to_local_and_still_calls(monkeypatch):
    script = FaultScript([ok("served")]).install(monkeypatch)
    seen: list = []
    clock = FakeClock()
    local = _store(clock)
    guard = _guard(clock, store=BrokenStore(), local_store=local, events=seen)
    events = await _run(guard)
    assert script.calls == 1 and _errors(events) == []
    assert ("breaker_degraded", {}) in seen


async def test_b9_failures_during_outage_are_tracked_locally(monkeypatch):
    script = FaultScript([fail(503)]).install(monkeypatch)
    clock = FakeClock()
    local = _store(clock)
    guard = _guard(clock, store=BrokenStore(), local_store=local, max_attempts=1)
    for _ in range(5):
        await _run(guard)
    assert await local.admit(LIVE, trial_ttl_s=35.0) is Admission.REFUSED   # the local breaker still protects
    assert _errors(await _run(guard))[0]["type"] == "provider_unavailable" and script.calls == 5


async def test_guard_builds_its_own_local_stores_by_default():
    guard = resilience.Guard(resilience.ResiliencePolicy())
    assert isinstance(guard.store, resilience.LocalBreakerStore)
    assert isinstance(guard.local_store, resilience.LocalBreakerStore)


# §5: embeddings — refusal is a typed EmbeddingError, zero transport calls
async def test_embed_refused_by_open_breaker(monkeypatch):
    calls: list = []

    async def never(**kwargs):
        calls.append(kwargs)
        raise AssertionError("transport must not be called")
    monkeypatch.setattr(transport, "embed", never)
    guard = _guard(FakeClock())
    await _open(guard, BreakerKey("openai", "text-embedding-3-small", "batch"))
    with pytest.raises(EmbeddingError) as ei:
        await gateway.embed(["a"], provider="openai", model="text-embedding-3-small", api_key="k",
                            guard=guard, traffic_class="batch")
    assert ei.value.type == "provider_unavailable" and ei.value.retryable is True and ei.value.attempts == 0
    assert calls == []


async def test_embed_transient_failures_feed_the_breaker(monkeypatch):
    async def down(**kwargs):
        raise TransportError("injected 503", retryable=True, status=503)
    monkeypatch.setattr(transport, "embed", down)
    guard = _guard(FakeClock(), max_attempts=1)
    for _ in range(5):
        with pytest.raises(EmbeddingError):
            await gateway.embed(["a"], provider="openai", model="text-embedding-3-small", api_key="k", guard=guard)
    key = BreakerKey("openai", "text-embedding-3-small", "live")
    assert await guard.store.admit(key, trial_ttl_s=35.0) is Admission.REFUSED
