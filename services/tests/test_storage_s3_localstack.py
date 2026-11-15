"""Opt-in LocalStack test for the APP's S3 storage backend (feature 024, tasks.md T074; code-review R9).

Skipped unless `AC_RUN_LOCALSTACK=1` with the compose `localstack` service up (`make test-integration`). Unlike
test_s3_buckets_localstack.py (raw boto3, infra only), this drives `api.storage.registry.get_storage()` with
`AC_STORAGE_PROVIDER=s3` — the exact object the ingest pipeline uses — so it proves the prod storage path (R9 made s3
mandatory under prod) works end-to-end: put → list → get → delete under a `ws/{ws}/…` key, idempotent delete.
"""
from __future__ import annotations

import os
import uuid

import pytest

RUN = os.environ.get("AC_RUN_LOCALSTACK") == "1"
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not RUN, reason="set AC_RUN_LOCALSTACK=1 (and start compose localstack with s3) to run"),
]


@pytest.fixture
def storage(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", os.environ.get("AWS_ACCESS_KEY_ID", "test"))
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", os.environ.get("AWS_SECRET_ACCESS_KEY", "test"))
    monkeypatch.setenv("AC_STORAGE_PROVIDER", "s3")
    monkeypatch.setenv("AC_UPLOADS_BUCKET", "ac-uploads")
    monkeypatch.setenv("AC_AWS_ENDPOINT_URL", os.environ.get("AC_AWS_ENDPOINT_URL", "http://localhost:4566"))
    from api import settings as settings_mod
    from api.storage import registry

    for fn in (getattr(settings_mod, "get_settings", None), getattr(registry, "get_storage", None)):
        if hasattr(fn, "cache_clear"):
            fn.cache_clear()
    backend = registry.get_storage()
    yield backend
    for fn in (getattr(settings_mod, "get_settings", None), getattr(registry, "get_storage", None)):
        if hasattr(fn, "cache_clear"):
            fn.cache_clear()


async def test_app_s3_backend_roundtrip_under_workspace_prefix(storage):
    from api.storage.s3 import S3Storage

    assert isinstance(storage, S3Storage)
    ws = f"ws-{uuid.uuid4().hex[:8]}"
    key = f"ws/{ws}/kb/k1/doc/{uuid.uuid4().hex}/original.md"
    body = "# Leave policy\nAnnual leave is 27 days.\n".encode()

    await storage.put(key, body)
    listed = [k for k, _ in await storage.list(f"ws/{ws}/")]
    assert listed == [key]
    assert await storage.get(key) == body

    await storage.delete(key)
    await storage.delete(key)                      # idempotent: a second delete is not an error
    assert await storage.list(f"ws/{ws}/") == []
