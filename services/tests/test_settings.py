"""Settings layer (0d): typed, env-driven config with fail-closed driver validation.

The three DB URLs are load-bearing for RLS (09 §7): the request/job path MUST use an async driver as
the non-superuser app role; Alembic MUST use the sync driver. A URL pointed at the wrong driver is a
silent misconfiguration that breaks the async engine or RLS — so the settings layer rejects it at load
time (fail-closed) rather than failing deep in a request.
"""
import importlib

import pytest
from pydantic import ValidationError

from api import settings as settings_mod
from api.settings import Settings, get_settings

# Env var names the existing db.py already honors — settings must stay drop-in compatible.
_ENV = ("AC_DATABASE_URL", "AC_DATABASE_SYNC_URL", "AC_APP_DATABASE_URL")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in _ENV + ("AC_REDIS_URL",):
        monkeypatch.delenv(k, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_defaults_are_dev_urls_with_correct_drivers():
    s = Settings()
    assert s.database_url.startswith("postgresql+asyncpg://")
    assert s.database_sync_url.startswith("postgresql+psycopg://")
    assert s.app_database_url.startswith("postgresql+asyncpg://ac_app")  # app path = non-superuser role
    assert s.redis_url is None  # optional slot, not wired yet


def test_env_overrides_are_read(monkeypatch):
    monkeypatch.setenv("AC_APP_DATABASE_URL", "postgresql+asyncpg://ac_app:x@db:5432/prod")
    s = Settings()
    assert s.app_database_url == "postgresql+asyncpg://ac_app:x@db:5432/prod"


def test_async_slot_rejects_sync_driver_fail_closed(monkeypatch):
    # A psycopg (sync) URL in the async app slot must be rejected, not silently accepted.
    monkeypatch.setenv("AC_APP_DATABASE_URL", "postgresql+psycopg://ac_app:x@db:5432/prod")
    with pytest.raises(ValidationError):
        Settings()


def test_sync_slot_rejects_async_driver_fail_closed(monkeypatch):
    monkeypatch.setenv("AC_DATABASE_SYNC_URL", "postgresql+asyncpg://ac:x@db:5432/prod")
    with pytest.raises(ValidationError):
        Settings()


def test_get_settings_is_a_cached_singleton():
    assert get_settings() is get_settings()


def test_db_module_sources_urls_from_settings(monkeypatch):
    # db.py must not re-read os.environ independently; it reflects the settings layer (single source).
    monkeypatch.setenv("AC_APP_DATABASE_URL", "postgresql+asyncpg://ac_app:x@db:5432/other")
    get_settings.cache_clear()
    import api.db as db
    importlib.reload(db)
    assert db.APP_ASYNC_URL == "postgresql+asyncpg://ac_app:x@db:5432/other"
    # restore module state for other tests
    monkeypatch.delenv("AC_APP_DATABASE_URL", raising=False)
    get_settings.cache_clear()
    importlib.reload(db)


# ---- Feature 023 (T009): telemetry fingerprint key + dev content-logging override ----------------------------
_TELEMETRY_ENV = ("AC_ENV", "AC_TELEMETRY_FINGERPRINT_KEY", "AC_TELEMETRY_LOG_CONTENT_IN_DEV")


def _prod_safe(monkeypatch):
    for k in _TELEMETRY_ENV:
        monkeypatch.delenv(k, raising=False)


def test_prod_without_fingerprint_key_fails_closed(monkeypatch):
    _prod_safe(monkeypatch)
    monkeypatch.setenv("AC_ENV", "prod")
    with pytest.raises(ValidationError, match="AC_TELEMETRY_FINGERPRINT_KEY"):
        Settings()


def test_prod_with_dev_default_fingerprint_key_fails_closed(monkeypatch):
    _prod_safe(monkeypatch)
    monkeypatch.setenv("AC_ENV", "dev")
    dev_default = Settings().telemetry_fingerprint_key.get_secret_value()
    monkeypatch.setenv("AC_ENV", "prod")
    monkeypatch.setenv("AC_TELEMETRY_FINGERPRINT_KEY", dev_default)
    with pytest.raises(ValidationError, match="AC_TELEMETRY_FINGERPRINT_KEY"):
        Settings()


def test_prod_with_real_fingerprint_key_loads(monkeypatch):
    _prod_safe(monkeypatch)
    monkeypatch.setenv("AC_ENV", "prod")
    monkeypatch.setenv("AC_TELEMETRY_FINGERPRINT_KEY", "a-real-deployment-key-0123456789")
    s = Settings()
    assert s.telemetry_fingerprint_key.get_secret_value() == "a-real-deployment-key-0123456789"
    assert "a-real-deployment-key" not in repr(s)           # SecretStr: never echoed


def test_non_prod_gets_a_fixed_dev_default_key(monkeypatch):
    _prod_safe(monkeypatch)
    monkeypatch.setenv("AC_ENV", "dev")
    a, b = Settings(), Settings()
    assert a.telemetry_fingerprint_key.get_secret_value()
    assert a.telemetry_fingerprint_key.get_secret_value() == b.telemetry_fingerprint_key.get_secret_value()


def test_log_content_in_dev_default_true_and_ignored_in_prod(monkeypatch):
    _prod_safe(monkeypatch)
    monkeypatch.setenv("AC_ENV", "dev")
    assert Settings().telemetry_log_content_in_dev is True
    assert Settings().telemetry_content_override is True
    monkeypatch.setenv("AC_TELEMETRY_LOG_CONTENT_IN_DEV", "false")
    assert Settings().telemetry_content_override is False
    monkeypatch.setenv("AC_ENV", "prod")
    monkeypatch.setenv("AC_TELEMETRY_LOG_CONTENT_IN_DEV", "true")
    monkeypatch.setenv("AC_TELEMETRY_FINGERPRINT_KEY", "a-real-deployment-key-0123456789")
    assert Settings().telemetry_content_override is False       # never an override under prod


@pytest.mark.parametrize("env", ["staging", "qa", "preprod", "test"])
def test_content_override_only_in_exact_dev_profile(monkeypatch, env):
    """023 analysis S1: a shared non-prod env (staging) may hold real data — it must behave like prod for content
    logging. Only AC_ENV == "dev" may force text into call-log lines."""
    _prod_safe(monkeypatch)
    monkeypatch.setenv("AC_ENV", env)
    monkeypatch.setenv("AC_TELEMETRY_LOG_CONTENT_IN_DEV", "true")
    assert Settings().telemetry_content_override is False
