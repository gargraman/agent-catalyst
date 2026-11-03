"""Gateway orchestration tests (M1) — NO live calls (transport seam monkeypatched).

US1 scope: the gateway resolves the profile, picks the right adapter, and passes normalized events
through, ending in exactly one USAGE — for both providers, with no caller-side provider branching.
Cost attachment (US2), substitution merge (US3), and fail-closed/error/secret cases (Polish) land in
their own phases.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from runtime.providers import gateway, transport
from runtime.providers.cost import compute_cost
from runtime.providers.ir import EventType, NormalizedRequest
from runtime.providers.profiles import CapabilityProfile, resolve_profile


def _delta(text):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))], usage=None)


def _usage_chunk(*, prompt=0, completion=0, cached=None, reasoning=None, litellm_cost=0.99):
    ptd = SimpleNamespace(cached_tokens=cached) if cached is not None else None
    ctd = SimpleNamespace(reasoning_tokens=reasoning) if reasoning is not None else None
    usage = SimpleNamespace(
        prompt_tokens=prompt, completion_tokens=completion,
        prompt_tokens_details=ptd, completion_tokens_details=ctd,
        cost=litellm_cost,  # litellm's own cost — MUST be ignored by us
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


def _req(**over):
    base = dict(
        provider="openai", model="gpt-5-mini", api_version=None,
        messages=[{"role": "user", "content": "hi"}], system=None, params={},
    )
    base.update(over)
    return NormalizedRequest(**base)


_CHUNKS = [_delta("ans"), _delta("wer"), _usage_chunk(prompt=10, completion=3)]


def test_gateway_passthrough_openai(monkeypatch):
    _patch_transport(monkeypatch, _CHUNKS)
    events = _collect(gateway.stream(_req(), api_key="k"))
    text = "".join(e.data["text"] for e in events if e.type is EventType.TEXT_DELTA)
    assert text == "answer"
    assert sum(1 for e in events if e.type is EventType.USAGE) == 1


def test_gateway_passthrough_ollama(monkeypatch):
    # proves no caller-side provider branching: same call shape, different provider resolves + streams
    _patch_transport(monkeypatch, _CHUNKS)
    events = _collect(gateway.stream(_req(provider="ollama", model="llama3.1:8b"), api_key=""))
    text = "".join(e.data["text"] for e in events if e.type is EventType.TEXT_DELTA)
    assert text == "answer"
    usage = next(e.data["usage"] for e in events if e.type is EventType.USAGE)
    assert usage.input_tokens == 10 and usage.output_tokens == 3


# --- US2: honest, independently-computed cost ----------------------------------------------------

# gpt-5-mini: input .6, cached_input .06, output 2.4, reasoning 2.4 (per profiles_data).
#   billable_input=1200-300=900, billable_output=400-150=250
#   (900*.6 + 300*.06 + 250*2.4 + 150*2.4)/1e6 = (540+18+600+360)/1e6 = 0.001518
_COST_CHUNKS = [_delta("x"), _usage_chunk(prompt=1200, completion=400, cached=300, reasoning=150, litellm_cost=9.99)]


def test_gateway_attaches_our_cost_openai(monkeypatch):
    _patch_transport(monkeypatch, _COST_CHUNKS)
    usage = next(e.data["usage"] for e in _collect(gateway.stream(_req(), api_key="k"))
                 if e.type is EventType.USAGE)
    assert usage.cost_usd == pytest.approx(0.001518, abs=1e-9)  # reasoning billed once, not twice


def test_gateway_ignores_litellm_cost(monkeypatch):
    # the mock reports a wildly wrong litellm cost (9.99); ours must come only from the profile (P7/FR-003)
    _patch_transport(monkeypatch, _COST_CHUNKS)
    usage = next(e.data["usage"] for e in _collect(gateway.stream(_req(), api_key="k"))
                 if e.type is EventType.USAGE)
    profile = resolve_profile("openai", "gpt-5-mini")
    assert usage.cost_usd == pytest.approx(compute_cost(profile, usage), abs=1e-9)
    assert usage.cost_usd != 9.99


def test_gateway_ollama_is_free(monkeypatch):
    _patch_transport(monkeypatch, [_delta("x"), _usage_chunk(prompt=210, completion=58)])
    usage = next(e.data["usage"] for e in
                 _collect(gateway.stream(_req(provider="ollama", model="llama3.1:8b"), api_key=""))
                 if e.type is EventType.USAGE)
    assert usage.cost_usd == 0.0


def test_gateway_marks_estimate_for_provisional_profile(monkeypatch):
    # FR-010: cost from an unreconciled/provisional profile is labeled an estimate, not an exact charge
    _patch_transport(monkeypatch, _COST_CHUNKS)
    prov = CapabilityProfile(
        provider="openai", model="gpt-5-mini", api_version=None,
        pricing={"input": 0.6, "output": 2.4, "cached_input": 0.06, "reasoning": 2.4},
        limits={}, features={}, provisional=True,
    )
    usage_ev = next(e for e in _collect(gateway.stream(_req(), api_key="k", profile=prov))
                    if e.type is EventType.USAGE)
    assert usage_ev.data.get("cost_is_estimate") is True


def test_gateway_cost_not_estimate_for_reviewed_profile(monkeypatch):
    # a reviewed (non-provisional) profile → cost is NOT flagged an estimate. (The seeded openai profiles
    # are provisional until the paid H2 reconciliation, so this uses an explicitly-reviewed profile.)
    _patch_transport(monkeypatch, _COST_CHUNKS)
    reviewed = CapabilityProfile(
        provider="openai", model="gpt-5-mini", api_version=None,
        pricing={"input": 0.6, "output": 2.4, "cached_input": 0.06, "reasoning": 2.4},
        limits={}, features={},  # provisional defaults to False
    )
    usage_ev = next(e for e in _collect(gateway.stream(_req(), api_key="k", profile=reviewed))
                    if e.type is EventType.USAGE)
    assert usage_ev.data.get("cost_is_estimate", False) is False


# --- US3: substitution merge onto the USAGE event ------------------------------------------------

_PRICING = {"input": 0.6, "output": 2.4, "cached_input": 0.06, "reasoning": 2.4}


def _profile(features):
    return CapabilityProfile(
        provider="openai", model="gpt-5-mini", api_version=None,
        pricing=_PRICING, limits={}, features=features,
    )


def test_gateway_merges_preflight_substitutions_onto_usage(monkeypatch):
    # the gateway calls adapter.supports() and surfaces the downgrades on the terminal USAGE event.
    _patch_transport(monkeypatch, _COST_CHUNKS)
    weak = _profile({})  # supports nothing
    req = _req(response_format={"json_schema": {"schema": {"type": "object"}}}, reasoning={"effort": "high"})
    usage_ev = next(e for e in _collect(gateway.stream(req, api_key="k", profile=weak))
                    if e.type is EventType.USAGE)
    by = {(s.feature, s.action) for s in usage_ev.data["substitutions"]}
    assert ("structured_output", "emulated_via_prompt") in by
    assert ("reasoning_control", "dropped") in by


def test_gateway_no_substitutions_when_fully_supported(monkeypatch):
    # no false positives: a capable profile running the same request records no downgrades.
    _patch_transport(monkeypatch, _COST_CHUNKS)
    full = _profile({"structured_output": True, "forced_tool_choice": True,
                     "reasoning_control": True, "prompt_caching": True})
    req = _req(response_format={"json_schema": {"schema": {"type": "object"}}}, reasoning={"effort": "high"})
    usage_ev = next(e for e in _collect(gateway.stream(req, api_key="k", profile=full))
                    if e.type is EventType.USAGE)
    assert usage_ev.data.get("substitutions", []) == []


def test_gateway_records_cache_hint_drop_on_capable_model(monkeypatch):
    # code review F1: caching-capable profile, but M1 does not forward cache breakpoints → the drop MUST be
    # recorded, never silent (P7). preflight is empty here (feature supported), so this proves the
    # implementation-gap substitution surfaces.
    _patch_transport(monkeypatch, _COST_CHUNKS)
    capable = _profile({"prompt_caching": True})
    req = _req(cache_hints=[{"index": 0}])
    usage_ev = next(e for e in _collect(gateway.stream(req, api_key="k", profile=capable))
                    if e.type is EventType.USAGE)
    by = {(s.feature, s.action) for s in usage_ev.data["substitutions"]}
    assert ("prompt_caching", "dropped") in by


def test_gateway_records_budget_reasoning_drop_on_capable_model(monkeypatch):
    # code review F2: reasoning-capable profile, but M1 maps only `effort` → a token-budget request is a
    # recorded downgrade, not a silent drop.
    _patch_transport(monkeypatch, _COST_CHUNKS)
    capable = _profile({"reasoning_control": True})
    req = _req(reasoning={"budget": 2000})
    usage_ev = next(e for e in _collect(gateway.stream(req, api_key="k", profile=capable))
                    if e.type is EventType.USAGE)
    by = {(s.feature, s.action) for s in usage_ev.data["substitutions"]}
    assert ("reasoning_control", "downgraded") in by
