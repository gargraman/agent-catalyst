"""Object-storage seam for uploaded documents (feature 024, tasks.md T008; research D6).

`api.storage.get_storage()` maps `AC_STORAGE_PROVIDER` → a backend with an async `put/get/delete` interface:
`local` = in-memory (dev/unit), `s3` = boto3 against AWS S3 / LocalStack S3 (never MinIO, feature 022). Mirrors
the secrets registry (014): the s3 module is imported lazily so the unit suite never imports boto3. Pure unit test.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from api import storage as storage_mod
from api.settings import get_settings

_SERVICES = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.delenv("AC_STORAGE_PROVIDER", raising=False)
    get_settings.cache_clear()
    if hasattr(storage_mod.get_storage, "cache_clear"):
        storage_mod.get_storage.cache_clear()
    yield
    get_settings.cache_clear()
    if hasattr(storage_mod.get_storage, "cache_clear"):
        storage_mod.get_storage.cache_clear()


async def test_local_backend_roundtrip_put_get_delete():
    st = storage_mod.get_storage()
    key = "ws/w1/kb/k1/doc/d1/abc"
    await st.put(key, b"hello bytes")
    assert await st.get(key) == b"hello bytes"
    await st.delete(key)
    await st.delete(key)          # deleting a missing key is a no-op, never an error


async def test_local_backend_is_process_wide_singleton():
    a = storage_mod.get_storage()
    b = storage_mod.get_storage()
    assert a is b, "get_storage() must return one cached backend (uploads written by the route are read by the job)"
    await a.put("k", b"v")
    assert await b.get("k") == b"v"
    await a.delete("k")


def test_unknown_provider_fails_closed(monkeypatch):
    # Settings already restrict the value (T010); the registry must ALSO refuse an unknown id rather than guess.
    import api.storage.registry as registry
    monkeypatch.setattr(registry, "get_settings", lambda: SimpleNamespace(storage_provider="minio"))
    with pytest.raises(ValueError):
        storage_mod.get_storage()


def test_resolving_local_does_not_import_boto3():
    code = (
        "import os, sys; os.environ['AC_STORAGE_PROVIDER']='local'; os.environ.setdefault('AC_ENV','dev');"
        "from api.storage import get_storage; get_storage(); print('boto3' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=_SERVICES, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-1500:]
    assert out.stdout.strip() == "False", "the local storage path must not import boto3 (lazy s3 import)"
