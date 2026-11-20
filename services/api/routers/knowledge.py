"""Knowledge-base metadata router (feature 006, US3).

Workspace-scoped CRUD + soft-archive/restore over the existing `knowledge_bases` table. Editing content/retrieval
config (embedding_model / chunk_config) flags bound agents for re-eval (FR-002). Real ingestion is deferred to
M7 — feature 024 adds real ingestion: KB reads carry derived document/chunk counts + ingestion status; create/patch
validate the embedding model against the deployment policy (api/embedding_policy.py) and the chunk config; a content
change marks ready documents `stale`. Documents + ingestion-job routes live in api/routers/documents.py.
xmin ETag concurrency; idempotent create.
"""
from __future__ import annotations

import datetime
import uuid

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy import case, func, select, text, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api import drift
from api.embedding_policy import provider_for, validate_embedding_model
from api.access_policy import PolicyPrincipal, can_use
from api.deps import (access_principal, ensure_manage, ensure_use, require_permission, idempotency_guard,
                      Principal, rate_limit, resolve_workspace, session_principal, tenant_db)
from api.errors import ConflictError, ConflictStale, InputInvalid, NotFound
from api.models import IngestionJob, KbDocument, KnowledgeBase
from ingestion.chunk import ChunkConfig
from api.pagination import Page, clamp_limit, decode_cursor, encode_cursor
from api.schemas.access_policy import policy_for_store, policy_out, stamp_private_owner, validate_access_policy
from api.schemas.knowledge import KnowledgeBaseCreate, KnowledgeBaseOut, KnowledgeBasePatch

router = APIRouter(prefix="/api/v1", tags=["knowledge"])


def _derive_status(docs: int, ready: int, stale: int, failed: int, active_jobs: int) -> str:
    """data-model §1: empty → ingesting → stale → partial/failed → ready (first match wins)."""
    if docs == 0:
        return "empty"
    if active_jobs:
        return "ingesting"
    if stale:
        return "stale"
    if failed and ready:
        return "partial"
    if failed and not ready:
        return "failed"
    return "ready"


async def kb_stats(session: AsyncSession, kb_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    """Derived per-KB facts in TWO grouped queries for the whole page (never per-row)."""
    if not kb_ids:
        return {}
    docs = (await session.execute(
        select(KbDocument.kb_id,
               func.count(),
               func.coalesce(func.sum(case((KbDocument.active_generation > 0, KbDocument.chunk_count), else_=0)), 0),
               func.count().filter(KbDocument.status == "ready"),
               func.count().filter(KbDocument.status == "stale"),
               func.count().filter(KbDocument.status == "failed"))
        .where(KbDocument.kb_id.in_(kb_ids)).group_by(KbDocument.kb_id))).all()
    jobs = dict((await session.execute(
        select(IngestionJob.kb_id, func.count())
        .where(IngestionJob.kb_id.in_(kb_ids), IngestionJob.status.in_(("queued", "processing")))
        .group_by(IngestionJob.kb_id))).all())
    out = {kid: {"docs": 0, "chunks": 0, "status": "empty"} for kid in kb_ids}
    for kid, n, chunks, ready, stale, failed in docs:
        out[kid] = {"docs": int(n), "chunks": int(chunks),
                    "status": _derive_status(int(n), int(ready), int(stale), int(failed), int(jobs.get(kid, 0)))}
    return out


def _kb_out(k: KnowledgeBase, stats: dict | None = None) -> KnowledgeBaseOut:
    st = stats or {"docs": 0, "chunks": 0, "status": "empty"}
    return KnowledgeBaseOut(id=str(k.id), slug=k.slug, name=k.name, embedding_model=k.embedding_model,
                            embedding_dim=k.embedding_dim, chunk_config=k.chunk_config,
                            index_version=k.index_version, ingestion_status=st["status"],
                            archived=k.archived_at is not None, created_at=k.created_at,
                            access_policy=policy_out(k.access_policy),
                            document_count=st["docs"], chunk_count=st["chunks"],
                            last_ingested_at=k.last_ingested_at, embedding_provider=provider_for(k.embedding_model))


async def _kb_out_one(session: AsyncSession, k: KnowledgeBase) -> KnowledgeBaseOut:
    return _kb_out(k, (await kb_stats(session, [k.id])).get(k.id))


def _valid_chunk_config(cfg: dict) -> dict:
    try:
        ChunkConfig.from_dict(cfg)
    except ValueError as exc:
        raise InputInvalid(str(exc), detail={"field": "chunk_config"}) from None
    return cfg


async def _etag(session: AsyncSession, row_id: str) -> str:
    row = (await session.execute(text("select xmin::text as etag from knowledge_bases where id = :id"),
                                 {"id": row_id})).first()
    return row.etag if row else ""


async def _load(session: AsyncSession, kb_id: str, *, for_update: bool = False) -> KnowledgeBase | None:
    try:
        kid = uuid.UUID(kb_id)
    except (ValueError, TypeError):
        return None
    stmt = select(KnowledgeBase).where(KnowledgeBase.id == kid)
    if for_update:
        stmt = stmt.with_for_update()        # lock the row so the xmin compare+write is race-free (F2)
    return (await session.execute(stmt)).scalar_one_or_none()


@router.post("/knowledge-bases", status_code=201, dependencies=[Depends(require_permission("knowledge.manage")), Depends(rate_limit("default"))])
async def create_kb(body: KnowledgeBaseCreate, response: Response, ws: str = Depends(resolve_workspace),
                    session: AsyncSession = Depends(tenant_db),
                    principal: Principal = Depends(session_principal),
                    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> KnowledgeBaseOut:
    access_policy = policy_for_store(body.access_policy)      # feature 013: validate → input.invalid; default workspace
    access_policy = stamp_private_owner(access_policy, principal.subject)   # FR-012: private → stamp creator as owner
    profile = validate_embedding_model(body.embedding_model, embedding_dim=body.embedding_dim)   # feature 024
    _valid_chunk_config(body.chunk_config)
    async with idempotency_guard(ws, idempotency_key):
        k = KnowledgeBase(workspace_id=uuid.UUID(ws), slug=body.slug, name=body.name,
                          embedding_model=body.embedding_model, embedding_dim=profile.dimension,
                          chunk_config=body.chunk_config, access_policy=access_policy)
        session.add(k)
        try:
            await session.flush()
        except IntegrityError as exc:
            raise ConflictError("a knowledge base with this slug already exists in this workspace",
                                detail={"field": "slug"}) from exc
        response.headers["ETag"] = await _etag(session, str(k.id))
        await session.refresh(k)
        return _kb_out(k)


@router.get("/knowledge-bases", dependencies=[Depends(require_permission("view"))])
async def list_kb(ws: str = Depends(resolve_workspace), session: AsyncSession = Depends(tenant_db),
                  ap: PolicyPrincipal = Depends(access_principal),   # feature 013
                  limit: int | None = None, cursor: str | None = None,
                  include_archived: bool = False) -> Page[KnowledgeBaseOut]:
    n = clamp_limit(limit)
    stmt = select(KnowledgeBase).order_by(KnowledgeBase.created_at.desc(), KnowledgeBase.id.desc())
    if not include_archived:
        stmt = stmt.where(KnowledgeBase.archived_at.is_(None))
    cur = decode_cursor(cursor)
    if cur is not None:
        sort_value, last_id = cur
        stmt = stmt.where(tuple_(KnowledgeBase.created_at, KnowledgeBase.id)
                          < tuple_(datetime.datetime.fromisoformat(sort_value), uuid.UUID(last_id)))
    rows = list((await session.execute(stmt.limit(n + 1))).scalars().all())
    next_cursor = None
    if len(rows) > n:
        last = rows[n - 1]
        next_cursor = encode_cursor(last.created_at.isoformat(), str(last.id))
        rows = rows[:n]
    rows = [k for k in rows if can_use(k.access_policy, ap)]   # feature 013: omit non-usable (P5 no-leak)
    stats = await kb_stats(session, [k.id for k in rows])
    return Page[KnowledgeBaseOut](items=[_kb_out(k, stats.get(k.id)) for k in rows], next_cursor=next_cursor)


@router.get("/knowledge-bases/{kb_id}", dependencies=[Depends(require_permission("view"))])
async def get_kb(kb_id: str, response: Response, ws: str = Depends(resolve_workspace),
                 session: AsyncSession = Depends(tenant_db),
                 ap: PolicyPrincipal = Depends(access_principal)) -> KnowledgeBaseOut:   # feature 013
    k = await _load(session, kb_id)
    if k is None:
        raise NotFound("knowledge base not found", detail={"kb_id": kb_id})
    # feature 013: deny USE as a 404 byte-identical to the absent-row 404 (P5 no-leak).
    ensure_use(k.access_policy, ap, not_found=NotFound("knowledge base not found", detail={"kb_id": kb_id}))
    response.headers["ETag"] = await _etag(session, kb_id)
    return await _kb_out_one(session, k)


@router.patch("/knowledge-bases/{kb_id}", dependencies=[Depends(require_permission("knowledge.manage")), Depends(rate_limit("default"))])
async def patch_kb(kb_id: str, body: KnowledgeBasePatch, response: Response,
                   ws: str = Depends(resolve_workspace), session: AsyncSession = Depends(tenant_db),
                   principal: Principal = Depends(session_principal),
                   ap: PolicyPrincipal = Depends(access_principal),
                   if_match: str | None = Header(default=None, alias="If-Match")) -> KnowledgeBaseOut:
    if not if_match:
        raise InputInvalid("If-Match header is required for edits", detail={"header": "If-Match"})
    try:
        etag_val = int(if_match)
    except (TypeError, ValueError):
        raise ConflictStale("malformed If-Match ETag", detail={"kb_id": kb_id})
    k = await _load(session, kb_id, for_update=True)
    if k is None:
        raise NotFound("knowledge base not found", detail={"kb_id": kb_id})
    # feature 013: MANAGE gate (404-then-403) before the ETag compare (no-leak on non-usable).
    ensure_manage(k.access_policy, ap, not_found=NotFound("knowledge base not found", detail={"kb_id": kb_id}))
    if await _etag(session, kb_id) != str(etag_val):
        raise ConflictStale("stale If-Match ETag; the knowledge base was modified concurrently",
                            detail={"kb_id": kb_id})
    # content/retrieval surface = embedding_model + chunk_config (drift trigger, FR-002); name is cosmetic.
    content_before = (k.embedding_model, k.chunk_config)
    if body.name is not None:
        k.name = body.name
    if body.embedding_model is not None:
        k.embedding_dim = validate_embedding_model(body.embedding_model).dimension   # feature 024
        k.embedding_model = body.embedding_model
    if body.chunk_config is not None:
        k.chunk_config = _valid_chunk_config(body.chunk_config)
    if body.access_policy is not None:                        # feature 013
        k.access_policy = stamp_private_owner(validate_access_policy(body.access_policy), principal.subject)
    await session.flush()
    if (k.embedding_model, k.chunk_config) != content_before:
        await drift.flag_bound_agents(session, "kb", kb_id)
        # feature 024 (FR-031): live chunks were produced with the old model/config — mark them stale (still served
        # until re-ingested; retrieval filters on the frozen embedding_model, so a model change stops matching).
        await session.execute(update(KbDocument).where(KbDocument.kb_id == k.id, KbDocument.status == "ready")
                              .values(status="stale", updated_at=func.now()))
    response.headers["ETag"] = await _etag(session, kb_id)
    return await _kb_out_one(session, k)


@router.post("/knowledge-bases/{kb_id}/archive", dependencies=[Depends(require_permission("knowledge.manage")), Depends(rate_limit("default"))])
async def archive_kb(kb_id: str, ws: str = Depends(resolve_workspace),
                     session: AsyncSession = Depends(tenant_db),
                     ap: PolicyPrincipal = Depends(access_principal)) -> KnowledgeBaseOut:
    k = await _load(session, kb_id)
    if k is None:
        raise NotFound("knowledge base not found", detail={"kb_id": kb_id})
    ensure_manage(k.access_policy, ap, not_found=NotFound("knowledge base not found", detail={"kb_id": kb_id}))
    k.archived_at = datetime.datetime.now(datetime.timezone.utc)
    await session.flush()
    return await _kb_out_one(session, k)


@router.post("/knowledge-bases/{kb_id}/restore", dependencies=[Depends(require_permission("knowledge.manage")), Depends(rate_limit("default"))])
async def restore_kb(kb_id: str, ws: str = Depends(resolve_workspace),
                     session: AsyncSession = Depends(tenant_db),
                     ap: PolicyPrincipal = Depends(access_principal)) -> KnowledgeBaseOut:
    k = await _load(session, kb_id)
    if k is None:
        raise NotFound("knowledge base not found", detail={"kb_id": kb_id})
    ensure_manage(k.access_policy, ap, not_found=NotFound("knowledge base not found", detail={"kb_id": kb_id}))
    k.archived_at = None
    await session.flush()
    return await _kb_out_one(session, k)
