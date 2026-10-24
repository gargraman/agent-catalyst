"""Capability-profile loader — mirrors docs/backend/02-provider-gateway.md §4 (02:115-154).

Profiles are versioned DATA, not code. The JSON stores pricing under `pricing_usd_per_mtok`; the
loader maps that onto `CapabilityProfile.pricing` so cost.py (02:161-174) can index the same keys the
backend's compute_cost expects (input/output/cached_input/cache_write/reasoning/batch_multiplier).
"""

import json
from dataclasses import dataclass
from pathlib import Path

_PROFILE_DIR = Path(__file__).parent


@dataclass(frozen=True)
class CapabilityProfile:
    # 02:146-152
    provider: str
    model: str
    api_version: str | None
    pricing: dict        # USD per 1M tokens: input/output/cached_input/cache_write/reasoning/batch_multiplier
    limits: dict         # context_tokens / max_output_tokens
    features: dict       # structured_output / forced_tool_choice / reasoning_control / prompt_caching / tool_call_format
    availability: str = "active"      # active | deprecated | retired
    sunset_at: str | None = None      # ISO date the provider retires the model; None = none announced

    def feature(self, k: str) -> bool:
        return bool(self.features.get(k))


def _profile_from_json(doc: dict) -> CapabilityProfile:
    return CapabilityProfile(
        provider=doc["provider"],
        model=doc["model"],
        api_version=doc.get("api_version"),
        pricing=dict(doc["pricing_usd_per_mtok"]),  # map JSON key → .pricing
        limits=dict(doc.get("limits", {})),
        features=dict(doc.get("features", {})),
        availability=doc.get("availability", "active"),
        sunset_at=doc.get("sunset_at"),
    )


def resolve_profile(provider: str, model: str, dir: str | Path = _PROFILE_DIR) -> CapabilityProfile:
    """Load the profile whose (provider, model) match. Matches on file CONTENT, not filename, so model
    ids containing `/` or `:` (e.g. `llama3.1:8b`) don't need to be filesystem-safe."""
    directory = Path(dir)
    for path in sorted(directory.glob("*.json")):
        doc = json.loads(path.read_text())
        if doc.get("provider") == provider and doc.get("model") == model:
            return _profile_from_json(doc)
    raise FileNotFoundError(f"no capability profile for provider={provider!r} model={model!r} in {directory}")
