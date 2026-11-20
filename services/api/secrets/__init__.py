"""Pluggable secret-custody seam (feature 014).

Raw provider keys (and, later, other credentials) live ONLY behind this seam — in AWS Secrets Manager in a
deployed environment, LocalStack locally, or an in-process store in the unit suite. The application database
holds only a masked display value + an opaque `vault_path` reference. API-layer only: `runtime/` never imports
this package (P6 purity, guarded by tests/test_runtime_purity.py).
"""
from __future__ import annotations

from api.secrets.base import (
    SecretNotFound,
    SecretsError,
    SecretsProvider,
    SecretsUnavailable,
)
from api.secrets.registry import build_secrets_provider

__all__ = [
    "SecretsProvider",
    "SecretsError",
    "SecretNotFound",
    "SecretsUnavailable",
    "build_secrets_provider",
]
