"""Feature 012 (M5) US2 — provider selection + config safety (contract §5 items 12–13; FR-006/FR-008).

Pure unit tests over get_active_provider: config-driven selection, the production gate that disables the
unverified dev provider, and fail-closed handling of an unknown provider — no call-site changes anywhere.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from api.auth.dev_provider import DevHeaderProvider
from api.auth.registry import AuthConfigError, get_active_provider


def _settings(**over):
    base = dict(auth_provider="dev", env="dev", iam_issuer="https://test/iam", iam_audience="mcafee",
                iam_jwks_uri="https://test/iam/jwks", iam_allowed_algs="RS256", iam_clock_skew_s=60)
    base.update(over)
    return SimpleNamespace(**base)


def test_dev_provider_selected_in_dev_profile():
    assert isinstance(get_active_provider(_settings(auth_provider="dev", env="dev")), DevHeaderProvider)


def test_dev_provider_disabled_under_production_profile():
    with pytest.raises(AuthConfigError):
        get_active_provider(_settings(auth_provider="dev", env="prod"))     # FR-008 fail-closed


def test_unknown_provider_fails_closed():
    with pytest.raises(AuthConfigError):
        get_active_provider(_settings(auth_provider="okta"))


def test_trellix_provider_selected_by_config():
    p = get_active_provider(_settings(auth_provider="trellix-iam"))
    assert p.id == "trellix-iam"                                           # same contract, different provider


def test_swap_changes_provider_without_touching_call_sites():
    # Selection is purely a function of config; flipping auth_provider yields a different provider instance
    # with no code change at any call site (SC-003).
    dev = get_active_provider(_settings(auth_provider="dev"))
    iam = get_active_provider(_settings(auth_provider="trellix-iam"))
    assert dev.id == "dev" and iam.id == "trellix-iam"


def test_config_error_message_has_no_secret():
    try:
        get_active_provider(_settings(auth_provider="totally-unknown"))
        assert False, "expected AuthConfigError"
    except AuthConfigError as e:
        assert "jwks" not in str(e).lower() and "secret" not in str(e).lower()


def test_auth_config_error_renders_as_clean_refusal():
    # code-review #2: a misconfigured/disallowed provider surfaces via the error envelope (503), not an
    # unhandled 500. AuthConfigError must be an AppError the handler catches.
    from api.errors import AppError
    assert issubclass(AuthConfigError, AppError)
    err = AuthConfigError("dev auth provider is disabled under a production profile (AC_ENV=prod)")
    assert err.http_status == 503 and err.code == "auth.unavailable"
    assert "secret" not in err.message.lower()


def test_default_env_is_fail_closed():
    # code-review #1: an UNCONFIGURED process must default to a profile that disables the dev provider.
    from api.settings import Settings
    assert Settings.model_fields["env"].default == "prod"
