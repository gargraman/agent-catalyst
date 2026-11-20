"""Process-wide provider-resilience Guard for the API (feature 025 T025; research D13, contract api §3/§5).

One `Guard` per process: `ResiliencePolicy` from settings, `RedisBreakerStore` when `AC_REDIS_URL` is configured
(shared across replicas) else `LocalBreakerStore` (unset Redis is a supported mode, NOT "degraded"), and an observer
that feeds `api.metrics`. Callers pass it on `RunContext.guard` (invoke, evals) or to `gateway.embed` (ingestion).
"""
from __future__ import annotations

import asyncio

from api import metrics, redis_client
from api.settings import Settings, get_settings
from runtime.providers.resilience import Guard, ResiliencePolicy

_GUARD: Guard | None = None
_sleep = asyncio.sleep          # injection point: the test suite swaps in an instant sleep


def policy_from_settings(s: Settings) -> ResiliencePolicy:
    return ResiliencePolicy(max_attempts=s.provider_max_attempts, backoff_base_s=s.provider_backoff_base_s,
                            per_wait_cap_s=s.provider_wait_cap_s, failure_threshold=s.breaker_failure_threshold,
                            cooldown_s=s.breaker_cooldown_s, batch_concurrency=s.batch_concurrency)


def get_guard() -> Guard:
    global _GUARD
    if _GUARD is None:
        policy = policy_from_settings(get_settings())
        client = redis_client.get_redis()
        store = None
        if client is not None:
            from api.breaker_store import RedisBreakerStore
            store = RedisBreakerStore(client, failure_threshold=policy.failure_threshold, cooldown_s=policy.cooldown_s)
        _GUARD = Guard(policy, store=store, observer=metrics.record_resilience_event, sleep=_sleep)
    return _GUARD


def reset_for_tests() -> None:
    global _GUARD
    _GUARD = None
