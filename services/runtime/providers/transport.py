"""The LiteLLM transport seam (M1, research R1/R2/R3) — the ONLY place that calls LiteLLM.

LiteLLM is TRANSPORT ONLY: it carries bytes to/from providers and yields a unified (OpenAI-shaped)
streamed response. Everything that makes the platform honest stays on our side of this seam:
  - cost: computed by cost.compute_cost from our profiles — we NEVER read litellm's response_cost (R3).
  - capability downgrades: we pass drop_params=False so LiteLLM does not silently drop unsupported
    params; our adapters/preflight_substitutions decide + record each downgrade as a Substitution (R2).

Isolated as a single async generator so adapter/gateway tests monkeypatch `stream_chunks` with canned
LiteLLM-shaped chunks — no network, no API key use (R9). litellm is imported lazily so importing this
module (and runtime/) stays cheap and keeps the P6 purity invariant intact.
"""
from __future__ import annotations

import math
import os
import time
from email.utils import parsedate_to_datetime
from typing import Any, AsyncIterator

# Force litellm's BUNDLED local model-cost map. litellm otherwise fetches a remote prices map from GitHub
# on first import (retries with backoff) — cold-start latency and surprise egress that fails in an air-
# gapped pilot. We compute cost ourselves from our own profiles (P7/R3) and never read litellm's cost, so
# its map is dead weight here. Set BEFORE the lazy `import litellm` below so the production import never
# fetches. Must stay above any litellm import in this process to take effect.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")


class TransportError(Exception):
    """Normalized wrapper for ANY failure from the LiteLLM transport (provider/SDK/network). Raised ONLY
    by this seam, so the gateway can catch it specifically (FR-009/P8) — our own adapter/mapping bugs raise
    their own types and propagate uncaught, never misattributed as a provider error (code review F3).
    Carries a generic `retryable` hint (classified here, where the underlying exception is available —
    code review F5), the HTTP `status` when litellm exposed one, and a validated provider `retry_after` hint in seconds
    (feature 025, research G2) — `None` when absent or unusable, so the gateway falls back to its backoff schedule."""

    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None,
                 retry_after: float | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status
        self.retry_after = retry_after


# Class-name fragments that indicate a transient (retryable) failure when no HTTP status is available.
_RETRYABLE_NAME_HINTS = (
    "timeout", "connection", "ratelimit", "rate_limit", "unavailable", "overloaded",
    "internalserver", "serviceunavailable", "protocol", "reset", "eof", "disconnect", "read",
)


def _is_retryable(exc: BaseException) -> bool:
    """409 Conflict is NOT transient (feature 025 research D4): it is a deterministic request-state condition, so it
    must neither be retried nor count toward the circuit breaker."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        if status in (408, 429) or status >= 500:
            return True
        if 400 <= status < 500:
            return False
    name = type(exc).__name__.lower()
    return any(hint in name for hint in _RETRYABLE_NAME_HINTS)


def parse_retry_after(value: str | None, *, now: float | None = None) -> float | None:
    """Parse an HTTP `Retry-After` value into seconds to wait (feature 025, contract runtime-resilience §6).

    Accepts delta-seconds (`"7"`, `"1.5"`) or an HTTP-date (relative to `now`, epoch seconds; defaults to wall clock).
    Anything unusable — missing, empty, non-numeric, NaN/inf, zero, negative, or a date not in the future — returns
    `None`, so the caller uses its own backoff. The CAP is the gateway's job (policy.per_wait_cap_s), not ours."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        try:
            when = parsedate_to_datetime(raw)
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
        if when is None or when.tzinfo is None:
            return None
        seconds = when.timestamp() - (time.time() if now is None else now)
    if not math.isfinite(seconds) or seconds <= 0:
        return None
    return seconds


def _header(headers: Any, name: str) -> str | None:
    if not headers:
        return None
    try:
        got = headers.get(name)             # httpx.Headers is case-insensitive
    except Exception:  # noqa: BLE001 — a malformed headers object is just "no hint"
        return None
    if got is not None:
        return got
    try:
        lowered = {str(k).lower(): v for k, v in dict(headers).items()}
    except Exception:  # noqa: BLE001
        return None
    return lowered.get(name.lower())


def _retry_after(exc: BaseException) -> float | None:
    """Retry-After from the provider's HTTP response (research G2: litellm keeps it on `exc.response.headers`, and
    deliberately does NOT copy it onto `exc.headers`); `exc.headers` is a fallback for other SDK shapes."""
    response = getattr(exc, "response", None)
    raw = _header(getattr(response, "headers", None), "retry-after")
    if raw is None:
        raw = _header(getattr(exc, "headers", None), "retry-after")
    return parse_retry_after(raw)


def _wrap(exc: BaseException) -> TransportError:
    return TransportError(str(exc), retryable=_is_retryable(exc), status=getattr(exc, "status_code", None),
                          retry_after=_retry_after(exc))


async def stream_chunks(**kwargs: Any) -> AsyncIterator[Any]:
    """Call `litellm.acompletion(stream=True, drop_params=False, stream_options={include_usage}, **kwargs)`
    and yield raw chunks.

    Callers pass provider-native kwargs (model, messages, api_key, max_tokens, tools, …). We force
    stream=True, default drop_params=False (R2), and default `stream_options={"include_usage": True}` so the
    provider actually REPORTS usage on the stream — OpenAI-style providers omit it otherwise, and WE need it
    to compute cost (P7); without it every stream would read as degraded (code review F1). litellm applies
    include_usage per provider; the exact per-provider behavior is confirmed at M2 live integration.

    Any provider/SDK/network failure is wrapped as `TransportError` (the single litellm boundary) so the
    gateway can distinguish a provider failure from a bug in our own code (code review F3). The returned
    chunks are LiteLLM's unified streaming objects; adapters map them to NormalizedStreamEvent and raw
    NormalizedUsage. This function is the test seam — in tests it is replaced wholesale, so litellm is never
    imported there.
    """
    import litellm  # lazy: keeps `import runtime.providers.transport` cheap + purity-safe

    kwargs["stream"] = True
    kwargs.setdefault("drop_params", False)
    kwargs.setdefault("stream_options", {"include_usage": True})
    # G8 (feature 024 research D9): litellm 1.106.x imports its MCP gateway handler (needs the uninstalled `mcp`
    # package) whenever `tools` is non-empty, failing EVERY tool-bound call. Tool execution is ours (F3); bypass it.
    # Private kwarg — pinned by tests/test_transport.py; re-verify on any litellm bump.
    kwargs.setdefault("_skip_mcp_handler", True)
    # G1 (feature 025): litellm's OpenAI path defaults the SDK to max_retries=2, i.e. HIDDEN retries invisible to our
    # run record and multiplied by the gateway's own retry loop. Resilience is the gateway's job; disable both layers.
    kwargs.setdefault("max_retries", 0)
    kwargs.setdefault("num_retries", 0)
    try:
        response = await litellm.acompletion(**kwargs)
        async for chunk in response:
            yield chunk
    except TransportError:
        raise
    except Exception as exc:  # noqa: BLE001 — the single litellm boundary: normalize ALL provider failures
        raise _wrap(exc) from exc


async def embed(**kwargs: Any) -> Any:
    """Call `litellm.aembedding(drop_params=False, **kwargs)` and return its response (feature 024, research D2).

    The embedding counterpart of `stream_chunks`, and like it the single litellm boundary for embeddings: every
    provider/SDK/network failure is wrapped as `TransportError` with the same retryable classification. litellm also
    reports its own cost on the response (`_hidden_params["response_cost"]`); the gateway never reads it (R3). In tests
    this function is replaced wholesale, so litellm is never imported there.
    """
    import litellm  # lazy: keeps `import runtime.providers.transport` cheap + purity-safe

    kwargs.setdefault("drop_params", False)
    kwargs.setdefault("max_retries", 0)       # G1: no hidden SDK / litellm retries (feature 025)
    kwargs.setdefault("num_retries", 0)
    try:
        return await litellm.aembedding(**kwargs)
    except TransportError:
        raise
    except Exception as exc:  # noqa: BLE001 — the single litellm boundary: normalize ALL provider failures
        raise _wrap(exc) from exc
