"""compute_cost vs hand-calculated fixtures (mirrors the 02 §4.1 formula; spec.md §D cost test)."""

import pytest

from cost import compute_cost
from ir import NormalizedUsage
from profiles.loader import CapabilityProfile, resolve_profile


def _profile(pricing: dict) -> CapabilityProfile:
    return CapabilityProfile(
        provider="test", model="test", api_version=None,
        pricing=pricing, limits={}, features={},
    )


def test_simple_input_output():
    # haiku-like pricing, no cache/reasoning
    p = _profile({"input": 1.0, "output": 5.0})
    u = NormalizedUsage(input_tokens=1000, output_tokens=500)
    # (1000*1 + 500*5) / 1e6 = 3500/1e6
    assert compute_cost(p, u) == pytest.approx(0.0035, rel=0, abs=1e-12)


def test_cached_cache_write_and_reasoning():
    p = _profile({
        "input": 1.0, "output": 5.0,
        "cached_input": 0.1, "cache_write": 1.25, "reasoning": 5.0,
    })
    u = NormalizedUsage(
        input_tokens=10_000,
        output_tokens=1_000,
        cached_input_tokens=2_000,
        cache_write_tokens=1_000,
        reasoning_tokens=500,
    )
    # billable_input = 10000 - 2000 - 1000 = 7000
    # (7000*1 + 2000*0.1 + 1000*1.25 + 1000*5 + 500*5) / 1e6
    # = (7000 + 200 + 1250 + 5000 + 2500) / 1e6 = 15950/1e6
    assert compute_cost(p, u) == pytest.approx(0.01595, rel=0, abs=1e-12)


def test_batch_multiplier():
    # gpt-5-mini-like pricing with batch discount
    p = _profile({"input": 0.6, "output": 2.4, "batch_multiplier": 0.5})
    u = NormalizedUsage(input_tokens=2_000, output_tokens=1_000)
    # 0.5 * (2000*0.6 + 1000*2.4) / 1e6 = 0.5 * 3600/1e6 = 1800/1e6
    assert compute_cost(p, u, batch=True) == pytest.approx(0.0018, rel=0, abs=1e-12)
    # batch=False → no multiplier
    assert compute_cost(p, u, batch=False) == pytest.approx(0.0036, rel=0, abs=1e-12)


def test_from_loaded_hosted_profile():
    # exercises the loader's pricing_usd_per_mtok → .pricing mapping end-to-end
    p = resolve_profile("openai", "gpt-5-mini")
    u = NormalizedUsage(input_tokens=1_000_000, output_tokens=1_000_000)
    # 0.6 + 2.4 = 3.0 USD at 1M in + 1M out
    assert compute_cost(p, u) == pytest.approx(3.0, rel=0, abs=1e-9)


def test_ollama_profile_is_free():
    p = resolve_profile("ollama", "llama3.1:8b")
    u = NormalizedUsage(input_tokens=5_000_000, output_tokens=5_000_000, reasoning_tokens=1_000)
    assert compute_cost(p, u) == 0.0
