"""DB-backed repository for the ingestion pipeline (feature 024, tasks.md T028; data-model §2–§4).

Every step opens its OWN short `tenant_session(workspace)` (RLS-scoped, commits on exit) — never the request's session
and never one long transaction across embedding calls (research D4). `finalize_ready` is the single atomic flip:
promote the new generation, drop every other generation, update counts/dedup key, bump the KB index_version, and
mark the job ready — all in one transaction, so retrieval never sees a mixed or empty document (FR-005).
"""
from __future__ import annotations

import datetime
import uuid
from decimal import Decimal

from sqlalchemy import delete, func, insert, select, update

from api.db import tenant_session
from api.ingest_pipeline import ChunkRow, JobContext
from api.models import IngestionJob, KbChunk, KbDocument, KnowledgeBase


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


class DbIngestRepo:
    def __init__(self, workspace_id: str, *, owner: str = "") -> None:
        self.ws = str(workspace_id)
        self.owner = owner

    async def load(self, job_id) -> JobContext:
        async with tenant_session(self.ws) as s:
            row = (await s.execute(
                select(IngestionJob, KbDocument, KnowledgeBase)
                .join(KbDocument, KbDocument.id == IngestionJob.document_id)
                .join(KnowledgeBase, KnowledgeBase.id == IngestionJob.kb_id)
                .where(IngestionJob.id == uuid.UUID(str(job_id))))).one()
            job, doc, kb = row
            return JobContext(
                job_id=str(job.id), workspace_id=self.ws, kb_id=str(kb.id), document_id=str(doc.id), kind=job.kind,
                storage_key=doc.storage_key, media_type=doc.media_type, content_hash=doc.content_hash,
                active_generation=doc.active_generation, dedup_key=doc.dedup_key,
                embedding_model=kb.embedding_model, chunk_config=dict(kb.chunk_config or {}),
            )

    async def status(self, job_id) -> str:
        async with tenant_session(self.ws) as s:
            return (await s.execute(select(IngestionJob.status).where(IngestionJob.id == uuid.UUID(str(job_id))))).scalar_one()

    async def mark_processing(self, job_id) -> bool:
        """Claim the job ONLY if it is still queued (compare-and-set). A job the reaper already failed — possibly
        retried since — must not be resurrected by a late executor slot (two jobs on one generation = duplicates)."""
        async with tenant_session(self.ws) as s:
            doc_id = (await s.execute(
                update(IngestionJob).where(IngestionJob.id == uuid.UUID(str(job_id)), IngestionJob.status == "queued")
                .values(status="processing", started_at=func.now(), heartbeat_at=func.now(), owner=self.owner)
                .returning(IngestionJob.document_id))).scalar_one_or_none()
            if doc_id is None:
                return False
            await s.execute(update(KbDocument).where(KbDocument.id == doc_id)
                            .values(status="processing", updated_at=func.now()))
            return True

    async def heartbeat(self, job_id) -> None:
        async with tenant_session(self.ws) as s:
            await s.execute(update(IngestionJob).where(IngestionJob.id == uuid.UUID(str(job_id)),
                                                       IngestionJob.status.in_(("queued", "processing")))
                            .values(heartbeat_at=func.now(), owner=self.owner))

    async def set_progress(self, job_id, done: int, total: int) -> None:
        async with tenant_session(self.ws) as s:
            await s.execute(update(IngestionJob).where(IngestionJob.id == uuid.UUID(str(job_id)))
                            .values(progress_done=done, progress_total=total, heartbeat_at=func.now()))

    async def insert_chunks(self, document_id, generation: int, rows: list[ChunkRow]) -> None:
        if not rows:
            return
        async with tenant_session(self.ws) as s:
            doc = await s.get(KbDocument, uuid.UUID(str(document_id)))
            await s.execute(insert(KbChunk), [
                {"workspace_id": doc.workspace_id, "kb_id": doc.kb_id, "document_id": doc.id,
                 "generation": generation, "ordinal": r.ordinal, "text": r.text, "token_count": r.token_count,
                 "embedding": list(r.embedding), "embedding_model": r.embedding_model, "meta": r.meta,
                 "injection_categories": list(r.injection_categories)}
                for r in rows])

    async def delete_generation(self, document_id, generation: int) -> None:
        async with tenant_session(self.ws) as s:
            await s.execute(delete(KbChunk).where(KbChunk.document_id == uuid.UUID(str(document_id)),
                                                  KbChunk.generation == generation))

    async def finalize_ready(self, job_id, *, generation: int, previous_generation: int, chunk_count: int,
                             dedup_key: str, cost_usd: Decimal, cost_is_estimate: bool,
                             injection_flags: dict | None = None) -> None:
        async with tenant_session(self.ws) as s:                       # ONE transaction (FR-005)
            job = await s.get(IngestionJob, uuid.UUID(str(job_id)))
            await s.execute(delete(KbChunk).where(KbChunk.document_id == job.document_id,
                                                  KbChunk.generation != generation))
            await s.execute(update(KbDocument).where(KbDocument.id == job.document_id).values(
                active_generation=generation, chunk_count=chunk_count, status="ready", dedup_key=dedup_key,
                error=None, injection_flags=dict(injection_flags or {}), updated_at=func.now()))
            await s.execute(update(KnowledgeBase).where(KnowledgeBase.id == job.kb_id).values(
                index_version=KnowledgeBase.index_version + 1, last_ingested_at=func.now()))
            job.status, job.cost_usd, job.cost_is_estimate = "ready", cost_usd, cost_is_estimate
            job.progress_done = job.progress_total = chunk_count
            job.finished_at, job.error = _now(), None

    async def finalize_skipped(self, job_id) -> None:
        """Dedup hit (FR-006): nothing changed, so no index_version bump and zero cost."""
        async with tenant_session(self.ws) as s:
            job = await s.get(IngestionJob, uuid.UUID(str(job_id)))
            job.status, job.skipped_unchanged, job.cost_usd, job.finished_at = "ready", True, Decimal(0), _now()
            await s.execute(update(KbDocument).where(KbDocument.id == job.document_id)
                            .values(status="ready", error=None, updated_at=func.now()))

    async def finalize_failed(self, job_id, *, error: dict, has_live_generation: bool,
                              cost_usd: Decimal = Decimal(0), cost_is_estimate: bool = False) -> None:
        async with tenant_session(self.ws) as s:
            job = await s.get(IngestionJob, uuid.UUID(str(job_id)))
            job.cost_usd, job.cost_is_estimate = cost_usd, cost_is_estimate      # billed spend still counts (cap)
            job.status, job.error, job.finished_at = "failed", error, _now()
            await s.execute(update(KbDocument).where(KbDocument.id == job.document_id).values(
                status="ready" if has_live_generation else "failed", error=error, updated_at=func.now()))
