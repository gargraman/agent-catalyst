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
    monkeypatch.setenv("AC_STORAGE_PROVIDER", "s3")      # prod requires durable upload storage (review 2026-11-15 #9)


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


# ---- feature 024 (M7 RAG): storage + ingestion + embedding settings (tasks.md T010) -------------------------
_INGEST_ENV = ("AC_STORAGE_PROVIDER", "AC_UPLOADS_BUCKET", "AC_INGEST_MAX_FILE_BYTES", "AC_INGEST_CONCURRENCY",
               "AC_INGEST_STALE_AFTER_S", "AC_INGEST_HEARTBEAT_S", "AC_EMBEDDING_PROVIDER", "AC_EMBEDDING_ALLOWED_PROVIDERS")


def _ingest_clean(monkeypatch):
    for k in _INGEST_ENV:
        monkeypatch.delenv(k, raising=False)


def test_ingest_settings_defaults(monkeypatch):
    _ingest_clean(monkeypatch)
    s = Settings()
    assert getattr(s, "storage_provider", None) == "local"
    assert getattr(s, "uploads_bucket", None) == "ac-uploads"
    assert getattr(s, "ingest_max_file_bytes", None) == 20971520          # 20 MiB
    assert getattr(s, "ingest_concurrency", None) == 2
    assert getattr(s, "ingest_stale_after_s", None) == 120
    assert getattr(s, "ingest_heartbeat_s", None) == 15
    assert not hasattr(s, "embedding_provider"), "provider comes from the KB model's profile now (T083, analyze M3)"


def test_ingest_settings_env_overrides(monkeypatch):
    _ingest_clean(monkeypatch)
    monkeypatch.setenv("AC_STORAGE_PROVIDER", "s3")
    monkeypatch.setenv("AC_UPLOADS_BUCKET", "other-bucket")
    monkeypatch.setenv("AC_INGEST_MAX_FILE_BYTES", "1024")
    monkeypatch.setenv("AC_INGEST_CONCURRENCY", "4")
    s = Settings()
    assert (getattr(s, "storage_provider", None), getattr(s, "uploads_bucket", None)) == ("s3", "other-bucket")
    assert (getattr(s, "ingest_max_file_bytes", None), getattr(s, "ingest_concurrency", None)) == (1024, 4)


@pytest.mark.parametrize(
    "env",
    [
        {"AC_STORAGE_PROVIDER": "minio"},                                   # only local | s3
        {"AC_INGEST_MAX_FILE_BYTES": "0"},                                  # must be > 0
        {"AC_INGEST_CONCURRENCY": "0"},                                     # 1..16
        {"AC_INGEST_CONCURRENCY": "17"},
        {"AC_INGEST_STALE_AFTER_S": "29"},                                  # >= 30
        {"AC_INGEST_STALE_AFTER_S": "60", "AC_INGEST_HEARTBEAT_S": "30"},   # heartbeat < stale_after / 2
    ],
)
def test_ingest_settings_invalid_values_fail_closed(monkeypatch, env):
    _ingest_clean(monkeypatch)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(ValidationError):
        Settings()



# ---- decision 2026-11-14 (T083): customer KB content is embedded only on Bedrock in prod -------------------------
def test_embedding_providers_allowed_default_non_prod(monkeypatch):
    _ingest_clean(monkeypatch)
    monkeypatch.setenv("AC_ENV", "dev")
    assert Settings().embedding_providers_allowed == frozenset({"openai", "bedrock"})


def test_embedding_providers_allowed_default_prod_is_bedrock_only(monkeypatch):
    _prod_safe(monkeypatch)
    _ingest_clean(monkeypatch)
    monkeypatch.setenv("AC_ENV", "prod")
    monkeypatch.setenv("AC_STORAGE_PROVIDER", "s3")
    monkeypatch.setenv("AC_TELEMETRY_FINGERPRINT_KEY", "a-real-deployment-key-0123456789")
    assert Settings().embedding_providers_allowed == frozenset({"bedrock"})


def test_embedding_providers_allowed_env_override(monkeypatch):
    _ingest_clean(monkeypatch)
    monkeypatch.setenv("AC_ENV", "dev")
    monkeypatch.setenv("AC_EMBEDDING_ALLOWED_PROVIDERS", "bedrock")
    assert Settings().embedding_providers_allowed == frozenset({"bedrock"})


def test_embedding_providers_allowed_rejects_unknown(monkeypatch):
    _ingest_clean(monkeypatch)
    monkeypatch.setenv("AC_EMBEDDING_ALLOWED_PROVIDERS", "openai,vertex")
    with pytest.raises(ValidationError):
        Settings()


# ---- T080 (H5): orphaned-object sweep grace period -------------------------------------------------------------
def test_storage_orphan_grace_default_and_floor(monkeypatch):
    monkeypatch.delenv("AC_STORAGE_ORPHAN_GRACE_S", raising=False)
    assert getattr(Settings(), "storage_orphan_grace_s", None) == 3600
    monkeypatch.setenv("AC_STORAGE_ORPHAN_GRACE_S", "299")      # below 5 min could delete an in-flight upload's object
    with pytest.raises(ValidationError):
        Settings()


def test_prod_rejects_in_memory_upload_storage(monkeypatch):
    """Code review 2026-11-15 #9: `local` storage lives in process memory — uploads vanish on restart / other
    replicas. Fail closed at load under prod, like the fingerprint key."""
    _prod_safe(monkeypatch)
    monkeypatch.setenv("AC_ENV", "prod")
    monkeypatch.setenv("AC_TELEMETRY_FINGERPRINT_KEY", "a-real-deployment-key-0123456789")
    monkeypatch.setenv("AC_STORAGE_PROVIDER", "local")
    with pytest.raises(ValidationError, match="AC_STORAGE_PROVIDER"):
        Settings()
    monkeypatch.setenv("AC_STORAGE_PROVIDER", "s3")
    assert Settings().storage_provider == "s3"


def test_compose_dev_stack_uses_localstack_s3_for_uploads():
    from pathlib import Path
    compose = (Path(__file__).resolve().parents[2] / "deploy" / "compose.yaml").read_text()
    assert "AC_STORAGE_PROVIDER: s3" in compose


# ---- feature 025 T021 (US2): resilience tunables (contract api §3) -------------------------------------------------
_RES_ENV = {
    "AC_PROVIDER_MAX_ATTEMPTS": ("provider_max_attempts", "max_attempts", 3),
    "AC_PROVIDER_BACKOFF_BASE_S": ("provider_backoff_base_s", "backoff_base_s", 0.5),
    "AC_PROVIDER_WAIT_CAP_S": ("provider_wait_cap_s", "per_wait_cap_s", 8.0),
    "AC_BREAKER_FAILURE_THRESHOLD": ("breaker_failure_threshold", "failure_threshold", 5),
    "AC_BREAKER_COOLDOWN_S": ("breaker_cooldown_s", "cooldown_s", 30.0),
    "AC_BATCH_CONCURRENCY": ("batch_concurrency", "batch_concurrency", 4),
}


def test_resilience_settings_defaults(monkeypatch):
    for env in _RES_ENV:
        monkeypatch.delenv(env, raising=False)
    s = Settings()
    for env, (field, _, default) in _RES_ENV.items():
        assert getattr(s, field) == default, env


def test_resilience_settings_map_onto_policy(monkeypatch):
    from api.resilience_wiring import policy_from_settings
    values = {"AC_PROVIDER_MAX_ATTEMPTS": "2", "AC_PROVIDER_BACKOFF_BASE_S": "0.25", "AC_PROVIDER_WAIT_CAP_S": "4",
              "AC_BREAKER_FAILURE_THRESHOLD": "7", "AC_BREAKER_COOLDOWN_S": "12.5", "AC_BATCH_CONCURRENCY": "2"}
    for k, v in values.items():
        monkeypatch.setenv(k, v)
    pol = policy_from_settings(Settings())
    assert (pol.max_attempts, pol.backoff_base_s, pol.per_wait_cap_s, pol.failure_threshold, pol.cooldown_s,
            pol.batch_concurrency) == (2, 0.25, 4.0, 7, 12.5, 2)


@pytest.mark.parametrize("env", sorted(_RES_ENV))
@pytest.mark.parametrize("bad", ["0", "-1"])
def test_resilience_settings_reject_non_positive(monkeypatch, env, bad):
    monkeypatch.setenv(env, bad)
    with pytest.raises(ValidationError):
        Settings()
