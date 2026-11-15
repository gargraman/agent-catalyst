"""Embedding-model policy for knowledge bases (feature 024; research D3; decision 2026-11-14 in docs/scope.md).

A knowledge base names an embedding MODEL. It is accepted only if (1) it has an embedding capability profile, (2) the
profile's dimension equals the single deployment dimension (`vector(1024)`), and (3) the profile's provider is allowed
in this deployment (`AC_EMBEDDING_ALLOWED_PROVIDERS`; prod default = bedrock only — customer content is embedded only
on Bedrock). The KB's provider is DERIVED from the profile, never from settings (analyze M3).
"""
from __future__ import annotations

from api.errors import InputInvalid
from api.settings import get_settings
from runtime.providers.profiles import CapabilityProfile, embedding_profiles, resolve_embedding_profile_by_model

DEPLOYMENT_DIMENSION = 1024           # must equal the kb_chunks.embedding column type (migration 0013)


def supported_models() -> list[str]:
    allowed = get_settings().embedding_providers_allowed
    return sorted(p.model for p in embedding_profiles()
                  if p.dimension == DEPLOYMENT_DIMENSION and p.provider in allowed)


def validate_embedding_model(model: str, *, embedding_dim: int | None = None) -> CapabilityProfile:
    """Return the profile for an acceptable model, else 400 input.invalid listing the supported models."""
    try:
        profile = resolve_embedding_profile_by_model(model)
    except FileNotFoundError:
        profile = None
    if (profile is None or profile.dimension != DEPLOYMENT_DIMENSION
            or profile.provider not in get_settings().embedding_providers_allowed):
        raise InputInvalid("embedding_model is not a supported embedding model in this deployment",
                           detail={"field": "embedding_model", "supported_models": supported_models()})
    if embedding_dim is not None and embedding_dim != profile.dimension:
        raise InputInvalid("embedding_dim must match the embedding model's dimension",
                           detail={"field": "embedding_dim", "expected": profile.dimension})
    return profile


def provider_for(model: str) -> str | None:
    """The KB's derived provider (disclosed in KB reads); None for a legacy row whose model has no profile."""
    try:
        return resolve_embedding_profile_by_model(model).provider
    except FileNotFoundError:
        return None
