"""Active storage-backend selection (feature 024, research D6). Mirrors api/secrets/registry (014).

`local` = process-wide in-memory store (unit/dev only: contents vanish on restart). `s3` = AWS S3 / LocalStack S3 via
boto3, imported LAZILY so the unit suite and the local path never import boto3. Fail closed on any other id.
"""
from __future__ import annotations

from functools import lru_cache

from api.settings import get_settings
from api.storage.base import StorageBackend


@lru_cache(maxsize=1)
def get_storage() -> StorageBackend:
    """Process-wide singleton (the route that writes an upload and the job that reads it share it).
    Call ``get_storage.cache_clear()`` in tests that change the selector."""
    s = get_settings()
    name = s.storage_provider
    if name == "local":
        from api.storage.local import LocalStorage
        return LocalStorage()
    if name == "s3":
        from api.storage.s3 import S3Storage  # lazy — keeps boto3 out of the unit run
        return S3Storage(bucket=s.uploads_bucket, endpoint_url=s.aws_endpoint_url, region=s.aws_region)
    raise ValueError(f"unknown storage provider id: {name!r}")
