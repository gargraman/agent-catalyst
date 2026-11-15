"""Storage backend interface (feature 024). Keys are opaque, workspace-prefixed (`ws/{ws}/kb/{kb}/doc/{id}/{sha}`)
and never returned to clients."""
from __future__ import annotations

import datetime
from typing import Protocol


class StorageBackend(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None:
        """Idempotent: deleting a missing key is a no-op."""
        ...

    async def list(self, prefix: str) -> list[tuple[str, "datetime.datetime"]]:
        """[(key, last_modified tz-aware)] under a prefix."""
        ...
