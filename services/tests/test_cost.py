"""M1 (RED) — cost computed from our own profiles (02 §4.1) + quantization (02L §7).

These hand-calc fixtures are the H2 "cost within tolerance" contract (spec §D). The reasoning
double-count guard and the cache-rate carve-outs are the ones most likely to regress.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from runtime.providers.cost import compute_cost, quantize_usd
from runtime.providers.ir import NormalizedUsage
from runtime.providers.profiles import CapabilityProfile

PRICING = {
    "input": 4.0, "output": 20.0, "cached_input": 0.4, "cache_write": 5.0,
    "reasoning": 20.0, "batch_multiplier": 0.5,
}


def _profile(pricing=None):
    return CapabilityProfile(
        provider="anthropic", model="m", api_version="2026-01",
        pricing=pricing or PRICING, limits={}, features={},
    )


def test_basic_input_output_cost():
    # 1000 input @4/M + 500 output @20/M = 0.004 + 0.010 = 0.014
    u = NormalizedUsage(input_tokens=1000, output_tokens=500)
    assert compute_cost(_profile(), u) == pytest.approx(0.014)


def test_reasoning_not_double_counted():
    # output=500 INCLUDES reasoning=200. With rate_reasoning==rate_output, the output side must bill
    # 500*20/1e6 = 0.010 total (300 output + 200 reasoning), NOT 0.014 (500 output + 200 reasoning).
    u = NormalizedUsage(input_tokens=0, output_tokens=500, reasoning_tokens=200)
    assert compute_cost(_profile(), u) == pytest.approx(0.010)


def test_cache_reads_and_writes_carved_from_input():
    # input=1000 INCLUDES cached=200 (@0.4) + cache_write=100 (@5). billable_input=700 (@4).
    # = 700*4/1e6 + 200*0.4/1e6 + 100*5/1e6 = 0.0028 + 0.00008 + 0.0005 = 0.00338
    u = NormalizedUsage(input_tokens=1000, output_tokens=0, cached_input_tokens=200, cache_write_tokens=100)
    assert compute_cost(_profile(), u) == pytest.approx(0.00338)


def test_batch_multiplier_applies_only_when_batch():
    u = NormalizedUsage(input_tokens=1000, output_tokens=500)
    base = compute_cost(_profile(), u, batch=False)
    assert compute_cost(_profile(), u, batch=True) == pytest.approx(base * 0.5)


def test_reasoning_rate_defaults_to_output_rate():
    pricing = {"input": 4.0, "output": 20.0}  # no explicit reasoning rate
    u = NormalizedUsage(input_tokens=0, output_tokens=500, reasoning_tokens=200)
    assert compute_cost(_profile(pricing), u) == pytest.approx(0.010)


def test_quantize_banker_rounding_half_to_even():
    assert quantize_usd(0.0000005) == Decimal("0.000000")  # 5 → nearest even (0)
    assert quantize_usd(0.0000015) == Decimal("0.000002")  # 5 → nearest even (2)


def test_quantize_no_binary_noise_and_scale6():
    q = quantize_usd(0.1)
    assert q == Decimal("0.100000") and q.as_tuple().exponent == -6
