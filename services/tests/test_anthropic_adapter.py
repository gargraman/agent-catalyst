"""Anthropic adapter tests (M1 US4) — NO live calls. The transport seam is monkeypatched with chunks
shaped like LiteLLM's streamed Anthropic response. These are driven through the gateway so the RED is a
genuine BEHAVIORAL failure (the system cannot execute an anthropic request — adapter_for('anthropic')
fails closed) rather than a test-collection import error.

The crux (FR-005, usage-mapping.md ⚠): Anthropic reports input_tokens EXCLUSIVE of cache tokens; we add
cache_read + cache_creation back so NormalizedUsage.input_tokens is INCLUSIVE (our convention / cost.py).

ASSUMPTION pinned here (no live calls in M1): LiteLLM surfaces Anthropic's NATIVE usage field names on the
streamed usage object. This test is authoritative over that assumption — if a live run shows LiteLLM
pre-normalizes Anthropic into OpenAI's inclusive shape, the canned chunk + mapper change together and this
test is re-pinned.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from runtime.providers import gateway, transport
from runtime.providers.cost import compute_cost
from runtime.providers.ir import EventType, NormalizedRequest
from runtime.providers.profiles import resolve_profile


def _delta(text):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))], usage=None)


def _anthropic_usage_chunk(*, input_tokens, output_tokens, cache_read=0, cache_creation=0, cost=7.77):
    # Anthropic NATIVE usage field names (input_tokens EXCLUSIVE of cache); litellm's own cost must be ignored.
    usage = SimpleNamespace(
        input_tokens=input_tokens, output_tokens=output_tokens,
        cache_read_input_tokens=cache_read, cache_creation_input_tokens=cache_creation,
        cost=cost,
    )
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None))], usage=usage)


def _patch_transport(monkeypatch, chunks):
    async def _fake(**kwargs):
        for c in chunks:
            yield c
    monkeypatch.setattr(transport, "stream_chunks", _fake)


def _collect(agen):
    async def _drive():
        return [ev async for ev in agen]
    return asyncio.run(_drive())


def _req(model="claude-opus-5-5"):
    return NormalizedRequest(
        provider="anthropic", model=model, api_version=None,
        messages=[{"role": "user", "content": "triage this"}],
        system="be a SOC triage bot", params={"temperature": 0.2},
    )


# input EXCLUSIVE of cache: 1000 base + 200 read + 100 creation; output 500.
_CHUNKS = [
    _delta('{"severity":'), _delta('"high"}'),
    _anthropic_usage_chunk(input_tokens=1000, output_tokens=500, cache_read=200, cache_creation=100),
]


def test_anthropic_usage_mapping_makes_input_inclusive(monkeypatch):
    _patch_transport(monkeypatch, _CHUNKS)
    events = _collect(gateway.stream(_req(), api_key="k"))
    text = "".join(e.data["text"] for e in events if e.type is EventType.TEXT_DELTA)
    assert text == '{"severity":"high"}'
    usage = next(e.data["usage"] for e in events if e.type is EventType.USAGE)
    assert usage.cached_input_tokens == 200          # cache_read_input_tokens
    assert usage.cache_write_tokens == 100           # cache_creation_input_tokens
    assert usage.input_tokens == 1300                # 1000 + 200 + 100 (add-back → inclusive)
    assert usage.output_tokens == 500
    assert usage.reasoning_tokens == 0               # honest absence (no extended-thinking count)


def test_anthropic_cost_matches_hand_calc(monkeypatch):
    # opus-5-5 profile: input 4, output 20 per MTok; cache slices fall back to the input rate (rates omitted).
    #   billable_input = 1300-200-100 = 1000 → 1000*4/1e6 = 0.004
    #   cached_input    = 200*4/1e6  = 0.0008 ; cache_write = 100*4/1e6 = 0.0004
    #   billable_output = 500*20/1e6 = 0.01
    #   total = 0.0152
    _patch_transport(monkeypatch, _CHUNKS)
    usage_ev = next(e for e in _collect(gateway.stream(_req(), api_key="k")) if e.type is EventType.USAGE)
    assert usage_ev.data["usage"].cost_usd == pytest.approx(0.0152, abs=1e-9)
    assert usage_ev.data["usage"].cost_usd != 7.77   # litellm's cost ignored (P7/FR-003)
    # reconciles to compute_cost on the resolved profile + mapped usage
    profile = resolve_profile("anthropic", "claude-opus-5-5")
    assert usage_ev.data["usage"].cost_usd == pytest.approx(compute_cost(profile, usage_ev.data["usage"]), abs=1e-9)
    assert usage_ev.data.get("cost_is_estimate") is True   # provisional profile (FR-010)


def test_anthropic_usage_mapping_handles_litellm_openai_shape():
    # code review F3 robustness: if LiteLLM pre-normalizes Anthropic into OpenAI's INCLUSIVE shape
    # (prompt_tokens + prompt_tokens_details.cached_tokens, no native input_tokens attr), the mapper must
    # still map correctly — NO cache add-back (prompt_tokens is already inclusive), so cost stays right.
    from runtime.providers._common import map_anthropic_usage
    usage = SimpleNamespace(
        prompt_tokens=1300, completion_tokens=500,
        prompt_tokens_details=SimpleNamespace(cached_tokens=200),
        completion_tokens_details=None,
    )
    u = map_anthropic_usage(usage)
    assert u.input_tokens == 1300        # already inclusive — not double-counted to 1500
    assert u.output_tokens == 500
    assert u.cached_input_tokens == 200
