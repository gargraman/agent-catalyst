"""Sliding-window rate-limit engine (feature 016 / F5) — identity-agnostic by design.

`check(key, limit, window_s)` takes an opaque key so the SAME engine serves per-principal keying here (016)
and per-caller-token keying in 018 with no rewrite. It returns a `Decision`; it never raises for a *limit*
breach (that is the caller's 429) and never raises for a Redis outage either — on any backend error it
returns `Decision(allowed=True, degraded=True)` (fail OPEN + alert; availability control — spec FR-024a).
The invariant `degraded=True ⇒ allowed=True` is what keeps the limiter from self-inflicting an outage.

Algorithm — sliding-window *log* in a Redis sorted set (`ZSET`): members are unique per hit, scored by
arrival time (ms). One `MULTI`/`EXEC` pipeline atomically trims entries older than the window, records this
hit, and counts the window; a denied hit is compensated with a `ZREM` so a rejected request never consumes a
slot. A log (not fixed counters) is why a burst straddling a window boundary cannot double-count (spec Edge
Cases). Pipeline-transaction atomicity is equivalent to the Lua approach for this op and keeps the test path
free of the `lupa` Lua dependency.
"""
from __future__ import annotations

import math
import secrets
import time
from dataclasses import dataclass

from api import redis_client


@dataclass(frozen=True)
class Decision:
    """Outcome of one limiter check.

    `degraded=True` means the backend was unavailable and we failed open — in that case `allowed` is ALWAYS
    True (invariant enforced in `__post_init__`). `remaining`/`reset_epoch`/`retry_after` feed the
    `X-RateLimit-*` / `Retry-After` response headers.
    """
    allowed: bool
    remaining: int
    reset_epoch: int
    retry_after: int
    degraded: bool = False

    def __post_init__(self) -> None:
        if self.degraded and not self.allowed:  # availability control: degraded must never reject
            raise ValueError("invariant violated: degraded=True requires allowed=True (fail open)")


def _now_ms() -> int:
    """Wall clock in ms (window math + test time control). Isolated so tests can advance it."""
    return int(time.time() * 1000)


def _allow(limit: int, now_ms: int, window_ms: int, *, degraded: bool) -> Decision:
    """No-op / fail-open allow (Redis unset or unavailable): full budget, nothing to retry."""
    return Decision(
        allowed=True, remaining=limit, reset_epoch=(now_ms + window_ms) // 1000,
        retry_after=0, degraded=degraded,
    )


async def check(key: str, limit: int, window_s: int) -> Decision:
    """Count this hit against `key`'s sliding `window_s` window; allow until `limit` is exceeded.

    No Redis configured → no-op allow (parity with the pre-016 seam, not degraded). Any backend error →
    fail OPEN + `degraded=True` (the caller raises the alert). Never raises.
    """
    client = redis_client.get_redis()
    now = _now_ms()
    window_ms = window_s * 1000
    if client is None:
        return _allow(limit, now, window_ms, degraded=False)

    member = f"{now}-{secrets.token_hex(8)}"
    try:
        pipe = client.pipeline(transaction=True)
        pipe.zremrangebyscore(key, 0, now - window_ms)   # drop entries older than the window
        pipe.zadd(key, {member: now})                     # record this hit
        pipe.zcard(key)                                   # count the window (incl. this hit)
        pipe.zrange(key, 0, 0, withscores=True)           # oldest in-window entry → reset time
        pipe.pexpire(key, window_ms)                       # bound key lifetime to the window
        results = await pipe.execute()
        count = int(results[2])
        oldest = results[3]
        oldest_score = int(oldest[0][1]) if oldest else now
    except Exception:  # noqa: BLE001 — availability control: never self-inflict an outage (FR-024a)
        return _allow(limit, now, window_ms, degraded=True)

    reset_ms = oldest_score + window_ms
    reset_epoch = reset_ms // 1000
    if count > limit:
        # Over limit: don't let a rejected hit consume a slot (fair recovery) — compensate best-effort.
        try:
            await client.zrem(key, member)
        except Exception:  # noqa: BLE001 — compensation is best-effort; the entry ages out anyway
            pass
        retry_after = max(1, math.ceil((reset_ms - now) / 1000))
        return Decision(allowed=False, remaining=0, reset_epoch=reset_epoch,
                        retry_after=retry_after, degraded=False)
    return Decision(allowed=True, remaining=max(0, limit - count), reset_epoch=reset_epoch,
                    retry_after=0, degraded=False)
