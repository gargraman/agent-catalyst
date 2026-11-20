"""Document upload + background ingestion through the API (feature 024, tasks.md T022; contracts/api.md "Documents &
ingestion"; spec US1, FR-001..FR-011, H3).

Integration (compose Postgres, AC_STORAGE_PROVIDER=local). NO live calls: `runtime.providers.transport.embed` is
replaced with a deterministic stub. The in-process executor is drained with `get_executor().drain()` (httpx's
ASGITransport does not run the app lifespan, so the executor must be a lazily created process-wide singleton).

Embedding key: resolved at upload time, fail closed — a configured-but-unresolvable key refuses the upload (503
config_error) before anything is stored (same rule as invoke, feature 014). In this dev test env a *missing* key falls
back to PROVIDER_API_KEY.
"""
from __future__ import annotations

import asyncio
import uuid as _uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text

from api.db import tenant_session
from api.ingest_executor import get_executor
from api.models import IngestionJob, KbChunk, KbDocument, KnowledgeBase
from api.settings import get_settings
from api.storage import get_storage
from runtime.providers import transport
from runtime.providers.transport import TransportError
from tests._api_helpers import (
    client, dev_headers, seed_kb, seed_limits, seed_provider_key, seed_run, seed_workspace,
)

pytestmark = pytest.mark.integration

DOCS = Path(__file__).parent / "fixtures" / "docs"
_DENY_BUILDER = {"visibility": "restricted", "useRoles": ["admin"], "manageRoles": ["admin"]}
_USE_NOT_MANAGE = {"visibility": "restricted", "useRoles": ["builder"], "manageRoles": ["publisher"]}


# ------------------------------------------------------------------------------------------------- harness


class EmbedStub:
    def __init__(self):
        self.calls = 0
        self.fail_next: Exception | None = None
        self.fail_repeat = 1          # feature 025: raise fail_next on this many consecutive calls (gateway retries)

    async def __call__(self, **kwargs):
        self.calls += 1
        if self.fail_next is not None:
            exc = self.fail_next
            self.fail_repeat -= 1
            if self.fail_repeat <= 0:
                self.fail_next, self.fail_repeat = None, 1
            raise exc
        inp = kwargs["input"]
        return SimpleNamespace(
            data=[{"index": i, "embedding": [((hash(t) % 997) + 1) / 1000.0] * 1024} for i, t in enumerate(inp)],
            usage=SimpleNamespace(prompt_tokens=50 * len(inp), total_tokens=50 * len(inp)),
            _hidden_params={"additional_headers": {}},
        )


@pytest.fixture
def embed(monkeypatch):
    stub = EmbedStub()
    monkeypatch.setattr(transport, "embed", stub)
    return stub


@pytest.fixture(autouse=True)
def _local_storage(monkeypatch):
    monkeypatch.setenv("AC_STORAGE_PROVIDER", "local")
    get_settings.cache_clear()
    get_storage.cache_clear()
    yield
    get_settings.cache_clear()
    get_storage.cache_clear()


def _file(name: str) -> bytes:
    return (DOCS / name).read_bytes()


async def _upload(c, ws, kb, name, data, *, headers=None):
    return await c.post(f"/api/v1/knowledge-bases/{kb}/documents", headers=headers or dev_headers(workspace=ws),
                        files={"file": (name, data, "application/octet-stream")})


async def _drain():
    await get_executor().drain()


async def _count(ws, model, **where) -> int:
    async with tenant_session(ws) as s:
        q = select(func.count()).select_from(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return int((await s.execute(q)).scalar_one())


def _objects_under(ws: str) -> list[str]:
    st = get_storage()
    return [k for k in getattr(st, "_objects", {}) if k.startswith(f"ws/{ws}/")]


async def _kb_row(ws, kb) -> KnowledgeBase:
    async with tenant_session(ws) as s:
        return (await s.execute(select(KnowledgeBase).where(KnowledgeBase.id == _uuid.UUID(kb)))).scalar_one()


def _no_secrets(resp) -> None:
    body = resp.text
    assert "storage_key" not in body and "content_hash" not in body and "ws/" not in body


# ------------------------------------------------------------------------------------------------- happy path


async def test_upload_txt_processes_to_ready_and_kb_counts_update(embed):
    ws = await seed_workspace()
    kb = await seed_kb(ws)
    async with client() as c:
        r = await _upload(c, ws, kb, "faq.txt", _file("faq.txt"))
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["document"]["status"] in ("queued", "processing") and body["job"]["id"]
        _no_secrets(r)
        await _drain()
        job = (await c.get(f"/api/v1/ingestion-jobs/{body['job']['id']}", headers=dev_headers(workspace=ws))).json()
        assert job["status"] == "ready" and job["progress"]["done"] == job["progress"]["total"] > 0
        assert job["kind"] == "upload" and job["document_id"] == body["document"]["id"]
        assert job["cost_is_estimate"] is True and float(job["cost_usd"]) > 0
        k = (await c.get(f"/api/v1/knowledge-bases/{kb}", headers=dev_headers(workspace=ws))).json()
    assert k["document_count"] == 1 and k["chunk_count"] > 0
    assert k["ingestion_status"] == "ready" and k["last_ingested_at"] and k["embedding_provider"] == "openai"
    assert embed.calls >= 1


async def test_pdf_upload_keeps_page_numbers_on_chunks(embed):
    ws = await seed_workspace()
    kb = await seed_kb(ws)
    async with client() as c:
        assert (await _upload(c, ws, kb, "policy.pdf", _file("policy.pdf"))).status_code == 202
        await _drain()
    async with tenant_session(ws) as s:
        metas = (await s.execute(select(KbChunk.meta, KbChunk.text))).all()
    assert any(m["page"] == 2 and "30 days" in t for m, t in metas)


# ------------------------------------------------------------------------------------------------- refusals


@pytest.mark.parametrize("name, data_from, code", [
    ("setup.exe", "faq.txt", "unsupported_type"),
    ("fake.pdf", "fake.pdf", "content_mismatch"),
])
async def test_refused_uploads_store_nothing(embed, name, data_from, code):
    ws = await seed_workspace()
    kb = await seed_kb(ws)
    async with client() as c:
        r = await _upload(c, ws, kb, name, _file(data_from))
    assert r.status_code == 400 and r.json()["error"]["code"] == "input.invalid"
    assert r.json()["error"]["detail"]["code"] == code
    assert await _count(ws, KbDocument) == 0 and _objects_under(ws) == [] and embed.calls == 0


async def test_empty_file_refused(embed):
    ws = await seed_workspace()
    kb = await seed_kb(ws)
    async with client() as c:
        r = await _upload(c, ws, kb, "empty.txt", b"")
    assert r.status_code == 400 and await _count(ws, KbDocument) == 0 and _objects_under(ws) == []


async def test_oversize_file_413_nothing_stored(embed, monkeypatch):
    monkeypatch.setenv("AC_INGEST_MAX_FILE_BYTES", "1024")
    get_settings.cache_clear()
    ws = await seed_workspace()
    kb = await seed_kb(ws)
    async with client() as c:
        r = await _upload(c, ws, kb, "big.txt", b"a" * 2048)
    assert r.status_code == 413 and r.json()["error"]["code"] == "upload.too_large"
    assert await _count(ws, KbDocument) == 0 and _objects_under(ws) == []


async def test_image_only_pdf_accepted_then_fails_no_extractable_text(embed):
    ws = await seed_workspace()
    kb = await seed_kb(ws)
    async with client() as c:
        r = await _upload(c, ws, kb, "scanned.pdf", _file("scanned.pdf"))
        assert r.status_code == 202
        await _drain()
        job = (await c.get(f"/api/v1/ingestion-jobs/{r.json()['job']['id']}", headers=dev_headers(workspace=ws))).json()
    assert job["status"] == "failed" and job["error"]["code"] == "no_extractable_text"
    assert job["error"]["retryable"] is False and embed.calls == 0


async def test_unresolvable_configured_key_refuses_upload_503(embed):
    ws = await seed_workspace()
    kb = await seed_kb(ws)
    await seed_provider_key(ws, provider="openai", vault_path="local://nowhere/missing/1")
    async with client() as c:
        r = await _upload(c, ws, kb, "faq.txt", _file("faq.txt"))
    assert r.status_code == 503 and r.json()["error"]["code"] == "config_error"
    assert await _count(ws, KbDocument) == 0 and _objects_under(ws) == []


# ------------------------------------------------------------------------------------------------- authz / state


async def test_viewer_cannot_upload_403(embed):
    ws = await seed_workspace()
    kb = await seed_kb(ws)
    async with client() as c:
        r = await _upload(c, ws, kb, "faq.txt", _file("faq.txt"), headers=dev_headers(workspace=ws, claims={ws: "viewer"}))
    assert r.status_code == 403 and await _count(ws, KbDocument) == 0


async def test_policy_denied_kb_is_404_identical_to_absent(embed):
    ws = await seed_workspace()
    kb = await seed_kb(ws, slug="secret-kb", access_policy=_DENY_BUILDER)
    missing = str(_uuid.uuid4())
    async with client() as c:
        denied = await _upload(c, ws, kb, "faq.txt", _file("faq.txt"))
        absent = await _upload(c, ws, missing, "faq.txt", _file("faq.txt"))
    assert denied.status_code == absent.status_code == 404
    # Each response echoes only the id the CALLER sent (existing KB convention); with that normalized the two bodies
    # must be byte-identical — a denied KB is indistinguishable from an absent one.
    assert denied.text.replace(kb, "<id>") == absent.text.replace(missing, "<id>")


async def test_usable_but_not_manageable_is_403(embed):
    ws = await seed_workspace()
    kb = await seed_kb(ws, slug="use-only", access_policy=_USE_NOT_MANAGE)
    async with client() as c:
        r = await _upload(c, ws, kb, "faq.txt", _file("faq.txt"))
    assert r.status_code == 403


async def test_archived_kb_is_409(embed):
    ws = await seed_workspace()
    kb = await seed_kb(ws)
    async with client() as c:
        await c.post(f"/api/v1/knowledge-bases/{kb}/archive", headers=dev_headers(workspace=ws))
        r = await _upload(c, ws, kb, "faq.txt", _file("faq.txt"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "kb.archived"


async def test_over_spend_cap_is_402_and_never_embeds(embed):
    ws = await seed_workspace()
    kb = await seed_kb(ws)
    await seed_limits(ws, 10)
    await seed_run(ws, cost_usd=15.0)
    async with client() as c:
        r = await _upload(c, ws, kb, "faq.txt", _file("faq.txt"))
    assert r.status_code == 402 and r.json()["error"]["code"] == "budget.exceeded"
    assert embed.calls == 0 and await _count(ws, KbDocument) == 0


# ------------------------------------------------------------------------------------------------- dedup / replace


async def test_identical_reupload_is_skipped_unchanged_at_zero_cost(embed):
    ws = await seed_workspace()
    kb = await seed_kb(ws)
    async with client() as c:
        first = await _upload(c, ws, kb, "faq.txt", _file("faq.txt"))
        await _drain()
        calls_after_first = embed.calls
        second = await _upload(c, ws, kb, "faq.txt", _file("faq.txt"))
        await _drain()
        job = (await c.get(f"/api/v1/ingestion-jobs/{second.json()['job']['id']}", headers=dev_headers(workspace=ws))).json()
    assert second.json()["document"]["id"] == first.json()["document"]["id"]
    assert job["status"] == "ready" and job["skipped_unchanged"] is True and job["cost_usd"] == "0.000000"
    assert embed.calls == calls_after_first


async def test_changed_reupload_replaces_generation_under_same_document(embed):
    ws = await seed_workspace()
    kb = await seed_kb(ws)
    async with client() as c:
        first = await _upload(c, ws, kb, "notes.txt", b"Original note about the office location.\n")
        await _drain()
        second = await _upload(c, ws, kb, "notes.txt", b"Updated note: the office moved to the fifth floor.\n")
        await _drain()
    doc_id = first.json()["document"]["id"]
    assert second.json()["document"]["id"] == doc_id
    async with tenant_session(ws) as s:
        doc = (await s.execute(select(KbDocument).where(KbDocument.id == _uuid.UUID(doc_id)))).scalar_one()
        gens = {g for (g,) in (await s.execute(select(KbChunk.generation).where(KbChunk.document_id == doc.id))).all()}
        texts = " ".join(t for (t,) in (await s.execute(select(KbChunk.text).where(KbChunk.document_id == doc.id))).all())
    assert doc.active_generation == 2 and gens == {2}, "old generation must be gone once the new one is live"
    assert "fifth floor" in texts and "Original note" not in texts


# ------------------------------------------------------------------------------------------------- delete / retry / reingest


async def test_delete_removes_chunks_object_and_bumps_index_version(embed):
    ws = await seed_workspace()
    kb = await seed_kb(ws)
    async with client() as c:
        r = await _upload(c, ws, kb, "faq.txt", _file("faq.txt"))
        await _drain()
        before = (await _kb_row(ws, kb)).index_version
        assert _objects_under(ws)
        d = await c.delete(f"/api/v1/knowledge-bases/{kb}/documents/{r.json()['document']['id']}",
                           headers=dev_headers(workspace=ws))
    assert d.status_code == 204
    assert await _count(ws, KbChunk) == 0 and await _count(ws, KbDocument) == 0
    assert _objects_under(ws) == [] and (await _kb_row(ws, kb)).index_version == before + 1


async def _force_active_job(ws, doc_id, status="processing"):
    async with tenant_session(ws) as s:
        doc = (await s.execute(select(KbDocument).where(KbDocument.id == _uuid.UUID(doc_id)))).scalar_one()
        s.add(IngestionJob(workspace_id=doc.workspace_id, kb_id=doc.kb_id, document_id=doc.id, kind="retry",
                           status=status, created_by="test"))


async def test_delete_retry_and_reupload_while_active_are_409_document_processing(embed):
    ws = await seed_workspace()
    kb = await seed_kb(ws)
    async with client() as c:
        r = await _upload(c, ws, kb, "faq.txt", _file("faq.txt"))
        await _drain()
        doc_id = r.json()["document"]["id"]
        await _force_active_job(ws, doc_id)
        objects_before = list(_objects_under(ws))
        d = await c.delete(f"/api/v1/knowledge-bases/{kb}/documents/{doc_id}", headers=dev_headers(workspace=ws))
        rt = await c.post(f"/api/v1/knowledge-bases/{kb}/documents/{doc_id}/retry", headers=dev_headers(workspace=ws))
        up = await _upload(c, ws, kb, "faq.txt", b"changed while processing\n")
    for resp in (d, rt, up):
        assert resp.status_code == 409 and resp.json()["error"]["code"] == "document.processing", resp.text
    assert await _count(ws, IngestionJob, status="processing") == 1
    assert sorted(_objects_under(ws)) == sorted(objects_before), "a refused re-upload must not store a new object"


async def test_concurrent_identical_reuploads_yield_one_active_job(embed):
    ws = await seed_workspace()
    kb = await seed_kb(ws)
    async with client() as c:
        rs = await asyncio.gather(
            _upload(c, ws, kb, "faq.txt", _file("faq.txt"), headers=dev_headers(workspace=ws, idempotency_key="k1")),
            _upload(c, ws, kb, "faq.txt", _file("faq.txt"), headers=dev_headers(workspace=ws, idempotency_key="k1")),
        )
        await _drain()
    codes = sorted(r.status_code for r in rs)
    assert codes == [202, 409], [r.text for r in rs]
    assert await _count(ws, KbDocument) == 1


async def test_retry_failed_retryable_job_completes_and_ready_doc_is_not_retryable(embed):
    ws = await seed_workspace()
    kb = await seed_kb(ws)
    embed.fail_next = TransportError("upstream 503", retryable=True, status=503)
    embed.fail_repeat = 3   # feature 025: a transient error now gets retried — fail all 3 gateway attempts
    async with client() as c:
        r = await _upload(c, ws, kb, "faq.txt", _file("faq.txt"))
        await _drain()
        doc_id = r.json()["document"]["id"]
        failed = (await c.get(f"/api/v1/ingestion-jobs/{r.json()['job']['id']}", headers=dev_headers(workspace=ws))).json()
        assert failed["status"] == "failed" and failed["error"]["code"] == "embedding_failed"
        assert failed["error"]["retryable"] is True
        rt = await c.post(f"/api/v1/knowledge-bases/{kb}/documents/{doc_id}/retry", headers=dev_headers(workspace=ws))
        assert rt.status_code == 202
        await _drain()
        done = (await c.get(f"/api/v1/ingestion-jobs/{rt.json()['job']['id']}", headers=dev_headers(workspace=ws))).json()
        again = await c.post(f"/api/v1/knowledge-bases/{kb}/documents/{doc_id}/retry", headers=dev_headers(workspace=ws))
    assert done["status"] == "ready" and done["kind"] == "retry"
    assert again.status_code == 409 and again.json()["error"]["code"] == "job.not_retryable"


async def test_reingest_skips_active_documents_and_reports_counts(embed):
    ws = await seed_workspace()
    kb = await seed_kb(ws)
    async with client() as c:
        a = await _upload(c, ws, kb, "faq.txt", _file("faq.txt"))
        await _upload(c, ws, kb, "handbook.md", _file("handbook.md"))
        await _drain()
        await _force_active_job(ws, a.json()["document"]["id"])
        r = await c.post(f"/api/v1/knowledge-bases/{kb}/reingest", headers=dev_headers(workspace=ws))
    assert r.status_code == 202 and r.json() == {"enqueued": 1, "skipped_active": 1}


# ------------------------------------------------------------------------------------------------- jobs listing


async def test_ingestion_jobs_list_is_real_and_filtered_to_usable_kbs(embed):
    ws = await seed_workspace()
    kb = await seed_kb(ws)
    hidden = await seed_kb(ws, slug="hidden-kb", access_policy=_DENY_BUILDER)
    async with client() as c:
        await _upload(c, ws, kb, "faq.txt", _file("faq.txt"))
        await _drain()
    # a job on the hidden KB, created out-of-band (the caller could never upload there)
    async with tenant_session(ws) as s:
        d = KbDocument(workspace_id=_uuid.UUID(ws), kb_id=_uuid.UUID(hidden), name="x.txt", media_type="text/plain",
                       size_bytes=1, content_hash="h", storage_key="k", status="ready", created_by="t")
        s.add(d)
        await s.flush()
        s.add(IngestionJob(workspace_id=d.workspace_id, kb_id=d.kb_id, document_id=d.id, kind="upload",
                           status="ready", created_by="t"))
    async with client() as c:
        lst = await c.get(f"/api/v1/ingestion-jobs?kb_id={kb}", headers=dev_headers(workspace=ws))
        all_jobs = await c.get("/api/v1/ingestion-jobs", headers=dev_headers(workspace=ws))
    assert lst.status_code == 200 and len(lst.json()["items"]) == 1
    assert all(j["kb"] != hidden and j.get("kb_id") != hidden for j in all_jobs.json()["items"])
    _no_secrets(lst)


async def test_ingestion_job_cross_workspace_is_404(embed):
    ws_a, ws_b = await seed_workspace(), await seed_workspace()
    kb = await seed_kb(ws_a)
    async with client() as c:
        r = await _upload(c, ws_a, kb, "faq.txt", _file("faq.txt"))
        await _drain()
        other = await c.get(f"/api/v1/ingestion-jobs/{r.json()['job']['id']}", headers=dev_headers(workspace=ws_b))
    assert other.status_code == 404


async def test_documents_list_and_get_never_expose_internal_fields(embed):
    ws = await seed_workspace()
    kb = await seed_kb(ws)
    async with client() as c:
        r = await _upload(c, ws, kb, "faq.txt", _file("faq.txt"))
        await _drain()
        lst = await c.get(f"/api/v1/knowledge-bases/{kb}/documents", headers=dev_headers(workspace=ws))
        one = await c.get(f"/api/v1/knowledge-bases/{kb}/documents/{r.json()['document']['id']}",
                          headers=dev_headers(workspace=ws))
    assert lst.status_code == one.status_code == 200
    assert lst.json()["items"][0]["status"] == "ready" and one.json()["chunk_count"] > 0
    _no_secrets(lst)
    _no_secrets(one)
