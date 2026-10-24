"""Provider-adapter tests — NO live calls. The HTTP layer of each adapter is monkeypatched with canned
streamed responses; we assert usage parsing, the substitutions supports()/stream() record, and that cost
via compute_cost matches an independent hand-calc."""

import asyncio
import io
import urllib.error

import pytest

from cost import compute_cost
from ir import EventType, NormalizedRequest, NormalizedUsage, Substitution
from profiles.loader import CapabilityProfile, resolve_profile
from providers import ollama_adapter, openai_adapter
from providers.openai_adapter import OpenAIAdapter, _build_body
from providers.ollama_adapter import OllamaAdapter


def _collect(agen) -> list:
    async def _drive():
        return [ev async for ev in agen]

    return asyncio.run(_drive())


def _req(**overrides) -> NormalizedRequest:
    base = dict(
        provider="openai", model="gpt-5-mini", api_version=None,
        messages=[{"role": "user", "content": "triage this"}],
        system="be a SOC triage bot",
        params={"temperature": 0.2, "max_tokens": 500},
        tools=[{"type": "function", "function": {"name": "escalate"}}],
        response_format={"type": "json_object"},
        reasoning={"effort": "low"},
        cache_hints=[{"type": "ephemeral"}],
    )
    base.update(overrides)
    return NormalizedRequest(**base)


# ---------------------------------------------------------------------------------------------------
# OpenAI adapter
# ---------------------------------------------------------------------------------------------------

_OPENAI_SSE = [
    'data: {"choices":[{"delta":{"content":"{\\"severity\\":"}}]}',
    'data: {"choices":[{"delta":{"content":"\\"high\\"}"}}]}',
    'data: {"choices":[],"usage":{"prompt_tokens":1200,"completion_tokens":400,'
    '"prompt_tokens_details":{"cached_tokens":300},'
    '"completion_tokens_details":{"reasoning_tokens":150}}}',
    "data: [DONE]",
]


def test_openai_usage_parsing(monkeypatch):
    monkeypatch.setattr(openai_adapter, "_open_sse", lambda url, headers, body: iter(_OPENAI_SSE))
    profile = resolve_profile("openai", "gpt-5-mini")
    events = _collect(OpenAIAdapter().stream(_req(), api_key="k", profile=profile))

    text = "".join(e.data["text"] for e in events if e.type is EventType.TEXT)
    assert text == '{"severity":"high"}'

    usage = next(e.data["usage"] for e in events if e.type is EventType.USAGE)
    assert usage.input_tokens == 1200
    assert usage.output_tokens == 400
    assert usage.cached_input_tokens == 300      # prompt_tokens_details.cached_tokens
    assert usage.reasoning_tokens == 150         # completion_tokens_details.reasoning_tokens


def test_openai_cost_matches_hand_calc(monkeypatch):
    monkeypatch.setattr(openai_adapter, "_open_sse", lambda url, headers, body: iter(_OPENAI_SSE))
    profile = resolve_profile("openai", "gpt-5-mini")
    events = _collect(OpenAIAdapter().stream(_req(), api_key="k", profile=profile))
    usage = next(e.data["usage"] for e in events if e.type is EventType.USAGE)

    # hand-calc with gpt-5-mini pricing: input .6, cached_input .06, output 2.4, reasoning 2.4 per Mtok
    billable_input = 1200 - 300          # = 900
    hand = (900 * 0.6 + 300 * 0.06 + 400 * 2.4 + 150 * 2.4) / 1e6
    assert compute_cost(profile, usage) == pytest.approx(hand, rel=1e-9)


def test_openai_translates_max_tokens():
    body = _build_body(_req(params={"max_tokens": 777}), drop_temperature=False)
    assert "max_tokens" not in body
    assert body["max_completion_tokens"] == 777


def test_openai_drops_temperature_on_400(monkeypatch):
    """gpt-5 rejects a non-default temperature → the adapter drops it, records a Substitution, retries."""
    calls = {"n": 0}

    def fake_open(url, headers, body):
        calls["n"] += 1
        if "temperature" in body:
            raise urllib.error.HTTPError(
                url, 400, "Bad Request", {},
                io.BytesIO(b'{"error":{"message":"Unsupported value: temperature does not support 0.2"}}'),
            )
        return iter(_OPENAI_SSE)

    monkeypatch.setattr(openai_adapter, "_open_sse", fake_open)
    profile = resolve_profile("openai", "gpt-5-mini")
    events = _collect(OpenAIAdapter().stream(_req(), api_key="k", profile=profile))

    assert calls["n"] == 2  # first attempt rejected, retried without temperature
    subs = next(e.data["substitutions"] for e in events if e.type is EventType.USAGE)
    assert [s.feature for s in subs] == ["temperature"]
    assert subs[0].action == "dropped"


def test_openai_supports_records_missing_structured_output():
    # a hypothetical OpenAI-family model WITHOUT structured output → emulated_via_prompt
    profile = CapabilityProfile(
        provider="openai", model="x", api_version=None, pricing={}, limits={},
        features={"structured_output": False, "reasoning_control": True, "prompt_caching": True},
    )
    subs = OpenAIAdapter().supports(profile, _req())
    assert any(s.feature == "structured_output" and s.action == "emulated_via_prompt" for s in subs)


# ---------------------------------------------------------------------------------------------------
# Ollama adapter
# ---------------------------------------------------------------------------------------------------

_OLLAMA_NDJSON = [
    '{"message":{"role":"assistant","content":"{\\"severity\\":"},"done":false}',
    '{"message":{"role":"assistant","content":"\\"high\\"}"},"done":false}',
    '{"message":{"role":"assistant","content":""},"done":true,'
    '"prompt_eval_count":210,"eval_count":58}',
]


def test_ollama_usage_parsing(monkeypatch):
    monkeypatch.setattr(ollama_adapter, "_open_ndjson", lambda url, headers, body: iter(_OLLAMA_NDJSON))
    profile = resolve_profile("ollama", "llama3.1:8b")
    events = _collect(OllamaAdapter().stream(_req(provider="ollama", model="llama3.1:8b"),
                                             api_key="", profile=profile))

    text = "".join(e.data["text"] for e in events if e.type is EventType.TEXT)
    assert text == '{"severity":"high"}'

    usage = next(e.data["usage"] for e in events if e.type is EventType.USAGE)
    assert usage.input_tokens == 210            # prompt_eval_count
    assert usage.output_tokens == 58            # eval_count
    assert usage.cached_input_tokens == 0
    assert usage.reasoning_tokens == 0


def test_ollama_is_free(monkeypatch):
    monkeypatch.setattr(ollama_adapter, "_open_ndjson", lambda url, headers, body: iter(_OLLAMA_NDJSON))
    profile = resolve_profile("ollama", "llama3.1:8b")
    events = _collect(OllamaAdapter().stream(_req(provider="ollama", model="llama3.1:8b"),
                                             api_key="", profile=profile))
    usage = next(e.data["usage"] for e in events if e.type is EventType.USAGE)
    assert compute_cost(profile, usage) == 0.0


def test_ollama_supports_records_downgrades():
    profile = resolve_profile("ollama", "llama3.1:8b")
    req = _req(provider="ollama", model="llama3.1:8b",
               tool_choice={"type": "function", "function": {"name": "escalate"}})
    subs = {s.feature: s.action for s in OllamaAdapter().supports(profile, req)}
    assert subs["reasoning_control"] == "dropped"           # reasoning hint, profile lacks control
    assert subs["prompt_caching"] == "dropped"              # cache_hints, profile lacks caching
    assert subs["forced_tool_choice"] == "emulated_via_prompt"  # forced tool, profile can't force


def test_ollama_no_downgrade_when_request_is_plain():
    profile = resolve_profile("ollama", "llama3.1:8b")
    plain = _req(provider="ollama", model="llama3.1:8b",
                 reasoning=None, cache_hints=[], tool_choice=None)
    assert OllamaAdapter().supports(profile, plain) == []
