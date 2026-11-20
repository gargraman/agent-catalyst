"""Active secrets-provider selection (feature 014).

The single place that maps config → a concrete `SecretsProvider` (mirrors api/auth/registry). `aws` is
lazy-imported so the unit suite (and runtime/ purity scan) never pull in boto3. Fail-closed: an unknown
provider id raises (no secret in the message).
"""
from __future__ import annotations

from functools import lru_cache

from api.errors import AppError
from api.secrets.base import SecretsProvider
from api.secrets.local_provider import LocalProvider
from api.settings import get_settings


class SecretsConfigError(AppError):
    """Misconfigured secrets backend — refused fail-closed via the envelope (503) rather than a raw 500.
    Message carries no secret."""
    http_status, code = 503, "secrets.unavailable"


@lru_cache(maxsize=1)
def build_secrets_provider() -> SecretsProvider:
    """Process-wide singleton selected by `AC_SECRETS_PROVIDER`. Call ``build_secrets_provider.cache_clear()``
    in tests that change the selector."""
    s = get_settings()
    name = s.secrets_provider
    if name == "local":
        return LocalProvider()
    if name == "aws":
        from api.secrets.aws_provider import AwsProvider  # lazy — keeps boto3 out of the unit run
        return AwsProvider(endpoint_url=s.aws_endpoint_url, region=s.aws_region,
                           prefix=s.secrets_prefix, env=s.env)
    raise SecretsConfigError(f"unknown secrets provider id: {name!r}")
