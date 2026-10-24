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
    # billable_input  = 10000 - 2000 - 1000 = 7000
    # billable_output = 1000 - 500 = 500 (reasoning carved OUT of the inclusive completion_tokens)
    # (7000*1 + 2000*0.1 + 1000*1.25 + 500*5 + 500*5) / 1e6
    # = (7000 + 200 + 1250 + 2500 + 2500) / 1e6 = 13450/1e6
    assert compute_cost(p, u) == pytest.approx(0.01345, rel=0, abs=1e-12)


def test_reasoning_not_double_counted():
    """Guard for CRITICAL #1: completion_tokens is INCLUSIVE of reasoning_tokens, so reasoning must be
    carved out of the output side and NOT billed twice. Literal expected value, independent of cost.py."""
    # distinct output/reasoning rates so a double-count would be numerically obvious
    p = _profile({"input": 1.0, "output": 2.0, "reasoning": 10.0})
    u = NormalizedUsage(input_tokens=1_000, output_tokens=800, reasoning_tokens=300)
    # billable_output = 800 - 300 = 500
    # correct   : (1000*1 + 500*2 + 300*10) / 1e6 = (1000 + 1000 + 3000)/1e6 = 5000/1e6 = 0.005
    # the bug   : (1000*1 + 800*2 + 300*10) / 1e6 = 6600/1e6 = 0.0066  (reasoning billed twice)
    assert compute_cost(p, u) == pytest.approx(0.005, rel=0, abs=1e-12)


def test_reasoning_falls_back_to_output_rate():
    """When the profile omits an explicit 'reasoning' rate, reasoning tokens fall back to the output rate —
    but are still billed exactly once (output carved out, reasoning added back at the same rate)."""
    p = _profile({"input": 1.0, "output": 2.0})  # no 'reasoning' key
    u = NormalizedUsage(input_tokens=1_000, output_tokens=800, reasoning_tokens=300)
    # fallback: reasoning priced at output rate → total output effectively 800*2
    # (1000*1 + 500*2 + 300*2) / 1e6 = (1000 + 1000 + 600)/1e6 = 2600/1e6 = 0.0026
    assert compute_cost(p, u) == pytest.approx(0.0026, rel=0, abs=1e-12)


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
