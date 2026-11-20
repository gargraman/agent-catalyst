"""In-memory storage backend (unit tests + offline dev). NOT durable: a restart loses every upload, so it must never
back a deployment that keeps data (see analyze M2 — prod guard not yet in scope)."""
from __future__ import annotations

import datetime


class LocalStorage:
    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, datetime.datetime]] = {}

    async def put(self, key: str, data: bytes) -> None:
        self._objects[key] = (bytes(data), datetime.datetime.now(datetime.timezone.utc))

    async def get(self, key: str) -> bytes:
        try:
            return self._objects[key][0]
        except KeyError:
            raise KeyError(f"no stored object for key ending …{key[-12:]}") from None

    async def delete(self, key: str) -> None:
        self._objects.pop(key, None)

    async def list(self, prefix: str) -> list[tuple[str, datetime.datetime]]:
        """[(key, last_modified)] under `prefix`, key-sorted (orphan sweep, analyze H5)."""
        return sorted((k, ts) for k, (_, ts) in self._objects.items() if k.startswith(prefix))
