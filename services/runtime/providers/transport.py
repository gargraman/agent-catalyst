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

import os
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
    code review F5) and the HTTP `status` when litellm exposed one."""

    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


# Class-name fragments that indicate a transient (retryable) failure when no HTTP status is available.
_RETRYABLE_NAME_HINTS = (
    "timeout", "connection", "ratelimit", "rate_limit", "unavailable", "overloaded",
    "internalserver", "serviceunavailable", "protocol", "reset", "eof", "disconnect", "read",
)


def _is_retryable(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        if status in (408, 409, 429) or status >= 500:
            return True
        if 400 <= status < 500:
            return False
    name = type(exc).__name__.lower()
    return any(hint in name for hint in _RETRYABLE_NAME_HINTS)


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
    try:
        response = await litellm.acompletion(**kwargs)
        async for chunk in response:
            yield chunk
    except TransportError:
        raise
    except Exception as exc:  # noqa: BLE001 — the single litellm boundary: normalize ALL provider failures
        raise TransportError(
            str(exc), retryable=_is_retryable(exc), status=getattr(exc, "status_code", None)
        ) from exc
