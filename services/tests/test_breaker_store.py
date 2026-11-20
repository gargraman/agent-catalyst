"""T018 (feature 025, US2) — `api.breaker_store.RedisBreakerStore`: the shared breaker state (contract api §5,
runtime-resilience §3, research D5/D13; spec FR-008, SC-003, B9).

Unit tier: fakeredis (no `lupa` installed ⇒ any Lua `EVAL` would fail — the store must use plain commands + MULTI/EXEC).
Breaker cooldowns here are real Redis TTLs, so the scenarios use a 50 ms cooldown and real `asyncio.sleep`.
The integration tier against compose Redis is T026 (verification, appended later). No live provider calls.
"""
from __future__ import annotations

import asyncio
import random

import fakeredis.aioredis
import pytest

from runtime.providers import gateway, resilience
from runtime.providers.ir import EventType, NormalizedRequest
from runtime.providers.resilience import Admission, BreakerKey

from tests import _breaker_scenarios as sc
from tests._fault_transport import FaultScript, ok
from tests._redis_degraded import BoomRedis

COOLDOWN = 0.05


def _redis_store(client=None, **kw):
    from api.breaker_store import RedisBreakerStore
    return RedisBreakerStore(client if client is not None else fakeredis.aioredis.FakeRedis(),
                             failure_threshold=kw.get("failure_threshold", sc.THRESHOLD),
                             cooldown_s=kw.get("cooldown_s", COOLDOWN))


async def _real_sleep(s: float) -> None:
    await asyncio.sleep(s)


# --- the same B1–B8 + trial-lease scenarios as the local store ------------------------------------------------------
@pytest.mark.parametrize("name", sorted(sc.SCENARIOS))
async def test_redis_store_scenarios(name):
    await sc.SCENARIOS[name](_redis_store(), _real_sleep, COOLDOWN)


def test_redis_store_satisfies_protocol():
    assert isinstance(_redis_store(), resilience.BreakerStore)


async def test_keys_are_provider_model_class_only():
    client = fakeredis.aioredis.FakeRedis()
    store = _redis_store(client)
    key = BreakerKey("openai", "gpt-5-mini", "live")
    for _ in range(sc.THRESHOLD):
        await store.record_failure(key, was_trial=False)
    await asyncio.sleep(COOLDOWN * 1.5)
    assert await store.admit(key, trial_ttl_s=1.0) is Admission.TRIAL
    keys = sorted(k.decode() if isinstance(k, bytes) else k for k in await client.keys("*"))
    assert keys and all(k.startswith("ac:cb:openai:gpt-5-mini:live:") for k in keys)
    assert {k.rsplit(":", 1)[1] for k in keys} <= {"fails", "open", "trial"}
    assert "trial" in {k.rsplit(":", 1)[1] for k in keys}
    # FR-013: no tenant identifiers in breaker keys
    assert all(len(k.split(":")) == 6 for k in keys)                 # ac:cb:{p}:{m}:{cls}:{suffix} — nothing else


async def test_fails_key_has_a_ttl():
    client = fakeredis.aioredis.FakeRedis()
    store = _redis_store(client)
    await store.record_failure(BreakerKey("openai", "gpt-5-mini", "live"), was_trial=False)
    pttl = await client.pttl("ac:cb:openai:gpt-5-mini:live:fails")
    assert 0 < pttl <= 600_000                                         # PEXPIRE 10 min (D5)


async def test_concurrent_failures_lose_no_counts():
    client = fakeredis.aioredis.FakeRedis()
    store = _redis_store(client, failure_threshold=1000)
    key = BreakerKey("openai", "gpt-5-mini", "live")
    await asyncio.gather(*(store.record_failure(key, was_trial=False) for _ in range(50)))
    assert int(await client.get("ac:cb:openai:gpt-5-mini:live:fails")) == 50     # FR-008


# --- SC-003: three instances sharing one Redis → exactly one trial per cooldown window -----------------------------
def _req() -> NormalizedRequest:
    return NormalizedRequest(provider="openai", model="gpt-5-mini", api_version=None,
                             messages=[{"role": "user", "content": "hi"}], system=None, params={})


async def test_three_instances_one_trial_per_cooldown(monkeypatch):
    # The trial holds the provider for 50 ms so the other instances admit WHILE it is in flight (fakeredis never yields
    # to the loop on its own; without the delay the trial would finish and close the breaker first — valid B3, but
    # then the test would not exercise single-flight).
    script = FaultScript([ok("trial", delay_s=0.05)]).install(monkeypatch)
    server = fakeredis.FakeServer()
    guards = [resilience.Guard(resilience.ResiliencePolicy(cooldown_s=COOLDOWN),
                               store=_redis_store(fakeredis.aioredis.FakeRedis(server=server)),
                               rng=random.Random(i))
              for i in range(3)]
    key = BreakerKey("openai", "gpt-5-mini", "live")
    for _ in range(sc.THRESHOLD):
        await guards[0].store.record_failure(key, was_trial=False)
    for g in guards:                                                    # every instance sees "open"
        assert await g.store.admit(key, trial_ttl_s=1.0) is Admission.REFUSED
    await asyncio.sleep(COOLDOWN * 1.5)

    async def call(g):
        return [ev async for ev in gateway.stream(_req(), api_key="k", timeout=5.0, guard=g)]
    results = await asyncio.gather(*(call(g) for g in guards))
    refused = [r for r in results if any(e.type is EventType.ERROR
                                         and e.data["error"]["type"] == "provider_unavailable" for e in r)]
    assert script.calls == 1 and len(refused) == 2
    # the trial succeeded → closed for every instance sharing the store (B3)
    for g in guards:
        assert await g.store.admit(key, trial_ttl_s=1.0) is Admission.CLOSED


# --- B9: Redis configured but unreachable → local fallback + breaker_degraded; call still attempted ----------------
async def test_store_outage_falls_back_local(monkeypatch):
    script = FaultScript([ok("served")]).install(monkeypatch)
    seen: list = []
    guard = resilience.Guard(resilience.ResiliencePolicy(), store=_redis_store(BoomRedis()),
                             observer=lambda name, labels: seen.append(name))
    events = [ev async for ev in gateway.stream(_req(), api_key="k", timeout=5.0, guard=guard)]
    assert script.calls == 1
    assert not any(e.type is EventType.ERROR for e in events)
    assert "breaker_degraded" in seen


# --- API wiring: Redis UNSET → LocalBreakerStore, not "degraded" -----------------------------------------------------
async def test_wiring_without_redis_uses_local_store_and_is_not_degraded(monkeypatch):
    from api import metrics, redis_client, resilience_wiring
    monkeypatch.setattr(redis_client, "get_redis", lambda: None)
    resilience_wiring.reset_for_tests()
    try:
        guard = resilience_wiring.get_guard()
        assert isinstance(guard.store, resilience.LocalBreakerStore)
        before = metrics.ac_breaker_degraded_total._value.get()
        FaultScript([ok("served")]).install(monkeypatch)
        events = [ev async for ev in gateway.stream(_req(), api_key="k", timeout=5.0, guard=guard)]
        assert not any(e.type is EventType.ERROR for e in events)
        assert metrics.ac_breaker_degraded_total._value.get() == before
    finally:
        resilience_wiring.reset_for_tests()


async def test_wiring_with_redis_uses_redis_store(monkeypatch):
    from api import redis_client, resilience_wiring
    from api.breaker_store import RedisBreakerStore
    client = fakeredis.aioredis.FakeRedis()
    monkeypatch.setattr(redis_client, "get_redis", lambda: client)
    resilience_wiring.reset_for_tests()
    try:
        guard = resilience_wiring.get_guard()
        assert isinstance(guard.store, RedisBreakerStore)
        assert isinstance(guard.local_store, resilience.LocalBreakerStore)
        assert resilience_wiring.get_guard() is guard                  # process singleton
    finally:
        resilience_wiring.reset_for_tests()


# ---- T026 (US2) verification — real Redis (compose, host :6380). Exercises code built in T023; expected green on first
# run (constitution §9: verification evidence, not a Red→Green pair). Uses a dedicated logical DB and cleans its keys.
_REAL_REDIS = "redis://localhost:6380/14"


async def _real_client():
    from redis.asyncio import Redis
    client = Redis.from_url(_REAL_REDIS, decode_responses=False)
    try:
        await client.ping()
    except Exception:  # noqa: BLE001
        await client.aclose()
        pytest.skip("compose Redis not reachable on :6380 (make up)")
    for k in await client.keys("ac:cb:*"):
        await client.delete(k)
    return client


@pytest.mark.integration
async def test_real_redis_three_instances_one_trial_per_cooldown(monkeypatch):
    client = await _real_client()
    try:
        script = FaultScript([ok("trial", delay_s=0.05)]).install(monkeypatch)
        guards = [resilience.Guard(resilience.ResiliencePolicy(cooldown_s=COOLDOWN),
                                   store=_redis_store(client), rng=random.Random(i)) for i in range(3)]
        key = BreakerKey("openai", "gpt-5-mini", "live")
        for _ in range(sc.THRESHOLD):
            await guards[0].store.record_failure(key, was_trial=False)
        await asyncio.sleep(COOLDOWN * 1.5)

        async def call(g):
            return [ev async for ev in gateway.stream(_req(), api_key="k", timeout=5.0, guard=g)]
        results = await asyncio.gather(*(call(g) for g in guards))
        refused = [r for r in results if any(e.type is EventType.ERROR
                                             and e.data["error"]["type"] == "provider_unavailable" for e in r)]
        assert script.calls == 1 and len(refused) == 2
        assert await guards[1].store.admit(key, trial_ttl_s=1.0) is Admission.CLOSED
    finally:
        for k in await client.keys("ac:cb:*"):
            await client.delete(k)
        await client.aclose()


@pytest.mark.integration
async def test_real_redis_closed_port_falls_back_local(monkeypatch):
    from redis.asyncio import Redis
    dead = Redis.from_url("redis://localhost:1/0", socket_connect_timeout=0.2)   # nothing listens on port 1
    script = FaultScript([ok("served")]).install(monkeypatch)
    seen: list = []
    guard = resilience.Guard(resilience.ResiliencePolicy(), store=_redis_store(dead),
                             observer=lambda name, labels: seen.append(name))
    try:
        events = [ev async for ev in gateway.stream(_req(), api_key="k", timeout=5.0, guard=guard)]
    finally:
        await dead.aclose()
    assert script.calls == 1 and not any(e.type is EventType.ERROR for e in events)
    assert "breaker_degraded" in seen
