"""T009 (feature 025, US1) — bounded retry in `gateway.stream` (contract runtime-resilience §2 S1–S8, research D3).

Driven through the real gateway with the fault-injecting transport (`_fault_transport`) and an injected
`Guard(clock, sleep, rng)`: `sleep` records each wait and advances a fake clock, so every test runs in ~0 wall time.
The deadline the retry loop honours is the gateway's existing `timeout` argument (invoke passes the run deadline).
No live calls, no network, no API key use.
"""
from __future__ import annotations

import random

import pytest

from runtime.providers import base, gateway, resilience
from runtime.providers.ir import EventType, NormalizedRequest, NormalizedStreamEvent
from runtime.providers.transport import TransportError

from tests._fault_transport import FaultScript, fail, no_usage, ok, partial


class FakeClock:
    """Monotonic clock + sleep that advances it. `waits` records every requested sleep (the D3 observable)."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.waits: list[float] = []

    def now(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.t += seconds


def _guard(clock: FakeClock, *, seed: int = 7, events: list | None = None, **policy) -> "resilience.Guard":
    def observer(name: str, labels: dict) -> None:
        if events is not None:
            events.append((name, labels))
    return resilience.Guard(policy=resilience.ResiliencePolicy(**policy), clock=clock.now, sleep=clock.sleep,
                            rng=random.Random(seed), observer=observer)


def _req() -> NormalizedRequest:
    return NormalizedRequest(provider="openai", model="gpt-5-mini", api_version=None,
                             messages=[{"role": "user", "content": "hi"}], system=None, params={})


async def _run(guard, *, timeout: float | None = 30.0) -> list[NormalizedStreamEvent]:
    return [ev async for ev in gateway.stream(_req(), api_key="k", timeout=timeout, guard=guard)]


def _text(events) -> str:
    return "".join(e.data["text"] for e in events if e.type is EventType.TEXT_DELTA)


def _errors(events) -> list[dict]:
    return [e.data["error"] for e in events if e.type is EventType.ERROR]


def _usage(events) -> list[NormalizedStreamEvent]:
    return [e for e in events if e.type is EventType.USAGE]


# --- S1 / S2: transient before any surfaced event → retry; success on attempt k reports attempts == k ------------
async def test_s1_s2_two_transient_failures_then_success(monkeypatch):
    script = FaultScript([fail(503), fail(429), ok("ans", "wer")]).install(monkeypatch)
    clock = FakeClock()
    events = await _run(_guard(clock))
    assert script.calls == 3
    assert _text(events) == "answer"                       # delivered exactly once
    assert _errors(events) == []
    [usage] = _usage(events)
    assert usage.data["attempts"] == 3
    assert len(clock.waits) == 2


async def test_s1_failed_attempt_yields_nothing(monkeypatch):
    FaultScript([fail(503), ok("x")]).install(monkeypatch)
    events = await _run(_guard(FakeClock()))
    assert [e.type for e in events] == [EventType.TEXT_DELTA, EventType.USAGE]


async def test_s2_first_attempt_success_reports_one_attempt(monkeypatch):
    script = FaultScript([ok("hi")]).install(monkeypatch)
    clock = FakeClock()
    events = await _run(_guard(clock))
    assert script.calls == 1 and clock.waits == []
    assert _usage(events)[0].data["attempts"] == 1


async def test_retry_emits_observer_event_with_bounded_labels(monkeypatch):
    FaultScript([fail(503), ok("x")]).install(monkeypatch)
    seen: list = []
    await _run(_guard(FakeClock(), events=seen))
    retries = [labels for name, labels in seen if name == "retry"]
    assert retries == [{"provider": "openai", "model": "gpt-5-mini", "cls": "live"}]


# --- S3: non-transient → single ERROR, no retry --------------------------------------------------------------------
@pytest.mark.parametrize("status", [400, 401, 404, 409])
async def test_s3_non_transient_is_not_retried(monkeypatch, status):
    script = FaultScript([fail(status), ok("never")]).install(monkeypatch)
    clock = FakeClock()
    events = await _run(_guard(clock))
    assert script.calls == 1 and clock.waits == []
    [err] = _errors(events)
    assert err["type"] == "provider_error" and err["retryable"] is False and err["attempts"] == 1
    assert _usage(events) == []


# --- S4: transient AFTER a surfaced event → no retry, no duplicated output -------------------------------------------
async def test_s4_failure_after_text_delta_is_not_retried(monkeypatch):
    script = FaultScript([partial("par", "tial"), ok("DUPLICATE")]).install(monkeypatch)
    clock = FakeClock()
    events = await _run(_guard(clock))
    assert script.calls == 1 and clock.waits == []
    assert _text(events) == "partial"
    [err] = _errors(events)
    assert err["retryable"] is True and err["attempts"] == 1


class _ScriptedAdapter:
    """Adapter-level stub: attempt i yields `attempts[i]` events then (optionally) raises a retryable TransportError.
    Used for surfaced-event kinds the OpenAI chunk mapper never produces (reasoning / tool calls)."""

    def __init__(self, attempts: list[tuple[list[NormalizedStreamEvent], bool]]) -> None:
        self.attempts = attempts
        self.calls = 0

    def supports(self, profile, req):
        return []

    async def stream(self, req, *, api_key, profile, timeout=None):
        events, then_fail = self.attempts[min(self.calls, len(self.attempts) - 1)]
        self.calls += 1
        for ev in events:
            yield ev
        if then_fail:
            raise TransportError("injected mid-stream 503", retryable=True, status=503)


_SURFACED = {
    "reasoning": NormalizedStreamEvent(EventType.REASONING_DELTA, {"text": "thinking"}),
    "tool_call_start": NormalizedStreamEvent(EventType.TOOL_CALL_START, {"id": "c1", "name": "lookup"}),
    "tool_call_delta": NormalizedStreamEvent(EventType.TOOL_CALL_DELTA, {"id": "c1", "args_delta": "{\"q\""}),
}


@pytest.mark.parametrize("kind", sorted(_SURFACED))
async def test_s4_failure_after_reasoning_or_tool_call_is_not_retried(monkeypatch, kind):
    from runtime.providers.ir import NormalizedUsage
    good = [NormalizedStreamEvent(EventType.USAGE, {"usage": NormalizedUsage(1, 1), "substitutions": []})]
    adapter = _ScriptedAdapter([([_SURFACED[kind]], True), (good, False)])
    monkeypatch.setattr(gateway, "adapter_for", lambda provider: adapter)
    clock = FakeClock()
    events = await _run(_guard(clock))
    assert adapter.calls == 1 and clock.waits == []
    assert [e.type for e in events] == [_SURFACED[kind].type, EventType.ERROR]
    assert _errors(events)[0]["attempts"] == 1


# --- S5: exhaustion → exactly max_attempts calls, last error surfaced ----------------------------------------------
async def test_s5_always_failing_stops_at_max_attempts(monkeypatch):
    script = FaultScript([fail(500), fail(502), fail(503)]).install(monkeypatch)
    clock = FakeClock()
    events = await _run(_guard(clock))
    assert script.calls == 3 and len(clock.waits) == 2
    [err] = _errors(events)
    assert err["attempts"] == 3 and err["status"] == 503 and err["retryable"] is True


async def test_s5_kill_switch_max_attempts_one(monkeypatch):
    script = FaultScript([fail(503), ok("x")]).install(monkeypatch)
    events = await _run(_guard(FakeClock(), max_attempts=1))
    assert script.calls == 1
    assert _errors(events)[0]["attempts"] == 1


# --- S6: never start a doomed attempt past deadline − min_attempt_budget ---------------------------------------------
async def test_s6_wait_that_would_cross_deadline_stops_early(monkeypatch):
    # Retry-After 5 s (under the 8 s cap) but only a 4 s deadline: elapsed(0) + 5 + 0.5 ≥ 4 → stop, no sleep.
    script = FaultScript([fail(429, retry_after=5.0), ok("x")]).install(monkeypatch)
    clock = FakeClock()
    events = await _run(_guard(clock), timeout=4.0)
    assert script.calls == 1 and clock.waits == []
    [err] = _errors(events)
    assert err["status"] == 429 and err["attempts"] == 1


async def test_s6_slow_failing_attempts_consume_the_deadline(monkeypatch):
    # Each failing attempt "takes" 2.5 s of clock; deadline 5 s. Attempt 1 ends at elapsed 2.5 (+≤0.5 wait +0.5 budget
    # < 5 → retry); attempt 2 ends at elapsed ≥ 5.0 → any further wait crosses the deadline → stop after 2 calls.
    clock = FakeClock()

    async def slow_fail(**kwargs):
        clock.t += 2.5
        raise TransportError("slow 503", retryable=True, status=503)
        yield  # pragma: no cover — makes this an async generator

    from runtime.providers import transport
    monkeypatch.setattr(transport, "stream_chunks", slow_fail)
    events = await _run(_guard(clock), timeout=5.0)
    assert len(clock.waits) == 1
    assert _errors(events)[0]["attempts"] == 2


async def test_s6_no_deadline_bounded_by_attempts_only(monkeypatch):
    script = FaultScript([fail(503)]).install(monkeypatch)
    events = await _run(_guard(FakeClock()), timeout=None)
    assert script.calls == 3 and _errors(events)[0]["attempts"] == 3


# --- S7: wait computation — Retry-After honoured but capped; otherwise seeded full-jitter backoff --------------------
async def test_s7_valid_retry_after_is_used(monkeypatch):
    FaultScript([fail(429, retry_after=2.0), ok("x")]).install(monkeypatch)
    clock = FakeClock()
    await _run(_guard(clock))
    assert clock.waits == [2.0]


async def test_s7_huge_retry_after_is_capped(monkeypatch):
    FaultScript([fail(429, retry_after=3600.0), ok("x")]).install(monkeypatch)
    clock = FakeClock()
    await _run(_guard(clock), timeout=60.0)
    assert clock.waits == [8.0]


async def test_s7_no_hint_uses_full_jitter_backoff(monkeypatch):
    FaultScript([fail(503), fail(503), ok("x")]).install(monkeypatch)
    clock = FakeClock()
    await _run(_guard(clock, seed=42))
    expected_rng = random.Random(42)
    expected = [expected_rng.uniform(0, 0.5), expected_rng.uniform(0, 1.0)]   # base × 2^(n-2), n = 2, 3
    assert clock.waits == pytest.approx(expected)
    assert all(w <= 8.0 for w in clock.waits)


# --- S8: usage-less stream → existing degraded ERROR, never retried -------------------------------------------------
async def test_s8_degraded_stream_is_not_retried(monkeypatch):
    script = FaultScript([no_usage("text"), ok("DUPLICATE")]).install(monkeypatch)
    clock = FakeClock()
    events = await _run(_guard(clock))
    assert script.calls == 1 and clock.waits == []
    [err] = _errors(events)
    assert err["type"] == "degraded" and err["retryable"] is False


# --- default guard: guard=None still retries (process default), signature stays backward compatible ---------------
async def test_default_guard_is_used_when_none(monkeypatch):
    script = FaultScript([fail(503), ok("x")]).install(monkeypatch)
    sleeps: list[float] = []

    async def no_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(resilience.default_guard(), "sleep", no_sleep)
    events = [ev async for ev in gateway.stream(_req(), api_key="k")]
    assert script.calls == 2 and len(sleeps) == 1
    assert _usage(events)[0].data["attempts"] == 2


def test_adapter_registry_untouched():
    # guard plumbing must not replace adapter selection (S10 sibling: config errors still raise before admission)
    assert base.adapter_for("openai") is not None
