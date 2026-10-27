"""Capability profiles + the substitution rule set (02 §4/§5, 02L §6).

A profile is versioned DATA per (provider, model, api_version): pricing + feature flags. The
gateway refuses to run a request feature the model lacks silently — it records a Substitution.
CapabilityProfile + Substitution are the data contracts; preflight_substitutions is implemented.
resolve_profile (the profile-JSON loader) is intentionally deferred to a later M1 batch.
"""
from __future__ import annotations

from dataclasses import dataclass

from runtime.providers.ir import NormalizedRequest


@dataclass(frozen=True)
class CapabilityProfile:
    provider: str
    model: str
    api_version: str | None
    pricing: dict        # USD per 1M tokens: input/output/cached_input/cache_write/reasoning/batch_multiplier
    limits: dict         # context_tokens / max_output_tokens
    features: dict       # structured_output / forced_tool_choice / reasoning_control / prompt_caching / …
    availability: str = "active"      # active | deprecated | retired (16 §3)
    sunset_at: str | None = None

    def feature(self, k: str) -> bool:
        return bool(self.features.get(k))


@dataclass(frozen=True)
class Substitution:
    feature: str
    action: str          # one of VALID_ACTIONS
    detail: str = ""


VALID_ACTIONS = frozenset({"dropped", "emulated_via_prompt", "downgraded"})


def preflight_substitutions(profile: CapabilityProfile, req: NormalizedRequest) -> list[Substitution]:
    """supports(): one Substitution per downgrade needed to make `req` runnable on this model
    (02L §6 rule table). Empty list when the profile supports everything the request uses."""
    subs: list[Substitution] = []
    if req.response_format is not None and not profile.feature("structured_output"):
        subs.append(
            Substitution("structured_output", "emulated_via_prompt", "JSON enforced via instruction, not API")
        )
    # A forced tool is a dict tool_choice; a string ("auto"/"none") is not forced.
    if isinstance(req.tool_choice, dict) and not profile.feature("forced_tool_choice"):
        subs.append(
            Substitution("forced_tool_choice", "emulated_via_prompt", "tool forced via prompt, not tool_choice")
        )
    if req.reasoning is not None and not profile.feature("reasoning_control"):
        subs.append(
            Substitution("reasoning_control", "dropped", "effort/budget not controllable on this model")
        )
    if req.cache_hints and not profile.feature("prompt_caching"):
        subs.append(Substitution("prompt_caching", "dropped", "prompt-cache breakpoints unsupported"))
    return subs


def resolve_profile(provider: str, model: str, api_version: str | None = None) -> CapabilityProfile:
    """Load + cache the profile JSON for (provider, model, api_version). Profile-file loading is a
    later M1 sub-batch; tests construct CapabilityProfile directly."""
    raise NotImplementedError("M1 — profile loader lands in a later batch")
