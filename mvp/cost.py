"""Cost computation — the single authority for cost.

Copied EXACTLY from docs/backend/02-provider-gateway.md §4.1 (02:161-174). `batch=True` (eval/ingest via
the Batch API) applies the profile's batch_multiplier; cache writes/reads are priced at their own rates.
Interactive paths (playground, published invoke) always pass batch=False.
"""

from ir import NormalizedUsage
from profiles.loader import CapabilityProfile


def compute_cost(profile: CapabilityProfile, u: NormalizedUsage, *, batch: bool = False) -> float:
    p = profile.pricing  # USD per 1M tokens
    billable_input = u.input_tokens - u.cached_input_tokens - u.cache_write_tokens
    mult = p.get("batch_multiplier", 1.0) if batch else 1.0
    return mult * (
        billable_input        * p["input"]        / 1e6
      + u.cached_input_tokens * p.get("cached_input", p["input"]) / 1e6
      + u.cache_write_tokens  * p.get("cache_write",  p["input"]) / 1e6
      + u.output_tokens       * p["output"]       / 1e6
      + u.reasoning_tokens    * p.get("reasoning", p["output"]) / 1e6
    )
