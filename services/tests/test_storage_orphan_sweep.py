"""Orphaned stored-file sweep (feature 024, tasks.md T079; analyze H5; data-model §2).

A crash between `storage.put` and the upload commit, or between a document delete's commit and its object delete, leaves
an object no row references. `sweep_orphans()` (run hourly from the executor's reaper loop) lists each workspace's
`ws/{ws}/` prefix and deletes objects older than the grace period that no `kb_documents.storage_key` references. It reads
live keys per workspace on a tenant session — no cross-tenant SQL. Integration (compose Postgres, local storage).
"""
from __future__ import annotations

import datetime
import uuid as _uuid

import pytest

from api.db import tenant_session
from api.ingest_executor import sweep_orphans
from api.models import KbDocument
from api.settings import get_settings
from api.storage import get_storage
from tests._api_helpers import seed_kb, seed_workspace

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _local(monkeypatch):
    monkeypatch.setenv("AC_STORAGE_PROVIDER", "local")
    get_settings.cache_clear()
    get_storage.cache_clear()
    yield
    get_settings.cache_clear()
    get_storage.cache_clear()


async def _doc_with_key(ws: str, key: str) -> None:
    kb = await seed_kb(ws, slug=f"kb-{_uuid.uuid4().hex[:6]}")
    async with tenant_session(ws) as s:
        s.add(KbDocument(workspace_id=_uuid.UUID(ws), kb_id=_uuid.UUID(kb), name=f"{_uuid.uuid4().hex[:6]}.txt",
                         media_type="text/plain", size_bytes=1, content_hash="h", storage_key=key, status="ready",
                         created_by="t"))


async def test_local_storage_lists_by_prefix_with_last_modified():
    st = get_storage()
    await st.put("ws/a/kb/1/doc/1/x", b"1")
    await st.put("ws/b/kb/1/doc/1/y", b"2")
    listed = await st.list("ws/a/")
    assert [k for k, _ in listed] == ["ws/a/kb/1/doc/1/x"]
    (_, modified), = listed
    assert isinstance(modified, datetime.datetime) and modified.tzinfo is not None
    assert abs((datetime.datetime.now(datetime.timezone.utc) - modified).total_seconds()) < 60


async def test_sweep_deletes_only_unreferenced_objects_past_grace():
    ws_a, ws_b = await seed_workspace(), await seed_workspace()
    st = get_storage()
    ref_a, orphan_a, ref_b = f"ws/{ws_a}/kb/k/doc/d1/aa", f"ws/{ws_a}/kb/k/doc/d2/bb", f"ws/{ws_b}/kb/k/doc/d3/cc"
    for k in (ref_a, orphan_a, ref_b):
        await st.put(k, b"bytes")
    await _doc_with_key(ws_a, ref_a)
    await _doc_with_key(ws_b, ref_b)
    later = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=2)
    deleted = await sweep_orphans(grace_s=3600, now=later)
    keys = {k for k, _ in await st.list("ws/")}
    assert deleted >= 1
    assert orphan_a not in keys
    assert ref_a in keys and ref_b in keys          # referenced objects (any workspace) are never touched


async def test_sweep_keeps_young_unreferenced_objects():
    ws = await seed_workspace()
    st = get_storage()
    young = f"ws/{ws}/kb/k/doc/d9/young"
    await st.put(young, b"in-flight upload")       # e.g. put done, upload txn not yet committed
    await sweep_orphans(grace_s=3600)
    assert young in {k for k, _ in await st.list(f"ws/{ws}/")}, "an unreferenced object younger than the grace must survive"
