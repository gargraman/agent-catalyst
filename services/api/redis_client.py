"""Shared async Redis client (feature 016 / F5) — the first consumer of the `AC_REDIS_URL` slot.

A single process-wide lazy connection pool so the rate limiter (016), the caller-token deny-set (018),
and degraded-mode checks (021) share one client instead of each opening their own. `get_redis()` returns
`None` when `AC_REDIS_URL` is unset so callers degrade to a no-op-allow (parity with the pre-016 seam —
spec Assumptions). Importing this module never connects; the pool is built on first `get_redis()`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from api.settings import get_settings

if TYPE_CHECKING:  # pragma: no cover — type-only import, keeps module import cheap/infra-free
    from redis.asyncio import Redis

_client: "Redis | None" = None
_initialized = False


def get_redis() -> "Redis | None":
    """Return the shared async Redis client, or ``None`` when ``AC_REDIS_URL`` is unset (no-op-allow).

    Lazy: the pool is built on first call from `AC_REDIS_URL`, then memoized process-wide. `decode_responses`
    is left False — the limiter works in bytes and never needs string decoding on the hot path.
    """
    global _client, _initialized
    if not _initialized:
        url = get_settings().redis_url
        if url:
            from redis.asyncio import Redis
            _client = Redis.from_url(url, decode_responses=False)
        _initialized = True
    return _client


async def is_healthy() -> bool:
    """Liveness ping for readiness (`/readyz`). ``False`` when unconfigured or unreachable (never raises)."""
    client = get_redis()
    if client is None:
        return False
    try:
        return bool(await client.ping())
    except Exception:  # noqa: BLE001 — readiness probe must never raise
        return False


def reset_for_tests() -> None:
    """Drop the memoized client so a test that changes `AC_REDIS_URL` re-initializes (test-only)."""
    global _client, _initialized
    _client = None
    _initialized = False
