"""Eval-fan-out circuit breaker (M3, feature 004) — F8/FR-021.

A deliberately small, **instance-scoped** breaker: the eval runner owns its own instance, so a judge/candidate
fan-out storm trips only this breaker and never a production one (there is no prod breaker yet — grep-verified;
keeping state per-instance means a future prod breaker is independent by construction). No module-global state.
`clock` is injectable for deterministic tests.
"""
from __future__ import annotations

import time
from typing import Awaitable, Callable


class CircuitOpenError(Exception):
    """Raised by a call made while the breaker is open (fail fast, do not invoke the wrapped fn)."""


class CircuitBreaker:
    def __init__(self, *, failure_threshold: int = 5, reset_timeout_s: float = 30.0,
                 clock: Callable[[], float] = time.monotonic):
        self.failure_threshold = failure_threshold
        self.reset_timeout_s = reset_timeout_s
        self._clock = clock
        self._state: str = "closed"            # closed | open | half_open — per instance, never shared (F8)
        self._consecutive_failures = 0
        self._opened_at = 0.0

    @property
    def state(self) -> str:
        return self._state

    async def call(self, coro_fn: Callable[..., Awaitable], *args, **kwargs):
        if self._state == "open":
            if self._clock() - self._opened_at >= self.reset_timeout_s:
                self._state = "half_open"       # allow one trial call through
            else:
                raise CircuitOpenError("eval circuit breaker is open — failing fast to protect the provider")
        try:
            result = await coro_fn(*args, **kwargs)
        except Exception:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.failure_threshold:
                self._state = "open"
                self._opened_at = self._clock()
            raise
        else:
            self._consecutive_failures = 0
            self._state = "closed"
            return result
