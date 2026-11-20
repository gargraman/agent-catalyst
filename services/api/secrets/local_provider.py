"""In-process secrets provider (feature 014) — for the unit suite and offline dev.

Holds secrets in a process-local dict keyed by an opaque `local://…` handle. Ephemeral: a captured secret
does NOT survive a process restart, so local-dev-with-compose uses the `aws` provider against LocalStack
(which persists for the container session). The non-prod `PROVIDER_API_KEY` dev-continuity fallback is applied
by the custody layer (resolve_provider_key), NOT here — this stays a clean store.
"""
from __future__ import annotations

import uuid

from api.secrets.base import SecretNotFound


class LocalProvider:
    """A minimal in-memory `SecretsProvider`. Not for production custody — raw values live in process memory."""

    id = "local"

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def put(self, workspace_id: str, logical_id: str, value: str) -> str:
        vault_path = f"local://{workspace_id}/{logical_id}/{uuid.uuid4().hex}"
        self._store[vault_path] = value
        return vault_path

    async def get(self, vault_path: str) -> str:
        try:
            return self._store[vault_path]
        except KeyError:
            raise SecretNotFound(f"no secret at {vault_path}") from None

    async def rotate(self, vault_path: str, value: str) -> str:
        if vault_path not in self._store:
            raise SecretNotFound(f"no secret at {vault_path}")
        self._store[vault_path] = value
        return vault_path

    async def disable(self, vault_path: str) -> None:
        self._store.pop(vault_path, None)  # idempotent
