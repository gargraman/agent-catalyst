"""Shared circuit-breaker state in Redis (feature 025 T023; contract api-and-data §5, research D5).

Implements `runtime.providers.resilience.BreakerStore` so every API replica sees the same breaker. Keys are
`ac:cb:{provider}:{model}:{class}:{fails|open|trial}` — registry ids only, no tenant data (FR-013):

- `fails`  consecutive transient failures (`INCR` + `PEXPIRE` 10 min, one MULTI/EXEC — no lost counts, FR-008)
- `open`   marker whose TTL IS the cooldown (`SET … PX cooldown`) — no cross-instance clock skew
- `trial`  the single half-open lease (`SET … NX PX trial_ttl`) — exactly one winner across instances (SC-003)

Commands used: EXISTS, GET, INCR, PEXPIRE, SET PX, SET NX PX, DEL, MULTI/EXEC. No Lua (prod parity with the rate
limiter; fakeredis has no `lupa`). Errors propagate — the runtime `Guard` falls back to its local store (B9).
"""
from __future__ import annotations

from runtime.providers.resilience import Admission, BreakerKey

_FAILS_TTL_MS = 600_000


def _k(key: BreakerKey, suffix: str) -> str:
    return f"ac:cb:{key.provider}:{key.model}:{key.cls}:{suffix}"


class RedisBreakerStore:
    def __init__(self, client, *, failure_threshold: int = 5, cooldown_s: float = 30.0) -> None:
        self.client = client
        self.failure_threshold = failure_threshold
        self.cooldown_ms = max(1, int(cooldown_s * 1000))

    async def _state(self, key: BreakerKey) -> tuple[bool, int]:
        pipe = self.client.pipeline(transaction=True)
        pipe.exists(_k(key, "open"))
        pipe.get(_k(key, "fails"))
        is_open, fails = await pipe.execute()
        return bool(is_open), int(fails or 0)

    async def admit(self, key: BreakerKey, *, also_check: BreakerKey | None = None,
                    trial_ttl_s: float) -> Admission:
        if also_check is not None:                       # B6: live unhealthy ⇒ batch refused
            live_open, live_fails = await self._state(also_check)
            if live_open or live_fails >= self.failure_threshold:
                return Admission.REFUSED
        is_open, fails = await self._state(key)
        if is_open:
            return Admission.REFUSED
        if fails >= self.failure_threshold:              # cooldown elapsed, not yet healed ⇒ half-open
            won = await self.client.set(_k(key, "trial"), 1, nx=True, px=max(1, int(trial_ttl_s * 1000)))
            return Admission.TRIAL if won else Admission.REFUSED
        return Admission.CLOSED

    async def record_success(self, key: BreakerKey, *, was_trial: bool) -> bool:
        await self.client.delete(_k(key, "fails"), _k(key, "trial"))
        return False

    async def record_failure(self, key: BreakerKey, *, was_trial: bool) -> bool:
        pipe = self.client.pipeline(transaction=True)
        pipe.incr(_k(key, "fails"))
        pipe.pexpire(_k(key, "fails"), _FAILS_TTL_MS)
        pipe.exists(_k(key, "open"))
        n, _, was_open = await pipe.execute()
        if not (was_trial or int(n) >= self.failure_threshold):
            return False
        pipe = self.client.pipeline(transaction=True)
        pipe.set(_k(key, "open"), 1, px=self.cooldown_ms)
        if was_trial:
            pipe.delete(_k(key, "trial"))
        await pipe.execute()
        return not bool(was_open)
