"""Stale-job recovery (feature 024, tasks.md T023; research D5; data-model §4).

`ingestion_reap_stale(interval)` (SECURITY DEFINER, migration 0013) fails ONLY jobs whose heartbeat — or, for never-
started queued jobs, creation time — is older than the lease; a live replica's job with a fresh heartbeat is untouched.
Documents return to `ready` when they still have a live generation, else `failed`. The executor runs one sweep at app
startup (lifespan) and then periodically. Integration: compose Postgres.
"""
from __future__ import annotations

import asyncio
import uuid as _uuid

import pytest
from sqlalchemy import select, text

from api import db
from api.db import tenant_session
from api.ingest_executor import get_executor, reap_once
from api.main import app
from api.models import IngestionJob, KbDocument
from tests._api_helpers import seed_kb, seed_workspace

pytestmark = pytest.mark.integration


async def _doc_with_job(ws: str, *, active_generation: int, job_status: str, heartbeat_age_s: int | None,
                        created_age_s: int = 0) -> tuple[str, str]:
    kb = await seed_kb(ws, slug=f"kb-{_uuid.uuid4().hex[:6]}")
    async with tenant_session(ws) as s:
        d = KbDocument(workspace_id=_uuid.UUID(ws), kb_id=_uuid.UUID(kb), name=f"{_uuid.uuid4().hex[:6]}.txt",
                       media_type="text/plain", size_bytes=1, content_hash="h", storage_key="k",
                       status="processing", active_generation=active_generation, created_by="t")
        s.add(d)
        await s.flush()
        j = IngestionJob(workspace_id=d.workspace_id, kb_id=d.kb_id, document_id=d.id, kind="upload",
                         status=job_status, created_by="t")
        s.add(j)
        await s.flush()
        await s.execute(text(
            "update ingestion_jobs set created_at = now() - make_interval(secs => CAST(:c AS double precision)), "
            "heartbeat_at = CASE WHEN CAST(:h AS double precision) IS NULL THEN NULL "
            "ELSE now() - make_interval(secs => CAST(:h AS double precision)) END "
            "where id = :id"), {"c": created_age_s, "h": heartbeat_age_s, "id": str(j.id)})
        return str(d.id), str(j.id)


async def _job(ws, job_id) -> IngestionJob:
    async with tenant_session(ws) as s:
        return (await s.execute(select(IngestionJob).where(IngestionJob.id == _uuid.UUID(job_id)))).scalar_one()


async def _doc(ws, doc_id) -> KbDocument:
    async with tenant_session(ws) as s:
        return (await s.execute(select(KbDocument).where(KbDocument.id == _uuid.UUID(doc_id)))).scalar_one()


async def _reap_sql(seconds: int) -> int:
    async with db.app_engine().begin() as conn:     # plain ac_app session, NO tenant GUC
        return int((await conn.execute(text("select ingestion_reap_stale(make_interval(secs => :s))"),
                                       {"s": seconds})).scalar_one())


async def test_only_stale_heartbeat_is_reaped_live_replica_untouched():
    ws = await seed_workspace()
    stale_doc, stale_job = await _doc_with_job(ws, active_generation=0, job_status="processing", heartbeat_age_s=600)
    live_doc, live_job = await _doc_with_job(ws, active_generation=0, job_status="processing", heartbeat_age_s=0)
    n = await _reap_sql(120)
    assert n >= 1
    sj, lj = await _job(ws, stale_job), await _job(ws, live_job)
    assert sj.status == "failed" and sj.error == {"code": "interrupted", "message": "ingestion was interrupted; retry",
                                                  "retryable": True}
    assert sj.finished_at is not None
    assert lj.status == "processing" and lj.error is None
    assert (await _doc(ws, stale_doc)).status == "failed"       # no live generation
    assert (await _doc(ws, live_doc)).status == "processing"


async def test_document_with_live_generation_returns_to_ready():
    ws = await seed_workspace()
    doc, job = await _doc_with_job(ws, active_generation=3, job_status="processing", heartbeat_age_s=900)
    await _reap_sql(120)
    assert (await _job(ws, job)).status == "failed" and (await _doc(ws, doc)).status == "ready"


async def test_queued_job_with_old_created_at_and_null_heartbeat_is_reaped():
    ws = await seed_workspace()
    _, job = await _doc_with_job(ws, active_generation=0, job_status="queued", heartbeat_age_s=None, created_age_s=900)
    _, fresh = await _doc_with_job(ws, active_generation=0, job_status="queued", heartbeat_age_s=None, created_age_s=5)
    await _reap_sql(120)
    assert (await _job(ws, job)).status == "failed" and (await _job(ws, fresh)).status == "queued"


async def test_reaper_is_cross_workspace_but_app_role_still_cannot_read_other_tenant():
    ws_a, ws_b = await seed_workspace(), await seed_workspace()
    _, ja = await _doc_with_job(ws_a, active_generation=0, job_status="processing", heartbeat_age_s=600)
    _, jb = await _doc_with_job(ws_b, active_generation=0, job_status="processing", heartbeat_age_s=600)
    assert await _reap_sql(120) >= 2
    assert (await _job(ws_a, ja)).status == "failed" and (await _job(ws_b, jb)).status == "failed"
    async with tenant_session(ws_a) as s:
        leaked = (await s.execute(select(IngestionJob).where(IngestionJob.id == _uuid.UUID(jb)))).scalars().all()
    assert leaked == []


async def test_reap_once_helper_returns_count():
    ws = await seed_workspace()
    await _doc_with_job(ws, active_generation=0, job_status="processing", heartbeat_age_s=3600)
    assert await reap_once() >= 1


async def test_app_startup_runs_a_sweep():
    ws = await seed_workspace()
    _, job = await _doc_with_job(ws, active_generation=0, job_status="processing", heartbeat_age_s=3600)
    async with app.router.lifespan_context(app):
        pass
    assert (await _job(ws, job)).status == "failed"


async def test_periodic_reaper_loop_sweeps_again():
    ex = get_executor()
    ex.start_reaper(interval_s=0.05)
    try:
        ws = await seed_workspace()
        _, job = await _doc_with_job(ws, active_generation=0, job_status="processing", heartbeat_age_s=3600)
        for _ in range(40):
            if (await _job(ws, job)).status == "failed":
                break
            await asyncio.sleep(0.05)
        assert (await _job(ws, job)).status == "failed"
    finally:
        await ex.stop_reaper()


# ---------------------------------------------------------------- code review 2026-11-15 #2: queued wait ≠ dead process

async def test_heartbeat_keeps_a_queued_job_out_of_the_reaper():
    """A job waiting for an executor slot is heartbeated by its (live) owner, so it is not reaped as interrupted."""
    from api.ingest_repo import DbIngestRepo
    ws = await seed_workspace()
    _, jid = await _doc_with_job(ws, active_generation=0, job_status="queued", heartbeat_age_s=None,
                                 created_age_s=10_000)
    await DbIngestRepo(ws).heartbeat(jid)
    await _reap_sql(120)
    assert (await _job(ws, jid)).status == "queued"


async def test_mark_processing_refuses_a_job_that_is_no_longer_queued():
    """A job the reaper already failed (and the user may have retried) must not be resurrected by a late slot —
    two jobs writing the same generation is how a document got duplicate chunks."""
    from api.ingest_repo import DbIngestRepo
    ws = await seed_workspace()
    did, jid = await _doc_with_job(ws, active_generation=1, job_status="failed", heartbeat_age_s=None)
    ok = await DbIngestRepo(ws).mark_processing(jid)
    assert ok is False
    job = await _job(ws, jid)
    assert job.status == "failed" and job.started_at is None          # never claimed, never restarted
