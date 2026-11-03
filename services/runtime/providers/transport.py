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

from typing import Any, AsyncIterator


async def stream_chunks(**kwargs: Any) -> AsyncIterator[Any]:
    """Call `litellm.acompletion(stream=True, drop_params=False, **kwargs)` and yield raw chunks.

    Callers pass provider-native kwargs (model, messages, api_key, max_tokens, tools, …). We force
    stream=True and default drop_params=False (R2). The returned chunks are LiteLLM's unified streaming
    objects; adapters map them to NormalizedStreamEvent and raw NormalizedUsage. This function is the
    test seam — in tests it is replaced wholesale, so litellm is never imported there.
    """
    import litellm  # lazy: keeps `import runtime.providers.transport` cheap + purity-safe

    kwargs["stream"] = True
    kwargs.setdefault("drop_params", False)
    response = await litellm.acompletion(**kwargs)
    async for chunk in response:
        yield chunk
