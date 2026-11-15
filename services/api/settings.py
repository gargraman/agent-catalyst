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
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_DEV_ASYNC = "postgresql+asyncpg://ac:ac_dev_pw@localhost:5433/agent_catalyst"
_DEV_SYNC = "postgresql+psycopg://ac:ac_dev_pw@localhost:5433/agent_catalyst"
_DEV_APP = "postgresql+asyncpg://ac_app:ac_app_pw@localhost:5433/agent_catalyst"
# Dev-only fingerprint root key (feature 023). Public by design — rejected under AC_ENV=prod.
_DEV_FINGERPRINT_KEY = "ac-dev-telemetry-fingerprint-key-not-secret"
# Providers that have embedding profiles (feature 024). Extend together with runtime/providers/profiles_data.
_EMBEDDING_PROVIDERS = frozenset({"openai", "bedrock"})


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

    # --- Document storage + ingestion (feature 024 / M7, research D5/D6) --------------------------
    # Uploaded files: `local` = in-process memory (unit/dev only — lost on restart), `s3` = AWS S3 / LocalStack S3
    # (never MinIO, feature 022). Reuses the AWS endpoint/region above.
    storage_provider: Literal["local", "s3"] = Field(default="local", alias="AC_STORAGE_PROVIDER")
    uploads_bucket: str = Field(default="ac-uploads", alias="AC_UPLOADS_BUCKET")
    ingest_max_file_bytes: int = Field(default=20 * 1024 * 1024, gt=0, alias="AC_INGEST_MAX_FILE_BYTES")  # 20 MiB
    ingest_concurrency: int = Field(default=2, ge=1, le=16, alias="AC_INGEST_CONCURRENCY")
    # Heartbeat lease (research D5): a job whose heartbeat is older than stale_after is reaped as interrupted.
    ingest_stale_after_s: int = Field(default=120, ge=30, alias="AC_INGEST_STALE_AFTER_S")
    ingest_heartbeat_s: int = Field(default=15, ge=1, alias="AC_INGEST_HEARTBEAT_S")
    # Provider resilience (feature 025, contract api §3): retry / breaker / batch bulkhead. Every value must be > 0 —
    # bad config fails settings load (startup), never the first request. max_attempts=1 is the retry kill switch.
    provider_max_attempts: int = Field(default=3, gt=0, alias="AC_PROVIDER_MAX_ATTEMPTS")
    provider_backoff_base_s: float = Field(default=0.5, gt=0, alias="AC_PROVIDER_BACKOFF_BASE_S")
    provider_wait_cap_s: float = Field(default=8.0, gt=0, alias="AC_PROVIDER_WAIT_CAP_S")
    breaker_failure_threshold: int = Field(default=5, gt=0, alias="AC_BREAKER_FAILURE_THRESHOLD")
    breaker_cooldown_s: float = Field(default=30.0, gt=0, alias="AC_BREAKER_COOLDOWN_S")
    batch_concurrency: int = Field(default=4, gt=0, alias="AC_BATCH_CONCURRENCY")
    # Orphaned stored-object sweep (analyze H5): only objects older than this are eligible — an upload's object is
    # written just before its row commits, so a short grace could delete a live in-flight upload.
    storage_orphan_grace_s: int = Field(default=3600, ge=300, alias="AC_STORAGE_ORPHAN_GRACE_S")
    # Embedding-provider policy (decision 2026-11-14, docs/scope.md): customer KB content is embedded ONLY on Bedrock in
    # prod; OpenAI direct is dev-only. Enforced at KB create/patch. Unset → prod: {bedrock}; any other env: {openai, bedrock}.
    # The KB's provider itself is derived from its model's profile, never from here (analyze M3).
    embedding_allowed_providers: str | None = Field(default=None, alias="AC_EMBEDDING_ALLOWED_PROVIDERS")

    @field_validator("embedding_allowed_providers")
    @classmethod
    def _known_embedding_providers(cls, v: str | None) -> str | None:
        if v is None:
            return v
        names = {p.strip() for p in v.split(",") if p.strip()}
        unknown = names - _EMBEDDING_PROVIDERS
        if not names or unknown:
            raise ValueError(f"AC_EMBEDDING_ALLOWED_PROVIDERS has unknown/empty providers: {sorted(unknown) or v!r}")
        return v

    @property
    def embedding_providers_allowed(self) -> frozenset[str]:
        if self.embedding_allowed_providers is not None:
            return frozenset(p.strip() for p in self.embedding_allowed_providers.split(",") if p.strip())
        return frozenset({"bedrock"}) if self.env == "prod" else frozenset({"openai", "bedrock"})

    @model_validator(mode="after")
    def _heartbeat_well_inside_lease(self) -> "Settings":
        # At least two heartbeats must fit in the lease, or a live job on a slow tick could be reaped.
        if self.ingest_heartbeat_s >= self.ingest_stale_after_s / 2:
            raise ValueError("AC_INGEST_HEARTBEAT_S must be < AC_INGEST_STALE_AFTER_S / 2")
        return self

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

    # --- Console login + session + CSRF (feature 019) -------------------------------------------
    # The session is a STATELESS signed cookie (HS256 JWT via pyjwt), not a server-side store (R4 rejects a
    # store — it widens the Redis SPOF; revocation is the 018 deny-set, already wired into session_principal).
    # AC_SESSION_SECRET signs/verifies the cookie. Optional at load so the dev/test header path (no cookie) and
    # the infra-free unit run work unchanged; it is REQUIRED only to mint/read a real session (the codec raises
    # if a login flow runs without it). Never logged/echoed (FR-028, P5). No insecure baked default under prod.
    session_secret: str | None = Field(default=None, alias="AC_SESSION_SECRET")
    # Session lifetime, HARD-CAPPED at 1h (FR-029): a misconfigured larger value is clamped, never honored, so a
    # session can't outlive the deny-set window. The codec caps again (defense in depth).
    session_ttl_s: int = Field(default=3600, alias="AC_SESSION_TTL_S")
    # RESERVED knob for a future OIDC discovery-document fetch. NOT yet consumed: `oidc_login.config_from_settings`
    # always derives the authorize/token/jwks endpoints from the 012 `iam_issuer`/`iam_jwks_uri` trust config. Set
    # it now and it has no effect until discovery is implemented. Interactive login is gated on the OIDC CLIENT
    # fields below (client_id/secret/redirect_uri), not on this — the dev profile leaves those unset (D4).
    oidc_discovery_url: str | None = Field(default=None, alias="AC_OIDC_DISCOVERY_URL")
    oidc_client_id: str | None = Field(default=None, alias="AC_OIDC_CLIENT_ID")
    oidc_client_secret: str | None = Field(default=None, alias="AC_OIDC_CLIENT_SECRET")
    oidc_redirect_uri: str | None = Field(default=None, alias="AC_OIDC_REDIRECT_URI")
    # Post-login landing path in the console (relative — never an open redirect).
    oidc_post_login_path: str = Field(default="/", alias="AC_OIDC_POST_LOGIN_PATH")
    # Session/CSRF cookie attributes. Secure is ON by default (HTTPS-only); relaxable ONLY under a non-prod
    # profile for local http dev. HttpOnly (session) + SameSite=Lax are pinned in the codec, not configurable.
    session_cookie_secure: bool = Field(default=True, alias="AC_SESSION_COOKIE_SECURE")

    @field_validator("session_ttl_s")
    @classmethod
    def _session_ttl_capped(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("AC_SESSION_TTL_S must be a positive number of seconds")
        return min(v, 3600)                                   # FR-029: hard cap at 1h (clamp, never reject-to-extend)

    @model_validator(mode="after")
    def _secure_cookie_required_in_prod(self) -> "Settings":
        # The "Secure is relaxable ONLY under a non-prod profile" invariant must be ENFORCED, not just documented
        # (mirrors the dev-auth-provider guard in api.auth.registry). Otherwise AC_SESSION_COOKIE_SECURE=false
        # under prod would ship session/csrf cookies over plaintext HTTP. Fail closed at load, not deep in a flow.
        if self.env == "prod" and not self.session_cookie_secure:
            raise ValueError("AC_SESSION_COOKIE_SECURE cannot be disabled under a production profile (AC_ENV=prod)")
        return self

    # --- Per-call telemetry (feature 023) --------------------------------------------------------
    # Root key for the keyed per-workspace content fingerprints on call-log lines (research D4). Sensitive: never
    # logged or returned. Non-prod gets a fixed public dev default; prod MUST inject a real value (fail closed at
    # load — a public key would make fingerprints guessable for short values, defeating FR-002c).
    telemetry_fingerprint_key: SecretStr = Field(
        default=SecretStr(_DEV_FINGERPRINT_KEY), alias="AC_TELEMETRY_FINGERPRINT_KEY")
    # Dev-profile override: redacted text on call-log lines for every workspace (D6). Ignored under prod.
    telemetry_log_content_in_dev: bool = Field(default=True, alias="AC_TELEMETRY_LOG_CONTENT_IN_DEV")

    @property
    def telemetry_content_override(self) -> bool:
        """True when the deployment profile forces content logging on. EXACTLY `dev` only (023 analysis S1): a shared
        non-prod env such as staging may hold real data, so it behaves like prod and needs the audited opt-in."""
        return self.env == "dev" and self.telemetry_log_content_in_dev

    @model_validator(mode="after")
    def _durable_storage_required_in_prod(self) -> "Settings":
        # `local` storage is process memory: uploads vanish on restart and are invisible to other replicas.
        if self.env == "prod" and self.storage_provider != "s3":
            raise ValueError("AC_STORAGE_PROVIDER must be 's3' under AC_ENV=prod (local storage is not durable)")
        return self

    @model_validator(mode="after")
    def _fingerprint_key_required_in_prod(self) -> "Settings":
        key = self.telemetry_fingerprint_key.get_secret_value()
        if self.env == "prod" and (not key or key == _DEV_FINGERPRINT_KEY):
            raise ValueError("AC_TELEMETRY_FINGERPRINT_KEY must be set to a deployment value under AC_ENV=prod")
        return self

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
