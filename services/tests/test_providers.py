"""Provider-adapter tests (M1) — NO live calls. The LiteLLM transport seam
(`runtime.providers.transport.stream_chunks`) is monkeypatched with canned chunks shaped exactly like
LiteLLM's streaming output (ModelResponseStream: `chunk.choices[0].delta.content`; a final
`chunk.usage` Usage object). We assert the normalized event vocabulary (TEXT_DELTA then one USAGE) and
per-provider usage-field mapping. Cost / substitutions have their own phases (US2/US3).

US1 scope: normalized execution + usage mapping for openai + ollama. Tool-call events deferred to M2.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from runtime.providers import transport
from runtime.providers.ir import EventType, NormalizedRequest
from runtime.providers.ollama_adapter import OllamaAdapter
from runtime.providers.openai_adapter import OpenAIAdapter


# --- helpers: build canned LiteLLM-shaped stream chunks -------------------------------------------

def _delta(text: str | None):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))], usage=None)


def _usage_chunk(*, prompt=0, completion=0, cached=None, reasoning=None):
    ptd = SimpleNamespace(cached_tokens=cached) if cached is not None else None
    ctd = SimpleNamespace(reasoning_tokens=reasoning) if reasoning is not None else None
    usage = SimpleNamespace(
        prompt_tokens=prompt, completion_tokens=completion,
        prompt_tokens_details=ptd, completion_tokens_details=ctd,
        cost=0.123,  # litellm's own cost — MUST be ignored by us (asserted in US2)
    )
    # final chunk: delta has no content, usage present (mirrors confirmed litellm shape)
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None))], usage=usage)


def _patch_transport(monkeypatch, chunks):
    async def _fake(**kwargs):
        for c in chunks:
            yield c
    monkeypatch.setattr(transport, "stream_chunks", _fake)


def _collect(agen) -> list:
    async def _drive():
        return [ev async for ev in agen]
    return asyncio.run(_drive())


def _req(**over) -> NormalizedRequest:
    base = dict(
        provider="openai", model="gpt-5-mini", api_version=None,
        messages=[{"role": "user", "content": "triage this"}],
        system="be a SOC triage bot",
        params={"temperature": 0.2, "max_tokens": 500},
    )
    base.update(over)
    return NormalizedRequest(**base)


# --- OpenAI --------------------------------------------------------------------------------------

_OPENAI_CHUNKS = [
    _delta('{"severity":'),
    _delta('"high"}'),
    _usage_chunk(prompt=1200, completion=400, cached=300, reasoning=150),
]


def test_openai_streams_text_then_one_usage(monkeypatch):
    _patch_transport(monkeypatch, _OPENAI_CHUNKS)
    events = _collect(OpenAIAdapter().stream(_req(), api_key="k", profile=None))

    text = "".join(e.data["text"] for e in events if e.type is EventType.TEXT_DELTA)
    assert text == '{"severity":"high"}'
    usage_events = [e for e in events if e.type is EventType.USAGE]
    assert len(usage_events) == 1  # exactly one terminal USAGE


def test_openai_usage_mapping(monkeypatch):
    _patch_transport(monkeypatch, _OPENAI_CHUNKS)
    events = _collect(OpenAIAdapter().stream(_req(), api_key="k", profile=None))
    usage = next(e.data["usage"] for e in events if e.type is EventType.USAGE)
    assert usage.input_tokens == 1200          # prompt_tokens
    assert usage.output_tokens == 400          # completion_tokens
    assert usage.cached_input_tokens == 300    # prompt_tokens_details.cached_tokens
    assert usage.reasoning_tokens == 150       # completion_tokens_details.reasoning_tokens


# --- Ollama --------------------------------------------------------------------------------------

_OLLAMA_CHUNKS = [
    _delta('{"severity":'),
    _delta('"high"}'),
    _usage_chunk(prompt=210, completion=58),   # no cache/reasoning details (litellm maps ollama → prompt/completion)
]


def test_ollama_streams_text_then_one_usage(monkeypatch):
    _patch_transport(monkeypatch, _OLLAMA_CHUNKS)
    events = _collect(
        OllamaAdapter().stream(_req(provider="ollama", model="llama3.1:8b"), api_key="", profile=None)
    )
    text = "".join(e.data["text"] for e in events if e.type is EventType.TEXT_DELTA)
    assert text == '{"severity":"high"}'
    assert sum(1 for e in events if e.type is EventType.USAGE) == 1


def test_ollama_usage_mapping_no_cache_or_reasoning(monkeypatch):
    _patch_transport(monkeypatch, _OLLAMA_CHUNKS)
    events = _collect(
        OllamaAdapter().stream(_req(provider="ollama", model="llama3.1:8b"), api_key="", profile=None)
    )
    usage = next(e.data["usage"] for e in events if e.type is EventType.USAGE)
    assert usage.input_tokens == 210
    assert usage.output_tokens == 58
    assert usage.cached_input_tokens == 0      # honest absence, not a guess
    assert usage.reasoning_tokens == 0
