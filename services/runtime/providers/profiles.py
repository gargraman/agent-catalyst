"""Capability profiles + the substitution rule set (02 §4/§5, 02L §6).

A profile is versioned DATA per (provider, model, api_version): pricing + feature flags. The
gateway refuses to run a request feature the model lacks silently — it records a Substitution.
CapabilityProfile + Substitution are the data contracts; preflight_substitutions is implemented.
resolve_profile loads versioned profile JSON from profiles_data/ by content match (provider+model).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from runtime.providers.ir import NormalizedRequest

_PROFILE_DIR = Path(__file__).parent / "profiles_data"


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
    provisional: bool = False         # True = pricing unreconciled → cost labeled an estimate (FR-010/P7)
    # feature 024 (M7, research D2): embedding profiles live beside chat profiles, distinguished by `kind`.
    kind: str = "chat"                # "chat" | "embedding"
    dimension: int | None = None      # embedding vector length (embedding profiles only)
    max_batch: int | None = None      # max texts per embedding call
    max_input_tokens: int | None = None

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


def _profile_from_json(doc: dict) -> CapabilityProfile:
    """Map the on-disk profile JSON (pricing under `pricing_usd_per_mtok`) onto CapabilityProfile so
    cost.compute_cost indexes the same keys (input/output/cached_input/cache_write/reasoning/
    batch_multiplier). `provisional` is read through to the profile (FR-010 → cost labeled estimate);
    other extra top-level keys (`_note`) are ignored."""
    return CapabilityProfile(
        provider=doc["provider"],
        model=doc["model"],
        api_version=doc.get("api_version"),
        pricing=dict(doc["pricing_usd_per_mtok"]),
        limits=dict(doc.get("limits", {})),
        features=dict(doc.get("features", {})),
        availability=doc.get("availability", "active"),
        sunset_at=doc.get("sunset_at"),
        provisional=bool(doc.get("provisional", False)),
        kind=doc.get("kind", "chat"),
        dimension=doc.get("dimension"),
        max_batch=doc.get("max_batch"),
        max_input_tokens=doc.get("max_input_tokens"),
    )


@lru_cache(maxsize=256)
def resolve_profile(
    provider: str, model: str, api_version: str | None = None, *, dir: str | Path = _PROFILE_DIR
) -> CapabilityProfile:
    """Load the capability+pricing profile whose JSON `provider`+`model` match. Matches on file CONTENT,
    not filename, so model ids containing '/' or ':' (e.g. 'llama3.1:8b') need no filesystem-safe name.
    `api_version` is advisory (provider+model is the key). Fails closed (FR-008): a clear, caller-visible
    error — never a guessed model/pricing — when no profile matches.

    Cached (profiles are versioned, immutable data): the gateway calls this on every invoke, so the
    glob+read+parse runs once per (provider, model, api_version, dir), not per request. CapabilityProfile
    is frozen, so sharing a cached instance is safe. Misses (FileNotFoundError) are not cached."""
    directory = Path(dir)
    for path in sorted(directory.glob("*.json")):
        doc = json.loads(path.read_text())
        if doc.get("provider") == provider and doc.get("model") == model:
            return _profile_from_json(doc)
    raise FileNotFoundError(
        f"no capability profile for provider={provider!r} model={model!r} in {directory}"
    )


def resolve_embedding_profile(provider: str, model: str) -> CapabilityProfile:
    """Resolve an EMBEDDING profile (feature 024). Fails closed with FileNotFoundError for an unknown model AND for a
    chat model: a chat model is not an embedder, and guessing would mean wrong vectors and wrong pricing."""
    profile = resolve_profile(provider, model)
    if profile.kind != "embedding":
        raise FileNotFoundError(f"no embedding profile for provider={provider!r} model={model!r} (it is a {profile.kind} model)")
    return profile


@lru_cache(maxsize=16)
def _embedding_index(dir: str) -> tuple[CapabilityProfile, ...]:
    """Every embedding profile in `dir`, read once (profiles are versioned, immutable data — same policy as
    resolve_profile). On the invoke path via to_snapshot, so it must not re-glob per call."""
    out = []
    for path in sorted(Path(dir).glob("*.json")):
        doc = json.loads(path.read_text())
        if doc.get("kind") == "embedding":
            out.append(_profile_from_json(doc))
    return tuple(out)


def resolve_embedding_profile_by_model(model: str, *, dir: str | Path = _PROFILE_DIR) -> CapabilityProfile:
    """Find the EMBEDDING profile for a model id across providers (feature 024). A knowledge base names only its model;
    the provider is derived from the profile, never from deployment settings (analyze M3). Fails closed on an unknown or
    chat model. Embedding model ids are unique across providers in profiles_data (asserted, not assumed)."""
    hits = [p for p in _embedding_index(str(dir)) if p.model == model]
    if not hits:
        raise FileNotFoundError(f"no embedding profile for model={model!r}")
    if len(hits) > 1:
        raise FileNotFoundError(f"ambiguous embedding model id {model!r}: {[h.provider for h in hits]}")
    return hits[0]


def embedding_profiles(*, dir: str | Path = _PROFILE_DIR) -> list[CapabilityProfile]:
    """All embedding profiles (for KB validation's `supported_models`)."""
    return list(_embedding_index(str(dir)))
