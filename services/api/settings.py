"""Typed, env-driven settings (0d) — the single source for connection config.

Centralizes the three DB URLs `db.py` used to read ad hoc from `os.environ`, plus typed (optional)
slots for Redis/Vault/provider custody that later phases wire. Dev defaults mirror `deploy/compose.yaml`
and are **dev-only** — production injects real values via the environment (provider keys/secrets come
from Vault per 09, never from these defaults).

Driver correctness is enforced fail-closed: the request/job path and migrations must use the right
SQLAlchemy driver (asyncpg vs psycopg), so a mismatched URL is rejected at load time, not deep in a
request (09 §7 / 00L §4).
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_ASYNC = "postgresql+asyncpg://ac:ac_dev_pw@localhost:5433/agent_catalyst"
_DEV_SYNC = "postgresql+psycopg://ac:ac_dev_pw@localhost:5433/agent_catalyst"
_DEV_APP = "postgresql+asyncpg://ac_app:ac_app_pw@localhost:5433/agent_catalyst"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", populate_by_name=True, extra="ignore")

    # Superuser/owner (migrations + tooling). BYPASSES RLS — never on the request path.
    database_url: str = Field(default=_DEV_ASYNC, alias="AC_DATABASE_URL")
    # Sync driver, Alembic only (out-of-band migrations).
    database_sync_url: str = Field(default=_DEV_SYNC, alias="AC_DATABASE_SYNC_URL")
    # Non-superuser app role — the request/job path where RLS applies.
    app_database_url: str = Field(default=_DEV_APP, alias="AC_APP_DATABASE_URL")

    # Optional slots consumed by later phases (worker, custody) — unset until wired.
    redis_url: str | None = Field(default=None, alias="AC_REDIS_URL")
    vault_addr: str | None = Field(default=None, alias="AC_VAULT_ADDR")

    # --- Auth / IdP (feature 012) ---------------------------------------------------------------
    # Deployment profile. Defaults to `prod` so an UNCONFIGURED process is fail-closed: the unverified
    # dev auth provider is only usable when `AC_ENV` is explicitly a non-prod value (FR-008). Local dev and
    # the test harness set `AC_ENV=dev` deliberately (tests via conftest). (code-review hardening, 2026-11-09)
    env: str = Field(default="prod", alias="AC_ENV")
    # Active IdP provider id resolved by api.auth.registry: "dev" | "trellix-iam".
    auth_provider: str = Field(default="dev", alias="AC_AUTH_PROVIDER")
    # Trellix-IAM provider config (public metadata — NOT secrets; verified live 2026-11-08).
    iam_issuer: str = Field(default="https://preprod.iam.cloud.trellix.com/iam/v1.0", alias="AC_IAM_ISSUER")
    iam_audience: str = Field(default="mcafee", alias="AC_IAM_AUDIENCE")
    iam_jwks_uri: str = Field(default="https://preprod.iam.cloud.trellix.com/iam/v1.0/jwks", alias="AC_IAM_JWKS_URI")
    iam_allowed_algs: str = Field(default="RS256", alias="AC_IAM_ALGS")   # csv; token-header alg never trusted
    iam_clock_skew_s: int = Field(default=60, alias="AC_IAM_CLOCK_SKEW_S")

    @field_validator("iam_clock_skew_s")
    @classmethod
    def _skew_bounded(cls, v: int) -> int:
        if not 0 <= v <= 120:                                 # bounded tolerance (FR-003); no unbounded skew
            raise ValueError("AC_IAM_CLOCK_SKEW_S must be between 0 and 120 seconds")
        return v

    @field_validator("database_url", "app_database_url")
    @classmethod
    def _must_be_async(cls, v: str) -> str:
        if "+asyncpg" not in v:
            raise ValueError("async DB URL must use the asyncpg driver (postgresql+asyncpg://)")
        return v

    @field_validator("database_sync_url")
    @classmethod
    def _must_be_sync(cls, v: str) -> str:
        if "+psycopg" not in v:
            raise ValueError("sync DB URL must use the psycopg driver (postgresql+psycopg://)")
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide singleton. Call ``get_settings.cache_clear()`` in tests that mutate the env."""
    return Settings()
