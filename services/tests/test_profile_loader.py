"""M1 T003 (RED) — resolve_profile loads versioned capability+pricing profiles from profiles_data/
and fails closed on an unknown (provider, model).

This is a GENUINE BEHAVIORAL RED, not an import/env failure: resolve_profile is importable but currently
raises NotImplementedError, so every assertion below about real load behavior fails until T004 implements
the loader. Pins: content-match (not filename) so model ids with ':' resolve; api_version advisory
(provider+model is the key — analyze A1); fail-closed on unknown (FR-008, no guessed model/pricing).
"""
from __future__ import annotations

import pytest

from runtime.providers.profiles import CapabilityProfile, resolve_profile


def test_resolve_openai_gpt5_mini_profile():
    p = resolve_profile("openai", "gpt-5-mini")
    assert isinstance(p, CapabilityProfile)
    assert p.provider == "openai" and p.model == "gpt-5-mini"
    assert p.pricing["input"] == 0.6          # mapped from JSON pricing_usd_per_mtok.input
    assert p.pricing["output"] == 2.4
    assert p.feature("structured_output") is True
    assert p.availability == "active"
    assert p.provisional is True              # seeded pricing unreconciled until paid H2 (FR-010)


def test_resolve_reads_provisional_flag():
    # ollama local profiles are $0/exact → not provisional; openai seeds are provisional until reconciled
    assert resolve_profile("ollama", "llama3.1:8b").provisional is False
    assert resolve_profile("openai", "gpt-5").provisional is True


def test_resolve_matches_on_content_not_filename():
    # the ollama model id contains ':' (not filesystem-safe in the filename) — content match must still find it
    p = resolve_profile("ollama", "llama3.1:8b")
    assert p.provider == "ollama" and p.model == "llama3.1:8b"


def test_resolve_accepts_optional_api_version_advisory():
    # api_version is advisory: provider+model is the key; passing the profile's own version still resolves
    p = resolve_profile("openai", "gpt-5-mini", "2026-01")
    assert p.model == "gpt-5-mini"


def test_resolve_unknown_model_fails_closed():
    # FR-008: no guessed model/pricing — a clear, caller-visible error, not a silent default
    with pytest.raises((FileNotFoundError, LookupError, ValueError)):
        resolve_profile("openai", "does-not-exist")
