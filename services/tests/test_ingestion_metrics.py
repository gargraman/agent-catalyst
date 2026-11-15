"""Ingestion metrics (feature 024, tasks.md T033). Aggregate counters/histograms on /metrics, never a tenant label."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from api.ingest_executor import get_executor
from api.settings import get_settings
from api.storage import get_storage
from runtime.providers import transport
from tests._api_helpers import client, dev_headers, seed_kb, seed_workspace

pytestmark = pytest.mark.integration


async def test_ingestion_metrics_exposed_without_tenant_labels(monkeypatch):
    monkeypatch.setenv("AC_STORAGE_PROVIDER", "local")
    get_settings.cache_clear()
    get_storage.cache_clear()

    async def _embed(**kw):
        n = len(kw["input"])
        return SimpleNamespace(data=[{"index": i, "embedding": [0.01] * 1024} for i in range(n)],
                               usage=SimpleNamespace(prompt_tokens=10 * n), _hidden_params={})
    monkeypatch.setattr(transport, "embed", _embed)
    ws = await seed_workspace()
    kb = await seed_kb(ws)
    async with client() as c:
        r = await c.post(f"/api/v1/knowledge-bases/{kb}/documents", headers=dev_headers(workspace=ws),
                         files={"file": ("faq.txt", (Path(__file__).parent / "fixtures/docs/faq.txt").read_bytes(), "text/plain")})
        assert r.status_code == 202
        await get_executor().drain()
        body = (await c.get("/metrics")).text
    get_settings.cache_clear()
    get_storage.cache_clear()
    assert 'ac_ingestion_jobs_total{status="ready"}' in body
    assert "ac_ingestion_seconds_bucket" in body
    assert "ac_ingestion_reaped_total" in body
    assert "ac_storage_orphans_deleted_total" in body
    ingest_lines = [l for l in body.splitlines() if l.startswith(("ac_ingestion", "ac_storage_orphans"))]
    assert ingest_lines and not any(ws in l or kb in l or "workspace" in l for l in ingest_lines)
