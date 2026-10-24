"""Capability-profile loader — resolution + the missing-profile failure mode (loader.py:44-52)."""

import pytest

from profiles.loader import resolve_profile


def test_resolve_known_profile_maps_pricing_and_features():
    p = resolve_profile("openai", "gpt-5-mini")
    assert p.provider == "openai" and p.model == "gpt-5-mini"
    assert p.pricing["input"] == 0.6 and p.pricing["output"] == 2.4   # pricing_usd_per_mtok → .pricing
    assert p.feature("structured_output") is True


def test_missing_model_raises_file_not_found():
    with pytest.raises(FileNotFoundError):
        resolve_profile("openai", "gpt-does-not-exist")


def test_missing_provider_raises_file_not_found():
    with pytest.raises(FileNotFoundError):
        resolve_profile("no-such-provider", "gpt-5-mini")


def test_empty_dir_raises_file_not_found(tmp_path):
    # no *.json at all → the loop finds nothing and the explicit raise fires
    with pytest.raises(FileNotFoundError):
        resolve_profile("openai", "gpt-5-mini", dir=tmp_path)
