"""Immediate-revocation deny-set (feature 018) — the transient half of the caller-token plane.

A Redis set of revoked subjects / token-ids. Presence rejects a request on its NEXT auth decision — both
console sessions and caller tokens — without waiting for the next IdP login (FR-013). Keyed by subject
(`deny:sub:{subject}`) and token-id (`deny:tok:{token_id}`), each with TTL ≥ the max token/session lifetime
(FR-014) so a revoked subject can't outlive the set.

Error policy is the INVERSE of the 016 rate limiter, deliberately. The limiter is an availability control and
fails OPEN on a Redis outage; the deny-set is a SECURITY control and fails CLOSED (FR-024b): a configured-but-
unreachable Redis makes `is_denied` RAISE, and every caller converts that to a 401. This module is kept separate
from `ratelimit` precisely so the two opposite error policies can't be confused.

Parity carve-out: when `AC_REDIS_URL` is UNSET (dev/test with no Redis), there is no deny-set to consult and
`is_denied` returns False (no-op allow) — the same parity the limiter and the pre-016 seam use. Only a
*configured* Redis that then errors trips the fail-closed path.
"""
from __future__ import annotations

from api import redis_client


def _sub_key(subject: str) -> str:
    return f"deny:sub:{subject}"


def _tok_key(token_id: str) -> str:
    return f"deny:tok:{token_id}"


async def is_denied(subject: str | None, token_id: str | None) -> bool:
    """True iff `subject` or `token_id` is on the deny-set. Returns False when Redis is unconfigured
    (dev/test parity); RAISES on a configured-Redis error so the caller fails CLOSED → 401 (FR-024b).

    Deliberately does NOT swallow backend errors (the opposite of `ratelimit.check`): a deny-set that can't
    be consulted must not silently admit a revoked subject.
    """
    client = redis_client.get_redis()
    if client is None:
        return False                                      # nothing to consult → allow (parity, no-op)
    keys = [_sub_key(subject)] if subject else []
    if token_id:
        keys.append(_tok_key(token_id))
    if not keys:
        return False
    pipe = client.pipeline(transaction=False)
    for k in keys:
        pipe.exists(k)
    results = await pipe.execute()                         # ConnectionError here propagates → fail closed
    return any(int(r) for r in results)


async def deny(*, subject: str | None = None, token_id: str | None = None, ttl_s: int) -> None:
    """Add `subject` and/or `token_id` to the deny-set, each with a `ttl_s`-second lifetime. No-op when Redis
    is unconfigured. Raises on a configured-Redis error so a revoke that didn't take is surfaced, not silent."""
    client = redis_client.get_redis()
    if client is None:
        return
    pipe = client.pipeline(transaction=True)
    if subject:
        pipe.set(_sub_key(subject), b"1", ex=ttl_s)
    if token_id:
        pipe.set(_tok_key(token_id), b"1", ex=ttl_s)
    await pipe.execute()                                   # ConnectionError here propagates (not silent)
