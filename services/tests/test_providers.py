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

import pytest

from runtime.providers import transport
from runtime.providers.anthropic_adapter import AnthropicAdapter
from runtime.providers.ir import EventType, NormalizedRequest
from runtime.providers.ollama_adapter import OllamaAdapter
from runtime.providers.openai_adapter import OpenAIAdapter
from runtime.providers.profiles import CapabilityProfile


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


# --- US3: explicit capability substitutions ------------------------------------------------------
# A request may ask for features a chosen model lacks. The adapter must (a) report the downgrades via
# supports() (preflight) and (b) make the WIRE BODY match: forward a native feature only when the
# profile supports it, otherwise omit it and — for the emulated_via_prompt rules — inject an
# instruction so the recorded Substitution is never a lie (contract obligation #6 / P7).

_FORCED_TOOL = {"type": "function", "function": {"name": "set_severity"}}
_SCHEMA = {"json_schema": {"name": "triage", "schema": {"type": "object"}}}

_WEAK = dict(structured_output=False, forced_tool_choice=False, reasoning_control=False, prompt_caching=False)
_FULL = dict(structured_output=True, forced_tool_choice=True, reasoning_control=True, prompt_caching=True)


def _profile(provider="openai", model="gpt-5-mini", **features) -> CapabilityProfile:
    return CapabilityProfile(
        provider=provider, model=model, api_version=None,
        pricing={}, limits={}, features=features,
    )


def _feature_req(provider="openai", model="gpt-5-mini") -> NormalizedRequest:
    """A request exercising every downgradable feature at once."""
    return NormalizedRequest(
        provider=provider, model=model, api_version=None,
        messages=[{"role": "user", "content": "triage this"}],
        system="be a SOC triage bot",
        params={"temperature": 0.2},
        tools=[{"type": "function", "function": {"name": "set_severity"}}],
        tool_choice=_FORCED_TOOL,
        response_format=_SCHEMA,
        reasoning={"effort": "high"},
        cache_hints=[{"index": 0}],
    )


def _patch_transport_capture(monkeypatch, chunks) -> dict:
    """Like _patch_transport but records the kwargs actually sent to the transport seam (the wire body)."""
    captured: dict = {}

    async def _fake(**kwargs):
        captured.update(kwargs)
        for c in chunks:
            yield c

    monkeypatch.setattr(transport, "stream_chunks", _fake)
    return captured


def test_supports_reports_substitutions_for_weak_profile():
    # supports() (preflight) enumerates the downgrades needed to run this request on a weak model.
    subs = OpenAIAdapter().supports(_profile(**_WEAK), _feature_req())
    by = {(s.feature, s.action) for s in subs}
    assert ("structured_output", "emulated_via_prompt") in by
    assert ("forced_tool_choice", "emulated_via_prompt") in by
    assert ("reasoning_control", "dropped") in by
    assert ("prompt_caching", "dropped") in by


def test_supports_no_substitutions_for_plain_request():
    # no downgrade needed → no false positives.
    assert OpenAIAdapter().supports(_profile(**_FULL), _req()) == []


def test_build_request_forwards_native_features_when_supported(monkeypatch):
    # closes F2: a capable model must actually RECEIVE the native features, not have them silently dropped.
    captured = _patch_transport_capture(monkeypatch, _OPENAI_CHUNKS)
    _collect(OpenAIAdapter().stream(_feature_req(), api_key="k", profile=_profile(**_FULL)))
    assert captured.get("response_format") == _SCHEMA
    assert captured.get("tool_choice") == _FORCED_TOOL
    assert captured.get("reasoning_effort") == "high"


def test_build_request_omits_and_emulates_downgraded_features(monkeypatch):
    # obligation #6: nothing unsupported is sent raw; emulated_via_prompt downgrades are actually emulated.
    captured = _patch_transport_capture(monkeypatch, _OPENAI_CHUNKS)
    _collect(OpenAIAdapter().stream(_feature_req(), api_key="k", profile=_profile(**_WEAK)))
    assert "response_format" not in captured
    assert "tool_choice" not in captured
    assert "reasoning_effort" not in captured
    system = next(m["content"] for m in captured["messages"] if m["role"] == "system")
    assert "JSON" in system             # structured_output emulated via instruction
    assert "set_severity" in system     # forced tool emulated via instruction


# --- US4: one contract, N providers (parametrized conformance) ------------------------------------
# All three adapters satisfy the same ProviderAdapter contract (provider-adapter.md obligations):
# 1 event vocabulary (only EventType members; content as TEXT_DELTA; exactly one terminal USAGE),
# 2 raw usage (USAGE carries a NormalizedUsage; adapter invents no cost → cost_usd 0.0),
# 4 no secret leak (api_key never appears in any yielded payload). Proves provider-agnosticism (FR-002).

def _openai_usage_chunk(*, prompt, completion):
    usage = SimpleNamespace(
        prompt_tokens=prompt, completion_tokens=completion,
        prompt_tokens_details=None, completion_tokens_details=None, cost=0.123,
    )
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None))], usage=usage)


def _anthropic_usage_chunk(*, input_tokens, output_tokens):
    usage = SimpleNamespace(
        input_tokens=input_tokens, output_tokens=output_tokens,
        cache_read_input_tokens=0, cache_creation_input_tokens=0, cost=0.123,
    )
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None))], usage=usage)


_CONTRACT_CASES = [
    ("openai", OpenAIAdapter(), "gpt-5-mini",
     [_delta("a"), _delta("b"), _openai_usage_chunk(prompt=10, completion=3)]),
    ("ollama", OllamaAdapter(), "llama3.1:8b",
     [_delta("a"), _delta("b"), _openai_usage_chunk(prompt=10, completion=3)]),
    ("anthropic", AnthropicAdapter(), "claude-opus-5-5",
     [_delta("a"), _delta("b"), _anthropic_usage_chunk(input_tokens=10, output_tokens=3)]),
]


@pytest.mark.parametrize("provider,adapter,model,chunks", _CONTRACT_CASES, ids=[c[0] for c in _CONTRACT_CASES])
def test_adapter_contract_conformance(monkeypatch, provider, adapter, model, chunks):
    _patch_transport(monkeypatch, chunks)
    req = _req(provider=provider, model=model)
    events = _collect(adapter.stream(req, api_key="SECRET123", profile=None))

    # 1 — event vocabulary: every event is an EventType; content arrives as TEXT_DELTA; exactly one USAGE.
    assert all(isinstance(e.type, EventType) for e in events)
    assert "".join(e.data["text"] for e in events if e.type is EventType.TEXT_DELTA) == "ab"
    usage_events = [e for e in events if e.type is EventType.USAGE]
    assert len(usage_events) == 1

    # 2 — raw usage, no adapter-invented cost.
    usage = usage_events[0].data["usage"]
    assert usage.input_tokens == 10 and usage.output_tokens == 3
    assert usage.cost_usd == 0.0

    # 4 — the api_key never leaks into any emitted payload.
    assert "SECRET123" not in repr([e.data for e in events])
