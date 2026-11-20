"""Knowledge-base documents + ingestion jobs (feature 024 / M7, tasks.md T030; contracts/api.md "Documents & ingestion").

Upload → validate (size, empty, content sniff) → key check (fail closed, key-based providers only) → store the bytes →
document + queued job committed in their OWN transaction → submit to the in-process executor → 202. Nothing is stored
for a refused upload. At most one active job per document (analyze H3): re-upload / retry / delete while a job is
queued or processing → 409 `document.processing` (checked under a row lock and backstopped by the partial unique index).
Authz: RBAC first (403), then the KB's access policy (404 byte-identical to absent if not usable; 403 if usable but not
manageable). Internal fields (storage key, content hash, dedup key) are never returned.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid

from fastapi import APIRouter, Depends, File, Header, Request, Response, UploadFile
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.access_policy import PolicyPrincipal, can_use
from api.db import tenant_session
from api.deps import (Principal, access_principal, enforce_spend_cap, ensure_manage, ensure_use, idempotency_guard,
                      rate_limit, require_permission, resolve_workspace, session_principal, tenant_db)
from api.embedding_policy import provider_for
from api.errors import DocumentProcessing, InputInvalid, JobNotRetryable, KbArchived, NotFound, UploadTooLarge
from api.ingest_executor import get_executor
from api.models import IngestionJob, KbDocument, KnowledgeBase
from api.pagination import Page, clamp_limit
from api.schemas.knowledge import (DocumentOut, IngestionJobOut, JobProgress, ReingestAccepted, UploadAccepted)
from api.settings import get_settings
from api.storage import get_storage
from ingestion.extract import ExtractError, sniff

router = APIRouter(prefix="/api/v1", tags=["knowledge"])

_ACTIVE = ("queued", "processing")
_READ_CHUNK = 64 * 1024
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def _kb_not_found(kb_id: str) -> NotFound:
    return NotFound("knowledge base not found", detail={"kb_id": kb_id})


def _uuid_or_none(v: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(v)
    except (ValueError, TypeError):
        return None


async def _load_kb(session: AsyncSession, kb_id: str) -> KnowledgeBase:
    kid = _uuid_or_none(kb_id)
    k = (await session.execute(select(KnowledgeBase).where(KnowledgeBase.id == kid))).scalar_one_or_none() if kid else None
    if k is None:
        raise _kb_not_found(kb_id)
    return k


def _doc_out(d: KbDocument) -> DocumentOut:
    return DocumentOut(id=str(d.id), kb_id=str(d.kb_id), name=d.name, media_type=d.media_type, size_bytes=d.size_bytes,
                       status=d.status, chunk_count=d.chunk_count, live=d.active_generation > 0, error=d.error,
                       injection_flags=dict(d.injection_flags or {}),
                       created_at=d.created_at, updated_at=d.updated_at)


def _job_out(j: IngestionJob) -> IngestionJobOut:
    return IngestionJobOut(
        id=str(j.id), source="upload", kb=str(j.kb_id), status=j.status, docs=1, chunks=j.progress_done,
        kb_id=str(j.kb_id), document_id=str(j.document_id), kind=j.kind,
        progress=JobProgress(done=j.progress_done, total=j.progress_total), skipped_unchanged=j.skipped_unchanged,
        cost_usd=f"{j.cost_usd or 0:.6f}", cost_is_estimate=j.cost_is_estimate, error=j.error,
        created_at=j.created_at, started_at=j.started_at, finished_at=j.finished_at)


def _safe_name(raw: str | None) -> str:
    base = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = _CTRL.sub("", unicodedata.normalize("NFC", base)).strip()
    if not base or base in (".", ".."):
        raise InputInvalid("a file name is required", detail={"code": "missing_name"})
    return base[:255]


async def _read_capped(file: UploadFile, limit: int) -> bytes:
    """Read the upload in 64 KiB pieces and refuse the moment it exceeds the cap (spooled by Starlette; we never
    hold more than limit + one piece in memory)."""
    buf = bytearray()
    while True:
        piece = await file.read(_READ_CHUNK)
        if not piece:
            return bytes(buf)
        buf.extend(piece)
        if len(buf) > limit:
            raise UploadTooLarge(f"file exceeds the {limit}-byte upload limit", detail={"max_bytes": limit})


async def _require_embedding_key(session: AsyncSession, ws: str, model: str) -> None:
    """Fail closed BEFORE storing anything (feature 014 rule): a key-based provider needs a resolvable workspace key.
    Bedrock (customer data) uses the process AWS credential chain — no per-workspace key."""
    provider = provider_for(model)
    if provider and provider != "bedrock":
        from api.provider_keys import resolve_provider_key
        await resolve_provider_key(session, ws, provider)       # raises ConfigError (503) — never returns the key here


async def _has_active_job(s: AsyncSession, doc_id: uuid.UUID) -> bool:
    return bool((await s.execute(select(func.count()).select_from(IngestionJob).where(
        IngestionJob.document_id == doc_id, IngestionJob.status.in_(_ACTIVE)))).scalar_one())


# ------------------------------------------------------------------------------------------------- upload


@router.post("/knowledge-bases/{kb_id}/documents", status_code=202,
             dependencies=[Depends(require_permission("knowledge.manage")), Depends(rate_limit("ingest")),
                           Depends(enforce_spend_cap)])
async def upload_document(kb_id: str, request: Request, file: UploadFile = File(...),
                          ws: str = Depends(resolve_workspace), session: AsyncSession = Depends(tenant_db),
                          principal: Principal = Depends(session_principal),
                          ap: PolicyPrincipal = Depends(access_principal),
                          idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> UploadAccepted:
    kb = await _load_kb(session, kb_id)
    ensure_manage(kb.access_policy, ap, not_found=_kb_not_found(kb_id))
    if kb.archived_at is not None:
        raise KbArchived("the knowledge base is archived", detail={"kb_id": kb_id})
    limit = get_settings().ingest_max_file_bytes
    data = await _read_capped(file, limit)
    if not data:
        raise InputInvalid("the file is empty", detail={"code": "empty_file"})
    name = _safe_name(file.filename)
    try:
        media_type = sniff(name, data)
    except ExtractError as exc:
        raise InputInvalid(exc.message or "the file was refused", detail={"code": exc.code}) from None
    await _require_embedding_key(session, ws, kb.embedding_model)
    digest = hashlib.sha256(data).hexdigest()

    storage = get_storage()
    old_key: str | None = None
    async with idempotency_guard(ws, idempotency_key):
        async with tenant_session(ws) as s:                    # own txn: committed before the executor reads it
            doc = (await s.execute(select(KbDocument).where(KbDocument.kb_id == kb.id, KbDocument.name == name)
                                   .with_for_update())).scalar_one_or_none()
            if doc is not None:
                if await _has_active_job(s, doc.id):
                    raise DocumentProcessing("this document is still being processed", detail={"document_id": str(doc.id)})
                old_key = doc.storage_key
                doc.media_type, doc.size_bytes, doc.content_hash = media_type, len(data), digest
                doc.status, doc.error, doc.updated_at = "queued", None, func.now()
            else:
                doc = KbDocument(workspace_id=kb.workspace_id, kb_id=kb.id, name=name, media_type=media_type,
                                 size_bytes=len(data), content_hash=digest, storage_key="pending", status="queued",
                                 created_by=principal.subject)
                s.add(doc)
                try:
                    await s.flush()
                except IntegrityError:
                    raise DocumentProcessing("this document is already being uploaded", detail={"name": name}) from None
            key = f"ws/{ws}/kb/{kb.id}/doc/{doc.id}/{digest}"
            doc.storage_key = key
            job = IngestionJob(workspace_id=kb.workspace_id, kb_id=kb.id, document_id=doc.id, kind="upload",
                               status="queued", created_by=principal.subject)
            s.add(job)
            try:
                await s.flush()
            except IntegrityError:                              # lost the race on the one-active-job index (H3)
                raise DocumentProcessing("this document is still being processed", detail={"document_id": str(doc.id)}) from None
            await storage.put(key, data)                        # before commit: a job never points at a missing object
            await s.refresh(doc)
            await s.refresh(job)
            doc_out, job_out, job_id = _doc_out(doc), _job_out(job), str(job.id)
    if old_key and old_key != key:
        try:
            await storage.delete(old_key)                       # superseded bytes; failures are swept later (H5)
        except Exception:  # noqa: BLE001
            pass
    get_executor().submit(job_id, ws)
    return UploadAccepted(document=doc_out, job=job_out)


# ------------------------------------------------------------------------------------------------- documents


@router.get("/knowledge-bases/{kb_id}/documents", dependencies=[Depends(require_permission("view"))])
async def list_documents(kb_id: str, ws: str = Depends(resolve_workspace), session: AsyncSession = Depends(tenant_db),
                         ap: PolicyPrincipal = Depends(access_principal), status: str | None = None,
                         limit: int | None = None) -> Page[DocumentOut]:
    kb = await _load_kb(session, kb_id)
    ensure_use(kb.access_policy, ap, not_found=_kb_not_found(kb_id))
    stmt = select(KbDocument).where(KbDocument.kb_id == kb.id).order_by(KbDocument.created_at.desc(), KbDocument.id)
    if status:
        stmt = stmt.where(KbDocument.status == status)
    rows = (await session.execute(stmt.limit(clamp_limit(limit)))).scalars().all()
    return Page[DocumentOut](items=[_doc_out(d) for d in rows], next_cursor=None)


async def _load_doc(session: AsyncSession, kb: KnowledgeBase, doc_id: str) -> KbDocument:
    did = _uuid_or_none(doc_id)
    d = (await session.execute(select(KbDocument).where(KbDocument.id == did, KbDocument.kb_id == kb.id)
                               .with_for_update())).scalar_one_or_none() if did else None
    if d is None:
        raise NotFound("document not found", detail={"document_id": doc_id})
    return d


@router.get("/knowledge-bases/{kb_id}/documents/{doc_id}", dependencies=[Depends(require_permission("view"))])
async def get_document(kb_id: str, doc_id: str, ws: str = Depends(resolve_workspace),
                       session: AsyncSession = Depends(tenant_db),
                       ap: PolicyPrincipal = Depends(access_principal)) -> DocumentOut:
    kb = await _load_kb(session, kb_id)
    ensure_use(kb.access_policy, ap, not_found=_kb_not_found(kb_id))
    return _doc_out(await _load_doc(session, kb, doc_id))


@router.delete("/knowledge-bases/{kb_id}/documents/{doc_id}", status_code=204,
               dependencies=[Depends(require_permission("knowledge.manage")), Depends(rate_limit("default"))])
async def delete_document(kb_id: str, doc_id: str, ws: str = Depends(resolve_workspace),
                          ap: PolicyPrincipal = Depends(access_principal)) -> Response:
    async with tenant_session(ws) as s:                         # own txn: object deleted only after commit
        kb = await _load_kb(s, kb_id)
        ensure_manage(kb.access_policy, ap, not_found=_kb_not_found(kb_id))
        doc = await _load_doc(s, kb, doc_id)
        if await _has_active_job(s, doc.id):
            raise DocumentProcessing("this document is still being processed", detail={"document_id": doc_id})
        key = doc.storage_key
        await s.delete(doc)                                     # chunks + jobs cascade
        await s.execute(update(KnowledgeBase).where(KnowledgeBase.id == kb.id)
                        .values(index_version=KnowledgeBase.index_version + 1))
    try:
        await get_storage().delete(key)
    except Exception:  # noqa: BLE001 — best-effort; orphan sweep (H5) removes leftovers
        pass
    return Response(status_code=204)


async def _enqueue(s: AsyncSession, doc: KbDocument, kind: str, subject: str) -> IngestionJob:
    job = IngestionJob(workspace_id=doc.workspace_id, kb_id=doc.kb_id, document_id=doc.id, kind=kind, status="queued",
                       created_by=subject)
    s.add(job)
    doc.status, doc.updated_at = "queued", func.now()
    try:
        await s.flush()
    except IntegrityError:
        raise DocumentProcessing("this document is still being processed", detail={"document_id": str(doc.id)}) from None
    await s.refresh(job)
    return job


@router.post("/knowledge-bases/{kb_id}/documents/{doc_id}/retry", status_code=202,
             dependencies=[Depends(require_permission("knowledge.manage")), Depends(rate_limit("ingest")),
                           Depends(enforce_spend_cap)])
async def retry_document(kb_id: str, doc_id: str, ws: str = Depends(resolve_workspace),
                         principal: Principal = Depends(session_principal),
                         ap: PolicyPrincipal = Depends(access_principal)) -> UploadAccepted:
    async with tenant_session(ws) as s:
        kb = await _load_kb(s, kb_id)
        ensure_manage(kb.access_policy, ap, not_found=_kb_not_found(kb_id))
        doc = await _load_doc(s, kb, doc_id)
        if await _has_active_job(s, doc.id):
            raise DocumentProcessing("this document is still being processed", detail={"document_id": doc_id})
        latest = (await s.execute(select(IngestionJob).where(IngestionJob.document_id == doc.id)
                                  .order_by(IngestionJob.created_at.desc()).limit(1))).scalar_one_or_none()
        if latest is None or latest.status != "failed" or not (latest.error or {}).get("retryable"):
            raise JobNotRetryable("the latest job for this document is not a retryable failure",
                                  detail={"document_id": doc_id})
        job = await _enqueue(s, doc, "retry", principal.subject)
        await s.refresh(doc)
        out = UploadAccepted(document=_doc_out(doc), job=_job_out(job))
    get_executor().submit(out.job.id, ws)
    return out


@router.post("/knowledge-bases/{kb_id}/reingest", status_code=202,
             dependencies=[Depends(require_permission("knowledge.manage")), Depends(rate_limit("ingest")),
                           Depends(enforce_spend_cap)])
async def reingest_kb(kb_id: str, ws: str = Depends(resolve_workspace),
                      principal: Principal = Depends(session_principal),
                      ap: PolicyPrincipal = Depends(access_principal)) -> ReingestAccepted:
    job_ids: list[str] = []
    skipped = 0
    async with tenant_session(ws) as s:
        kb = await _load_kb(s, kb_id)
        ensure_manage(kb.access_policy, ap, not_found=_kb_not_found(kb_id))
        if kb.archived_at is not None:
            raise KbArchived("the knowledge base is archived", detail={"kb_id": kb_id})
        docs = (await s.execute(select(KbDocument).where(KbDocument.kb_id == kb.id).with_for_update())).scalars().all()
        for doc in docs:
            if await _has_active_job(s, doc.id):
                skipped += 1
                continue
            job_ids.append(str((await _enqueue(s, doc, "reingest", principal.subject)).id))
    for jid in job_ids:
        get_executor().submit(jid, ws)
    return ReingestAccepted(enqueued=len(job_ids), skipped_active=skipped)


# ------------------------------------------------------------------------------------------------- jobs


@router.get("/ingestion-jobs", dependencies=[Depends(require_permission("view"))])
async def list_ingestion_jobs(ws: str = Depends(resolve_workspace), session: AsyncSession = Depends(tenant_db),
                              ap: PolicyPrincipal = Depends(access_principal), kb_id: str | None = None,
                              status: str | None = None, limit: int | None = None) -> Page[IngestionJobOut]:
    """Real jobs (feature 024), only for KBs the caller can USE (no existence leak)."""
    stmt = (select(IngestionJob, KnowledgeBase.access_policy)
            .join(KnowledgeBase, KnowledgeBase.id == IngestionJob.kb_id)
            .order_by(IngestionJob.created_at.desc(), IngestionJob.id))
    if kb_id is not None:
        kid = _uuid_or_none(kb_id)
        if kid is None:
            return Page[IngestionJobOut](items=[], next_cursor=None)
        stmt = stmt.where(IngestionJob.kb_id == kid)
    if status:
        stmt = stmt.where(IngestionJob.status == status)
    rows = (await session.execute(stmt.limit(clamp_limit(limit)))).all()
    return Page[IngestionJobOut](items=[_job_out(j) for j, pol in rows if can_use(pol, ap)], next_cursor=None)


@router.get("/ingestion-jobs/{job_id}", dependencies=[Depends(require_permission("view"))])
async def get_ingestion_job(job_id: str, ws: str = Depends(resolve_workspace), session: AsyncSession = Depends(tenant_db),
                            ap: PolicyPrincipal = Depends(access_principal)) -> IngestionJobOut:
    jid = _uuid_or_none(job_id)
    row = (await session.execute(select(IngestionJob, KnowledgeBase.access_policy)
                                 .join(KnowledgeBase, KnowledgeBase.id == IngestionJob.kb_id)
                                 .where(IngestionJob.id == jid))).first() if jid else None
    if row is None or not can_use(row[1], ap):
        raise NotFound("ingestion job not found", detail={"job_id": job_id})
    return _job_out(row[0])
