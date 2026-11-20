"""Fault-injecting transport stub for feature 025 resilience tests (task T002 — test scaffolding only).

Replaces `transport.stream_chunks` / `transport.embed` wholesale (the established seam, as in test_gateway.py), so no
litellm import, no network, no API key use. Each call consumes the NEXT scripted step; the last step repeats if the
script runs out (so "always failing" is a one-step script). `calls` counts transport invocations — the observable the
retry/breaker contracts are written against (S1–S10, B1–B9).

    script = FaultScript([fail(429), fail(503, retry_after=2.0), ok("answer", prompt=10, completion=3)])
    script.install(monkeypatch)
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from runtime.providers import transport
from runtime.providers.transport import TransportError


# --- litellm-shaped chunk builders (same shapes as tests/test_gateway.py) -----------------------------------------
def delta(text: str) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))], usage=None)


def usage_chunk(*, prompt: int = 10, completion: int = 3) -> SimpleNamespace:
    usage = SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion,
                            prompt_tokens_details=None, completion_tokens_details=None, cost=0.99)
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None))], usage=usage)


# --- scripted steps -------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Step:
    kind: str                                  # "fail" | "partial" | "ok" | "no_usage"
    chunks: tuple = ()
    retryable: bool = True
    status: int | None = None
    retry_after: float | None = None
    delay_s: float = 0.0                       # awaited before the step acts (bulkhead/concurrency tests)


def _make_error(step: Step) -> TransportError:
    err = TransportError(f"injected {step.status or 'transport'} failure", retryable=step.retryable,
                         status=step.status)
    # Set after construction so this helper stays usable before TransportError grows the kwarg (T006).
    err.retry_after = step.retry_after
    return err


def fail(status: int | None = 503, *, retryable: bool | None = None, retry_after: float | None = None,
         delay_s: float = 0.0) -> Step:
    """A failure before any output. retryable defaults from status (408/429/5xx/None → True, other 4xx → False)."""
    if retryable is None:
        retryable = status is None or status in (408, 429) or status >= 500
    return Step("fail", retryable=retryable, status=status, retry_after=retry_after, delay_s=delay_s)


def partial(*texts: str, status: int | None = 503, retryable: bool = True) -> Step:
    """Stream `texts` as deltas, THEN fail — the after-first-event case that must never be retried (S4/FR-005)."""
    return Step("partial", chunks=tuple(delta(t) for t in texts), retryable=retryable, status=status)


def ok(*texts: str, prompt: int = 10, completion: int = 3, delay_s: float = 0.0) -> Step:
    return Step("ok", chunks=(*(delta(t) for t in texts), usage_chunk(prompt=prompt, completion=completion)),
                delay_s=delay_s)


def no_usage(*texts: str) -> Step:
    """Stream ends cleanly without a usage report → gateway's degraded ERROR (S8: never retried)."""
    return Step("no_usage", chunks=tuple(delta(t) for t in texts))


@dataclass
class FaultScript:
    steps: list[Step]
    calls: int = 0
    kwargs_seen: list[dict] = field(default_factory=list)
    in_flight: int = 0
    max_in_flight: int = 0

    def _next(self) -> Step:
        step = self.steps[min(self.calls, len(self.steps) - 1)]
        self.calls += 1
        return step

    async def stream_chunks(self, **kwargs: Any):
        self.kwargs_seen.append(kwargs)
        step = self._next()
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if step.delay_s:
                await asyncio.sleep(step.delay_s)
            if step.kind == "fail":
                raise _make_error(step)
            for c in step.chunks:
                yield c
            if step.kind == "partial":
                raise _make_error(step)
        finally:
            self.in_flight -= 1

    async def embed(self, **kwargs: Any):
        """Embedding counterpart: "fail" raises; anything else returns one 1024-dim vector per input."""
        self.kwargs_seen.append(kwargs)
        step = self._next()
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if step.delay_s:
                await asyncio.sleep(step.delay_s)
            if step.kind == "fail":
                raise _make_error(step)
            n = len(kwargs["input"])
            data = [{"index": i, "embedding": [0.001 * (i + 1)] * 1024} for i in range(n)]
            return SimpleNamespace(data=data, usage=SimpleNamespace(prompt_tokens=10 * n), id=None)
        finally:
            self.in_flight -= 1

    def install(self, monkeypatch) -> "FaultScript":
        monkeypatch.setattr(transport, "stream_chunks", self.stream_chunks)
        monkeypatch.setattr(transport, "embed", self.embed)
        return self
