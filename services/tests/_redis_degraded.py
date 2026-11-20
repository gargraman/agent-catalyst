"""Redis-down test harness for the 021 degraded-mode matrix (FR-024/025).

A deterministic, infra-free way to put the shared cache into each state the degraded-mode decision record
documents, so the matrix asserts *behavior*, not a live outage:

- ``BoomRedis`` — a **configured-but-unreachable** Redis: `pipeline()` is sync (real `redis.asyncio` shape)
  but the awaited `execute()` raises `ConnectionError`; any direct op (`ping`, …) raises when awaited. This
  is the state that distinguishes the two opposite error policies — the deny-set (security) fails CLOSED on
  it, the rate limiter (availability) fails OPEN + alert.
- ``fake_up()`` — a working in-memory ``fakeredis`` client (Redis healthy).
- ``RedisHolder`` — a mutable swap so a single test can model "Redis comes back mid-run" without a restart.

Mirrors the ``_Boom`` shapes already used in ``test_caller_token_denyset.py`` / ``test_rate_limit.py`` so the
matrix and those suites agree on what "Redis down" means. UNSET Redis (``get_redis() -> None``) is modelled by
patching ``redis_client.get_redis`` to ``lambda: None`` directly in the test (parity / no-op-allow path).
"""
from __future__ import annotations

import fakeredis.aioredis


class _BoomPipe:
    """A pipeline whose queueing is sync+chainable but whose awaited ``execute()`` explodes — the real
    ``redis.asyncio`` shape, so the fail-closed/-open paths are exercised at the true failure point."""

    def execute(self):
        async def _raise():
            raise ConnectionError("redis down")
        return _raise()

    def __getattr__(self, name):            # exists()/zadd()/set()/… queue synchronously and chain
        return lambda *a, **k: self


class BoomRedis:
    """A configured-but-unreachable Redis. ``pipeline()`` is sync (real shape); ``execute()``/direct ops raise."""

    def pipeline(self, *a, **k):
        return _BoomPipe()

    def __getattr__(self, name):            # any direct op (ping, zrem, …) explodes when awaited
        async def _raise(*a, **k):
            raise ConnectionError("redis down")
        return _raise


def fake_up() -> fakeredis.aioredis.FakeRedis:
    """A working in-memory Redis (healthy) — the recovered / normal state."""
    return fakeredis.aioredis.FakeRedis()


class RedisHolder:
    """Mutable current-client holder so a test can swap the backend mid-run (Redis recovers without restart).

    Pass ``holder.get`` as the ``redis_client.get_redis`` replacement; flip ``holder.current`` to change what
    every subsequent control call sees.
    """

    def __init__(self, current=None):
        self.current = current

    def get(self):
        return self.current
