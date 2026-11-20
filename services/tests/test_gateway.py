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
from runtime.providers.ir import EventType, NormalizedRequest, NormalizedStreamEvent
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


# --- Polish: fail-closed models, transport-error normalization, secret non-leakage ----------------

def _raising_transport(monkeypatch, exc):
    async def _boom(**kwargs):
        raise exc
        yield  # make this an async generator (unreachable)
    monkeypatch.setattr(transport, "stream_chunks", _boom)


def test_unknown_model_fails_closed(monkeypatch):
    # FR-008: an unknown (provider, model) is a clear, caller-visible error — never a guessed model/pricing.
    _patch_transport(monkeypatch, _COST_CHUNKS)
    with pytest.raises((FileNotFoundError, LookupError, ValueError)):
        _collect(gateway.stream(_req(model="does-not-exist"), api_key="k"))


def test_retired_model_not_executed(monkeypatch):
    # gateway behavior #2: a retired profile must NOT run as if active.
    _patch_transport(monkeypatch, _COST_CHUNKS)
    retired = CapabilityProfile(
        provider="openai", model="gpt-retired", api_version=None,
        pricing=_PRICING, limits={}, features={}, availability="retired",
    )
    with pytest.raises(gateway.ModelRetiredError):
        _collect(gateway.stream(_req(model="gpt-retired"), api_key="k", profile=retired))


def test_transport_error_normalized(monkeypatch):
    # FR-009/P8: a transport failure (the seam raises TransportError) surfaces as a normalized ERROR event —
    # stable type, retryable hint carried from the seam, no raw SDK type, no faked USAGE/success.
    _raising_transport(monkeypatch, transport.TransportError("upstream timed out", retryable=True, status=408))
    events = _collect(gateway.stream(_req(), api_key="k"))
    assert events and events[-1].type is EventType.ERROR
    err = events[-1].data["error"]
    assert err["type"] == "provider_error" and err["retryable"] is True and err["status"] == 408
    assert all(e.type is not EventType.USAGE for e in events)


def test_adapter_bug_not_normalized_as_provider_error(monkeypatch):
    # code review F3 (honest failure): a non-TransportError reaching the gateway is OUR bug, not a provider
    # failure — it must propagate uncaught, never be swallowed/mislabeled as provider_error.
    _raising_transport(monkeypatch, ValueError("bug in our mapping code"))
    with pytest.raises(ValueError):
        _collect(gateway.stream(_req(), api_key="k"))


def test_gateway_does_not_double_error_when_adapter_surfaces_error(monkeypatch):
    # code review F2: an adapter that yields its own ERROR and ends without USAGE must NOT get a second
    # 'degraded' ERROR appended by the gateway.
    class _ErroringAdapter:
        provider = "openai"

        def supports(self, profile, req):
            return []

        async def stream(self, req, *, api_key, profile, timeout=None):
            yield NormalizedStreamEvent(
                EventType.ERROR, {"error": {"type": "provider_error", "retryable": False, "message": "boom"}}
            )

    monkeypatch.setattr(gateway, "adapter_for", lambda provider: _ErroringAdapter())
    errors = [e for e in _collect(gateway.stream(_req(), api_key="k", profile=_profile({})))
              if e.type is EventType.ERROR]
    assert len(errors) == 1                               # exactly one, no spurious 'degraded'
    assert errors[0].data["error"]["type"] == "provider_error"


def test_interrupted_stream_is_degraded(monkeypatch):
    # P8: a stream that ends with no USAGE is NOT a silent success — the gateway emits a degraded ERROR.
    _patch_transport(monkeypatch, [_delta("partial, then the connection dropped")])  # content, no usage chunk
    events = _collect(gateway.stream(_req(), api_key="k"))
    assert any(e.type is EventType.ERROR for e in events)
    assert all(e.type is not EventType.USAGE for e in events)


def test_api_key_not_leaked_on_success(monkeypatch):
    # FR-012/P5/SC-006: the api_key appears in zero emitted payloads on a normal stream.
    _patch_transport(monkeypatch, _COST_CHUNKS)
    events = _collect(gateway.stream(_req(), api_key="SECRET123"))
    assert "SECRET123" not in repr([e.data for e in events])


def test_api_key_not_leaked_in_error(monkeypatch):
    # even when a provider error message embeds the key, it must be scrubbed from the ERROR event.
    _raising_transport(
        monkeypatch,
        transport.TransportError("auth failed for key SECRET123", retryable=False, status=401),
    )
    events = _collect(gateway.stream(_req(), api_key="SECRET123"))
    assert events[-1].type is EventType.ERROR
    assert "SECRET123" not in repr([e.data for e in events])


def test_gateway_threads_timeout_to_transport(monkeypatch):
    # T012 (research D5 / review C5): an explicit timeout threads gateway → adapter → stream_via_transport
    # → the litellm call, so the transport itself is bounded (defense in depth beyond the invoke deadline).
    captured: dict = {}

    async def _fake(**kwargs):
        captured.update(kwargs)
        for c in _CHUNKS:
            yield c

    monkeypatch.setattr(transport, "stream_chunks", _fake)
    _collect(gateway.stream(_req(), api_key="k", timeout=12.5))
    assert captured.get("timeout") == 12.5


def test_gateway_omits_timeout_by_default(monkeypatch):
    # Default is None → the kwarg is NOT forwarded, so M1's existing (unbounded-transport) behavior and its
    # mocked tests are unaffected.
    captured: dict = {}

    async def _fake(**kwargs):
        captured.update(kwargs)
        for c in _CHUNKS:
            yield c

    monkeypatch.setattr(transport, "stream_chunks", _fake)
    _collect(gateway.stream(_req(), api_key="k"))
    assert "timeout" not in captured


def test_cost_clamps_inconsistent_usage(monkeypatch):
    # T032 (converge F1) / FR-005 + edge-case "Inconsistent provider usage numbers": degenerate usage —
    # reasoning_tokens > output_tokens AND cached_input_tokens > input_tokens — must never yield a negative
    # cost, and must be deterministic. cost.py clamps billable slices with max(0, …); this verifies the
    # guard end-to-end through the gateway USAGE path.
    _patch_transport(monkeypatch, [_delta("x"), _usage_chunk(prompt=100, completion=50, cached=300, reasoning=500)])
    profile = _profile({})  # pricing=_PRICING (input .6, output 2.4, cached_input .06, reasoning 2.4)
    usages = [
        next(e.data["usage"] for e in _collect(gateway.stream(_req(), api_key="k", profile=profile))
             if e.type is EventType.USAGE)
        for _ in range(2)
    ]
    # billable_input=max(0,100-300)=0, billable_output=max(0,50-500)=0 → cost = cached+reasoning slices only:
    #   300*.06/1e6 + 500*2.4/1e6 = (18 + 1200)/1e6 = 0.001218
    assert usages[0].cost_usd >= 0.0                                   # never negative (the clamp holds)
    assert usages[0].cost_usd == pytest.approx(0.001218, abs=1e-9)    # exact + deterministic
    assert usages[0].cost_usd == usages[1].cost_usd                   # reproducible
