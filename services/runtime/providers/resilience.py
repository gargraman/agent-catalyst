"""Provider resilience (feature 025): bounded retry, circuit breaker, live/batch bulkhead.

Built: types (T008), bounded retry via `Guard` (T013), circuit breaker + `LocalBreakerStore` (T022), and the batch
bulkhead `Guard.bulkhead` (T030).
Pure library — no redis/prometheus/FastAPI imports; shared
state is injected through the `BreakerStore` protocol (research D13).
"""
from __future__ import annotations

import asyncio
import enum
import random
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Literal, Optional, Protocol, runtime_checkable


TrafficClass = Literal["live", "batch"]


@dataclass(frozen=True)
class ResiliencePolicy:
    """Tunables for retry + breaker + bulkhead (data-model §A1; defaults per research D3/D5/D6).

    `max_attempts=1` is the documented retry kill switch. Every other field must be > 0 — bad config fails at
    construction (settings load), not on the first request."""
    max_attempts: int = 3
    backoff_base_s: float = 0.5
    per_wait_cap_s: float = 8.0
    min_attempt_budget_s: float = 0.5
    failure_threshold: int = 5
    cooldown_s: float = 30.0
    batch_concurrency: int = 4

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {self.max_attempts}")
        for name in ("backoff_base_s", "per_wait_cap_s", "min_attempt_budget_s",
                     "failure_threshold", "cooldown_s", "batch_concurrency"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0, got {getattr(self, name)}")


def traffic_class(source: str) -> TrafficClass:
    """Eval traffic is `batch`; everything else (incl. unknown sources) is `live` — the safe direction, since a
    batch-opened breaker never refuses live traffic (research D6)."""
    return "batch" if source == "eval" else "live"


@dataclass(frozen=True)
class BreakerKey:
    """Breaker identity. Deliberately NO tenant fields (FR-013): provider health is shared across workspaces."""
    provider: str
    model: str
    cls: TrafficClass


class Admission(enum.Enum):
    CLOSED = "closed"      # breaker closed — attempt normally
    TRIAL = "trial"        # cooldown elapsed — this caller holds the single half-open trial
    REFUSED = "refused"    # open (or another caller holds the trial) — fail fast, 0 transport calls


@runtime_checkable
class BreakerStore(Protocol):
    """Shared breaker state (research D13). Implementations: in-process `LocalBreakerStore` (this module, T022) and
    `api/breaker_store.RedisBreakerStore` (T023) — the runtime never imports redis."""

    async def admit(self, key: BreakerKey, *, also_check: Optional[BreakerKey] = None,
                    trial_ttl_s: float) -> Admission: ...

    async def record_success(self, key: BreakerKey, *, was_trial: bool) -> Optional[bool]: ...

    async def record_failure(self, key: BreakerKey, *, was_trial: bool) -> Optional[bool]:
        """Count one transient failure. Returns True when this call opened (or re-opened) the breaker."""
        ...


# ---- T022 (US2): in-process breaker store — same algorithm as the Redis store (research D5) -----------------------

_FAILS_TTL_S = 600.0       # consecutive-failure window, mirrors the Redis `fails` key PEXPIRE 10 min


class LocalBreakerStore:
    """Per-process breaker state: the default store, and the fallback when the shared store errors (B9).

    No awaits inside a method, so each method is atomic on the event loop — B2's single trial holds without locks.
    State per key: consecutive `fails` (expires after 10 min idle), an `open` marker whose expiry IS the cooldown, and
    a `trial` lease (expires after `trial_ttl_s`, so a crashed trial frees itself)."""

    def __init__(self, failure_threshold: int = 5, cooldown_s: float = 30.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_s = cooldown_s
        self.clock = clock
        self._fails: dict[BreakerKey, tuple[int, float]] = {}     # key -> (count, expires_at)
        self._open_until: dict[BreakerKey, float] = {}
        self._trial_until: dict[BreakerKey, float] = {}

    def _count(self, key: BreakerKey, now: float) -> int:
        n, exp = self._fails.get(key, (0, 0.0))
        return n if exp > now else 0

    def _open(self, key: BreakerKey, now: float) -> bool:
        return self._open_until.get(key, float("-inf")) > now

    def _unhealthy(self, key: BreakerKey, now: float) -> bool:
        return self._open(key, now) or self._count(key, now) >= self.failure_threshold

    async def admit(self, key: BreakerKey, *, also_check: Optional[BreakerKey] = None,
                    trial_ttl_s: float) -> Admission:
        now = self.clock()
        if also_check is not None and self._unhealthy(also_check, now):
            return Admission.REFUSED                      # B6: live unhealthy ⇒ batch refused
        if self._open(key, now):
            return Admission.REFUSED
        if self._count(key, now) >= self.failure_threshold:   # cooldown elapsed, not yet healed ⇒ half-open
            if self._trial_until.get(key, float("-inf")) > now:
                return Admission.REFUSED
            self._trial_until[key] = now + trial_ttl_s
            return Admission.TRIAL
        return Admission.CLOSED

    async def record_success(self, key: BreakerKey, *, was_trial: bool) -> bool:
        self._fails.pop(key, None)
        self._trial_until.pop(key, None)
        return False

    async def record_failure(self, key: BreakerKey, *, was_trial: bool) -> bool:
        now = self.clock()
        n = self._count(key, now) + 1
        self._fails[key] = (n, now + _FAILS_TTL_S)
        if was_trial:
            self._trial_until.pop(key, None)
        if was_trial or n >= self.failure_threshold:
            was_open = self._open(key, now)
            self._open_until[key] = now + self.cooldown_s
            return not was_open                           # report the transition once, not every late failure
        return False


# ---- T013 (US1): the per-process Guard — retry-wait computation + deadline rule (research D3) --------------------

Observer = Callable[[str, dict], None]


class Guard:
    """Composes policy + (US2) breaker store + (US3) batch bulkhead for one process (data-model §A6).

    Callers never use it directly — `gateway.stream` / `gateway.embed` do. `clock`, `sleep` and `rng` are injectable so
    tests run in ~0 wall time. The breaker store defaults to a `LocalBreakerStore`; batch slots are a lazy per-(provider, model) semaphore map."""

    def __init__(self, policy: ResiliencePolicy | None = None, store: "BreakerStore | None" = None,
                 local_store: "BreakerStore | None" = None, observer: Observer | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
                 rng: random.Random | None = None) -> None:
        self.policy = policy or ResiliencePolicy()
        local = local_store if local_store is not None else LocalBreakerStore(
            self.policy.failure_threshold, self.policy.cooldown_s, clock=clock)
        self.store = store if store is not None else local
        self.local_store = local
        self.observer = observer
        self.clock = clock
        self.sleep = sleep
        self.rng = rng or random.Random()
        # Two independent semaphore maps (kind -> (provider, model) -> Semaphore): the gateway "batch" bulkhead and the
        # eval "case" slot. They MUST stay separate — a case holding a slot while its own call needs one would deadlock.
        self._slots: dict[str, dict[tuple[str, str], asyncio.Semaphore]] = {}
        self._slots_loop: asyncio.AbstractEventLoop | None = None

    def emit(self, name: str, labels: dict) -> None:
        """Telemetry never alters a call (contract §7; 023 FR-009 precedent): observer exceptions are swallowed."""
        if self.observer is None:
            return
        try:
            self.observer(name, labels)
        except Exception:  # noqa: BLE001
            pass

    def retry_wait(self, next_attempt: int, retry_after: float | None) -> float:
        """Wait before attempt `next_attempt` (≥ 2): a valid provider Retry-After, else full-jitter backoff
        `uniform(0, base × 2^(n-2))` — either way capped at `per_wait_cap_s` (F11: never an adversarial hang)."""
        if retry_after is not None and retry_after > 0:
            wait = retry_after
        else:
            wait = self.rng.uniform(0, self.policy.backoff_base_s * 2 ** (next_attempt - 2))
        return min(wait, self.policy.per_wait_cap_s)

    def fits_deadline(self, started: float, wait: float, deadline_s: float | None) -> bool:
        """D3 deadline rule: never start a doomed attempt. `None` deadline ⇒ bounded by attempts × cap only."""
        if deadline_s is None:
            return True
        elapsed = self.clock() - started
        return elapsed + wait + self.policy.min_attempt_budget_s < deadline_s

    # ---- breaker (T022) — admission once per call, recording per attempt (contract §2 S9, §3 B1–B9) ----------------
    async def _store_call(self, method: str, *args, **kwargs):
        """Call the shared store; on ANY store error use the local store for this call (B9) — never refuse because
        of the outage."""
        try:
            return await getattr(self.store, method)(*args, **kwargs)
        except Exception:  # noqa: BLE001
            self.emit("breaker_degraded", {})
            return await getattr(self.local_store, method)(*args, **kwargs)

    async def admit(self, provider: str, model: str, cls: TrafficClass, deadline_s: float | None) -> Admission:
        key = BreakerKey(provider, model, cls)
        also = BreakerKey(provider, model, "live") if cls == "batch" else None
        ttl = (deadline_s if deadline_s is not None else self.policy.cooldown_s) + 5.0   # D5: deadline + 5 s
        admission = await self._store_call("admit", key, also_check=also, trial_ttl_s=ttl)
        labels = {"provider": provider, "model": model, "cls": cls}
        if admission is Admission.REFUSED:
            self.emit("breaker_refused", labels)
        elif admission is Admission.TRIAL:
            self.emit("breaker_transition", {**labels, "to": "half_open"})
        return admission

    async def record(self, provider: str, model: str, cls: TrafficClass, *, success: bool, was_trial: bool) -> None:
        key = BreakerKey(provider, model, cls)
        labels = {"provider": provider, "model": model, "cls": cls}
        if success:
            await self._store_call("record_success", key, was_trial=was_trial)
            if was_trial:
                self.emit("breaker_transition", {**labels, "to": "closed"})
        elif await self._store_call("record_failure", key, was_trial=was_trial):
            self.emit("breaker_transition", {**labels, "to": "open"})

    # ---- bulkhead (T030) — batch concurrency cap per (provider, model) (contract §4 K1–K3, research D6) ------------
    def _semaphore(self, kind: str, provider: str, model: str) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._slots_loop is not loop:          # the Guard is process-global; a Semaphore binds to ONE loop
            self._slots, self._slots_loop = {}, loop
        return self._slots.setdefault(kind, {}).setdefault(
            (provider, model), asyncio.Semaphore(self.policy.batch_concurrency))

    @asynccontextmanager
    async def _hold(self, kind: str, event: str, provider: str, model: str) -> AsyncIterator[float]:
        """Acquire one `kind` slot; emit `event` with the seconds waited (EVERY acquisition, zeros included, so the
        histogram's percentiles are honest) and yield that wait to the caller."""
        sem = self._semaphore(kind, provider, model)
        contended = sem.locked()                  # free slot ⇒ no wait: report exactly 0, not clock jitter
        queued_at = self.clock()
        async with sem:
            waited = max(0.0, self.clock() - queued_at) if contended else 0.0
            self.emit(event, {"provider": provider, "model": model, "seconds": waited})
            yield waited

    @asynccontextmanager
    async def bulkhead(self, provider: str, model: str, cls: TrafficClass) -> AsyncIterator[float]:
        """Hold one batch slot for the whole call (admission + attempt loop); yields the seconds spent queued so the
        caller can deduct it from the first attempt's wire timeout. Live traffic never touches it (K2) and yields 0.

        Waiting is unbounded HERE: the caller's outer deadline (invoke's `asyncio.timeout`) cancels a queued acquire
        and ends the run as TIMEOUT (K3); ingestion bounds the slot holder by its wire timeout
        (`AC_INGEST_EMBED_TIMEOUT_S`). `asyncio.Semaphore` releases nothing a cancelled waiter never held.
        Eval runs take a `case_slot` BEFORE invoke, so their gateway calls are normally uncontended (option A).
        The cap is per process — effective cap = cap × replicas (research D6, documented)."""
        if cls != "batch":
            yield 0.0
            return
        async with self._hold("batch", "batch_wait", provider, model) as waited:
            yield waited

    @asynccontextmanager
    async def case_slot(self, provider: str, model: str) -> AsyncIterator[float]:
        """Eval-case admission (025 US3 follow-up, option A): at most `batch_concurrency` eval cases per candidate
        (provider, model) run at once ACROSS all concurrent eval runs in this process. Held by `run_eval` around the
        whole case (candidate + judge) and taken BEFORE invoke starts, so queueing is never charged to the case's
        deadline — a TIMEOUT in a drift report is then the agent's, not the platform's."""
        async with self._hold("case", "eval_case_wait", provider, model) as waited:
            yield waited

    def after_queue(self, timeout: float | None, queued: float) -> float | None:
        """First-attempt wire timeout after a bulkhead wait: queue time is deducted (floored at the attempt budget).
        A call that never queued keeps EXACTLY `timeout` (no clock jitter on the common path)."""
        if timeout is None or queued <= 0:
            return timeout
        return max(timeout - queued, self.policy.min_attempt_budget_s)

    def remaining(self, started: float, deadline_s: float | None) -> float | None:
        if deadline_s is None:
            return None
        return max(deadline_s - (self.clock() - started), self.policy.min_attempt_budget_s)


_DEFAULT_GUARD: Guard | None = None


def default_guard() -> Guard:
    """Process-default guard (contract §1): retry + (US2) local breaker. The API replaces it per request through
    `RunContext.guard`; pure-library callers get this one."""
    global _DEFAULT_GUARD
    if _DEFAULT_GUARD is None:
        _DEFAULT_GUARD = Guard(ResiliencePolicy())
    return _DEFAULT_GUARD


def reset_default_guard() -> None:
    """Drop the process-default guard (tests: fresh breaker state / sleep per test)."""
    global _DEFAULT_GUARD
    _DEFAULT_GUARD = None
