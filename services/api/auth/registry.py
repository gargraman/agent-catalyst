"""Active IdP provider selection (feature 012).

The single place that maps config → a concrete provider (FR-006). Fail-closed: the unverified `dev` provider is
refused under a production profile (FR-008); an unknown/misconfigured provider id raises (no secret in the
message, R5). This is the ONLY selection site, so swapping vendors never touches a call site (FR-005/SC-003).
"""
from __future__ import annotations

from functools import lru_cache

from api.auth.base import IdPProvider
from api.auth.dev_provider import DevHeaderProvider
from api.auth.jwt_provider import JwtBearerProvider, make_jwks_resolver
from api.errors import AppError


class AuthConfigError(AppError):
    """Misconfigured or disallowed auth provider — request refused fail-closed via the error envelope
    (503 auth.unavailable) rather than an unhandled 500. Message carries no secret. (code-review hardening)"""
    http_status, code = 503, "auth.unavailable"


@lru_cache(maxsize=8)
def _trellix_iam(issuer: str, audience: str, jwks_uri: str, algs_csv: str, leeway: int) -> JwtBearerProvider:
    # Cached so the underlying JWKS client (and its key cache) is reused across requests (FR-017).
    algs = [a.strip() for a in algs_csv.split(",") if a.strip()]
    return JwtBearerProvider(id="trellix-iam", issuer=issuer, audience=audience, algorithms=algs,
                             leeway=leeway, key_resolver=make_jwks_resolver(jwks_uri))


def get_active_provider(settings) -> IdPProvider:
    name = settings.auth_provider
    if name == "dev":
        if settings.env == "prod":
            raise AuthConfigError("dev auth provider is disabled under a production profile (AC_ENV=prod)")
        return DevHeaderProvider()
    if name == "trellix-iam":
        return _trellix_iam(settings.iam_issuer, settings.iam_audience, settings.iam_jwks_uri,
                            settings.iam_allowed_algs, settings.iam_clock_skew_s)
    raise AuthConfigError(f"unknown auth provider id: {name!r}")
