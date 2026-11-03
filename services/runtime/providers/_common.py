"""Shared OpenAI-shaped adapter plumbing (M1). OpenAI + Ollama both receive LiteLLM's unified
(OpenAI-shaped) streaming response, so they share the stream loop and usage mapper here; Anthropic
reuses the loop with its own usage mapper (cache_read/cache_creation divergence — see usage-mapping).

Transport is reached via the module attribute `transport.stream_chunks` so tests can monkeypatch it.
Raw token counts only — cost is attached by the gateway (never here, never from litellm).
"""
from __future__ import annotations

from typing import AsyncIterator, Callable

from runtime.providers import transport
from runtime.providers.ir import (
    EventType,
    NormalizedRequest,
    NormalizedStreamEvent,
    NormalizedUsage,
)


def build_messages(req: NormalizedRequest) -> list[dict]:
    """System turn (if any) prepended to the normalized messages."""
    messages: list[dict] = []
    if req.system:
        messages.append({"role": "system", "content": req.system})
    messages.extend(req.messages)
    return messages


def map_openai_usage(usage) -> NormalizedUsage:
    """LiteLLM Usage (OpenAI shape) → NormalizedUsage. prompt_tokens is inclusive of cached;
    completion_tokens inclusive of reasoning (matches cost.py conventions). Missing details → 0
    (honest absence)."""
    if usage is None:
        return NormalizedUsage(0, 0)
    ptd = getattr(usage, "prompt_tokens_details", None)
    ctd = getattr(usage, "completion_tokens_details", None)
    return NormalizedUsage(
        input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        cached_input_tokens=(getattr(ptd, "cached_tokens", 0) or 0) if ptd is not None else 0,
        reasoning_tokens=(getattr(ctd, "reasoning_tokens", 0) or 0) if ctd is not None else 0,
    )


async def stream_via_transport(
    *,
    model: str,
    messages: list[dict],
    api_key: str,
    params: dict,
    tools: list[dict] | None = None,
    usage_mapper: Callable[[object], NormalizedUsage] = map_openai_usage,
) -> AsyncIterator[NormalizedStreamEvent]:
    """Drive the LiteLLM transport seam and yield normalized events: TEXT_DELTA as content streams,
    then exactly one terminal USAGE carrying raw NormalizedUsage (cost attached by the gateway).
    `substitutions` on the USAGE event is runtime-discovered-only (empty here in M1); the gateway
    merges preflight substitutions. Tool-call events are deferred to M2 (content only in M1)."""
    kwargs: dict = {"model": model, "messages": messages, "api_key": api_key, **params}
    if tools:
        kwargs["tools"] = tools

    usage: NormalizedUsage | None = None
    async for chunk in transport.stream_chunks(**kwargs):
        choices = getattr(chunk, "choices", None) or []
        if choices:
            delta = getattr(choices[0], "delta", None)
            content = getattr(delta, "content", None) if delta is not None else None
            if content:
                yield NormalizedStreamEvent(EventType.TEXT_DELTA, {"text": content})
        chunk_usage = getattr(chunk, "usage", None)
        if chunk_usage is not None:
            usage = usage_mapper(chunk_usage)

    yield NormalizedStreamEvent(
        EventType.USAGE, {"usage": usage or NormalizedUsage(0, 0), "substitutions": []}
    )
