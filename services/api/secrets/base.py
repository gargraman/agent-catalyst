"""The provider-agnostic secret-custody contract (feature 014).

Mirrors the feature-012 `api/auth/` seam: a Protocol here, concrete providers in sibling modules, one
config-selected singleton in `registry.py`. Every call site uses this interface, so swapping the cloud
backend is config-only (FR-002/SC-006). No method ever logs or echoes a raw secret (FR-017).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


class SecretsError(Exception):
    """Base for custody-backend failures. Carries only a non-sensitive reason — never the raw value."""


class SecretNotFound(SecretsError):
    """The referenced secret does not exist, or has been disabled/revoked."""


class SecretsUnavailable(SecretsError):
    """The custody backend is unreachable or returned an unexpected error."""


@runtime_checkable
class SecretsProvider(Protocol):
    """Interchangeable custody backend. All methods are async (the request/job path is async; a blocking
    SDK runs off the event loop inside the concrete provider). An opaque `vault_path` is the only handle a
    caller keeps — its form (an ARN, a local handle) is the provider's private business (FR-018)."""

    id: str  # "local" | "aws"

    async def put(self, workspace_id: str, logical_id: str, value: str) -> str:
        """Store `value`; return an OPAQUE `vault_path`. Raises `SecretsUnavailable` on backend failure."""
        ...

    async def get(self, vault_path: str) -> str:
        """Return the plaintext for `vault_path`. Raises `SecretNotFound` (missing/disabled) or
        `SecretsUnavailable` (backend error)."""
        ...

    async def rotate(self, vault_path: str, value: str) -> str:
        """Replace the stored value; return the `vault_path` (may be unchanged). Raises `SecretsUnavailable`."""
        ...

    async def disable(self, vault_path: str) -> None:
        """Make the secret unresolvable (revoke). Idempotent. Raises `SecretsUnavailable` on backend failure."""
        ...
