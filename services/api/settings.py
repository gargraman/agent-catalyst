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
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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

    # Optional slots consumed by later phases (worker) — unset until wired.
    redis_url: str | None = Field(default=None, alias="AC_REDIS_URL")

    # --- Rate limiting (feature 016 / F5) --------------------------------------------------------
    # Master switch. When off (or AC_REDIS_URL unset), the limiter no-op-allows — parity with the pre-016
    # seam (spec Assumptions). Security controls (deny-set/spend, 018/021) do NOT key off this flag.
    rate_limit_enabled: bool = Field(default=True, alias="AC_RATE_LIMIT_ENABLED")
    # Reviewable per-surface limits table: route_class → (window_s, limit), conservative defaults tunable
    # against pilot traffic (FR-003; the testable gate is "limits exist and fire before expensive work", not a
    # specific number). `default` is the fallback for any route_class not listed. Keys mirror the route-class
    # mapping wired in deps/routers: invoke | playground | eval | ingest | default.
    rate_limits: dict[str, tuple[int, int]] = Field(
        default_factory=lambda: {
            "invoke":     (60, 120),   # published invoke: 120 / 60s per principal
            "playground": (60, 60),    # interactive console runs
            "eval":       (60, 20),    # eval batch kick-offs (expensive)
            "ingest":     (60, 20),    # knowledge source add/reingest (expensive)
            "default":    (60, 300),   # all other CRUD reads/writes
        }
    )

    def rate_limit_for(self, route_class: str) -> tuple[int, int]:
        """(window_s, limit) for a route_class, falling back to the `default` bucket (FR-003)."""
        return self.rate_limits.get(route_class) or self.rate_limits["default"]

    # --- Egress / SSRF guard (feature 017 / F19) -------------------------------------------------
    # The baseline block-set (private/loopback/link-local/ULA/multicast/reserved/cloud-metadata, IPv4+IPv6)
    # is ALWAYS on and cannot be disabled — it is a security control, so (like the deny-set/spend controls,
    # not the availability-oriented limiter) it does NOT key off any enable flag and fails closed (P3/FR-027).
    # This slot is ADDITIVE-ONLY: extra operator-defined CIDRs to deny on top of the baseline (e.g. a corp
    # supernet). Malformed entries are rejected at load (fail-closed config), never deep in a request.
    # NoDecode: take the env value as a raw CSV string (not JSON) so `_split_cidrs` parses it — see below.
    egress_extra_deny_cidrs: Annotated[list[str], NoDecode] = Field(
        default_factory=list, alias="AC_EGRESS_EXTRA_DENY_CIDRS")

    @field_validator("egress_extra_deny_cidrs", mode="before")
    @classmethod
    def _split_cidrs(cls, v):
        # Accept a CSV string (env) or a list (code); normalize to a list of trimmed, non-empty entries.
        if isinstance(v, str):
            return [p.strip() for p in v.split(",") if p.strip()]
        return v

    @field_validator("egress_extra_deny_cidrs")
    @classmethod
    def _valid_cidrs(cls, v: list[str]) -> list[str]:
        import ipaddress
        for c in v:
            try:
                ipaddress.ip_network(c, strict=False)
            except ValueError as exc:
                raise ValueError(f"AC_EGRESS_EXTRA_DENY_CIDRS entry is not a valid CIDR/IP: {c!r}") from exc
        return v

    # --- Caller-token deny-set (feature 018) ----------------------------------------------------
    # Immediate-revocation deny-set TTL: must be ≥ the max access-token/session lifetime (≥ 1h) so a revoked
    # subject can't outlive the set (FR-014). The deny-set is a SECURITY control — unlike the 016 limiter it
    # fails CLOSED on a configured-Redis outage (FR-024b); it does NOT key off rate_limit_enabled.
    denyset_ttl_s: int = Field(default=3600, alias="AC_DENYSET_TTL_S")
    # In-flight streaming invoke re-checks the deny-set at this cadence so a mid-stream revoke is cut (FR-012).
    # Per-frame checks would be one Redis round-trip per token-delta (too chatty); the worst-case leak window is
    # this interval. 0 disables the mid-stream re-check (initial-gate only).
    denyset_stream_recheck_s: float = Field(default=2.0, alias="AC_DENYSET_STREAM_RECHECK_S")

    @field_validator("denyset_ttl_s")
    @classmethod
    def _denyset_ttl_floor(cls, v: int) -> int:
        if v < 3600:                                          # FR-014: ≥ max token/session lifetime (1h)
            raise ValueError("AC_DENYSET_TTL_S must be ≥ 3600 (deny-set must outlive the max token/session TTL)")
        return v

    # Max caller-token lifetime (hardening). Caller tokens are long-lived SERVICE credentials, NOT human sessions,
    # so this is deliberately decoupled from `denyset_ttl_s` (the 1h immediate-cut window): capping tokens to the
    # deny-set TTL would make them useless. Revocation is durable (an UPDATE to `revoked_at` on both revoke paths),
    # so the deny-set no longer needs to outlive the token — this bound is pure hygiene: no eternal credential, and
    # a mint beyond it is rejected at the door (400). Default 90d. A mint with no expiry defaults to now + this.
    max_token_lifetime_s: int = Field(default=7776000, alias="AC_MAX_TOKEN_LIFETIME_S")

    @field_validator("max_token_lifetime_s")
    @classmethod
    def _max_token_lifetime_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("AC_MAX_TOKEN_LIFETIME_S must be a positive number of seconds")
        return v

    # --- Secret custody (feature 014) -----------------------------------------------------------
    # Pluggable secrets backend selector, resolved by api.secrets.registry: "local" | "aws".
    # `local` = in-process (unit tests + offline dev); `aws` = AWS Secrets Manager (boto3), pointed at
    # LocalStack locally via AC_AWS_ENDPOINT_URL and at real AWS in prod. Raw provider keys live ONLY in the
    # backend; the app DB holds masked value + opaque vault_path (09, feature 014). Replaces the HashiCorp
    # Vault slot (Vault→AWS reversal, see docs/scope.md).
    secrets_provider: str = Field(default="local", alias="AC_SECRETS_PROVIDER")
    aws_endpoint_url: str | None = Field(default=None, alias="AC_AWS_ENDPOINT_URL")  # LocalStack URL locally; unset → real AWS
    aws_region: str = Field(default="us-east-1", alias="AC_AWS_REGION")
    secrets_prefix: str = Field(default="ac", alias="AC_SECRETS_PREFIX")  # secret-name namespace root

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
