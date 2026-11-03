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
