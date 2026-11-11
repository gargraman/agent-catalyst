"""Unit tests for the pluggable secrets seam (feature 014, T004).

Covers the in-process LocalProvider contract, the config-selected registry, and the masking helper.
No DB and no network — these run in the pure unit lane.
"""
from __future__ import annotations

import pytest

from api.provider_keys import mask_secret
from api.secrets import SecretNotFound, build_secrets_provider
from api.secrets.local_provider import LocalProvider


async def test_local_provider_put_get_rotate_disable_roundtrip():
    p = LocalProvider()
    ref = await p.put("ws-1", "openai", "sk-secret-value-123")
    assert ref.startswith("local://ws-1/openai/")
    assert await p.get(ref) == "sk-secret-value-123"

    same = await p.rotate(ref, "sk-rotated-value-456")
    assert same == ref  # identity preserved
    assert await p.get(ref) == "sk-rotated-value-456"

    await p.disable(ref)
    with pytest.raises(SecretNotFound):
        await p.get(ref)


async def test_local_provider_get_missing_raises_secret_not_found():
    p = LocalProvider()
    with pytest.raises(SecretNotFound):
        await p.get("local://nope/none/deadbeef")


async def test_local_provider_disable_is_idempotent():
    p = LocalProvider()
    ref = await p.put("ws-1", "openai", "v")
    await p.disable(ref)
    await p.disable(ref)  # second disable must not raise


def test_registry_selects_local_by_default(monkeypatch):
    # Default config (conftest sets AC_ENV=dev, no AC_SECRETS_PROVIDER) → local provider.
    monkeypatch.delenv("AC_SECRETS_PROVIDER", raising=False)
    build_secrets_provider.cache_clear()
    from api.settings import get_settings

    get_settings.cache_clear()
    provider = build_secrets_provider()
    assert provider.id == "local"
    build_secrets_provider.cache_clear()
    get_settings.cache_clear()


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", ""),
        ("   ", ""),
        ("short", "•••••"),                       # len 5 ≤ 10 → full bullets
        ("exactlyten", "••••••••••"),             # len 10 ≤ 10 → full bullets
        ("sk-proj-ABCDEFGHIJKL", "sk-pro••••••••IJKL"),  # >10 → 6 head + 8 bullets + 4 tail
        ("  sk-proj-ABCDEFGHIJKL  ", "sk-pro••••••••IJKL"),  # trimmed first
    ],
)
def test_mask_secret_mirrors_frontend(raw, expected):
    assert mask_secret(raw) == expected
