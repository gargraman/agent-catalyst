"""The single cost authority (02 §4.1) + the one quantization point (02L §7).

We compute $ ourselves from versioned capability profiles — never trust LiteLLM's cost field.
Pinned token-accounting convention (both sides inclusive-then-subtract):
  - input_tokens is INCLUSIVE of cached + cache_write → carve them out, price each slice once.
  - output_tokens is INCLUSIVE of reasoning → bill reasoning once at rate_reasoning (defaults to
    rate_output), never twice.
The hand-calc fixtures in test_cost.py pin the contract.

NOTE (review #5, design question deferred to docs): compute_cost matches 02 §4.1 exactly —
`batch_multiplier` scales the WHOLE sum, including the cached_input / cache_write slices. Whether a
provider's Batch pricing should also discount cache reads/writes is a profile-semantics question for
the `02` owners; this code intentionally does not diverge from the LLD.
"""
from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

from runtime.providers.ir import NormalizedUsage
from runtime.providers.profiles import CapabilityProfile

_CENTS6 = Decimal("0.000001")  # numeric(12,6) scale


def compute_cost(profile: CapabilityProfile, u: NormalizedUsage, *, batch: bool = False) -> float:
    """The single authority for cost. batch=True (eval/ingest via Batch API) applies the profile's
    batch_multiplier; cache reads/writes priced at their own rates; reasoning carved out of output."""
    p = profile.pricing  # USD per 1M tokens
    billable_input = max(0, u.input_tokens - u.cached_input_tokens - u.cache_write_tokens)
    billable_output = max(0, u.output_tokens - u.reasoning_tokens)  # reasoning carved out
    mult = p.get("batch_multiplier", 1.0) if batch else 1.0
    return mult * (
        billable_input * p["input"] / 1e6
        + u.cached_input_tokens * p.get("cached_input", p["input"]) / 1e6
        + u.cache_write_tokens * p.get("cache_write", p["input"]) / 1e6
        + billable_output * p["output"] / 1e6
        + u.reasoning_tokens * p.get("reasoning", p["output"]) / 1e6  # rate_reasoning defaults to rate_output
    )


def quantize_usd(value: float | Decimal) -> Decimal:
    """Single quantization point (02L §7): Decimal(str(value)) to avoid binary noise, ROUND_HALF_EVEN
    (Postgres numeric default), scale 6. Quantize ONCE at attach time; never re-quantize downstream."""
    return Decimal(str(value)).quantize(_CENTS6, rounding=ROUND_HALF_EVEN)
