"""Knowledge-base metadata router (feature 006, US3).

Workspace-scoped CRUD + soft-archive/restore over the existing `knowledge_bases` table. Editing content/retrieval
config (embedding_model / chunk_config) flags bound agents for re-eval (FR-002). Real ingestion is deferred to
M7 — `GET /ingestion-jobs` returns an honest empty/deferred state (FR-009). xmin ETag concurrency; idempotent create.
"""
from __future__ import annotations

import datetime
import uuid

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy import select, text, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api import drift
from api.deps import require_permission, idempotency_guard, rate_limit, resolve_workspace, tenant_db
from api.errors import ConflictError, ConflictStale, InputInvalid, NotFound
from api.models import KnowledgeBase
from api.pagination import Page, clamp_limit, decode_cursor, encode_cursor
from api.schemas.knowledge import IngestionJobOut, KnowledgeBaseCreate, KnowledgeBaseOut, KnowledgeBasePatch

router = APIRouter(prefix="/api/v1", tags=["knowledge"])


def _kb_out(k: KnowledgeBase) -> KnowledgeBaseOut:
    return KnowledgeBaseOut(id=str(k.id), slug=k.slug, name=k.name, embedding_model=k.embedding_model,
                            embedding_dim=k.embedding_dim, chunk_config=k.chunk_config,
                            index_version=k.index_version, ingestion_status="deferred",
                            archived=k.archived_at is not None, created_at=k.created_at)


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


@router.post("/knowledge-bases", status_code=201, dependencies=[Depends(require_permission("knowledge.manage"))])
async def create_kb(body: KnowledgeBaseCreate, response: Response, ws: str = Depends(resolve_workspace),
                    session: AsyncSession = Depends(tenant_db),
                    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
                    _rl: None = Depends(rate_limit)) -> KnowledgeBaseOut:
    async with idempotency_guard(ws, idempotency_key):
        k = KnowledgeBase(workspace_id=uuid.UUID(ws), slug=body.slug, name=body.name,
                          embedding_model=body.embedding_model, embedding_dim=body.embedding_dim,
                          chunk_config=body.chunk_config)
        session.add(k)
        try:
            await session.flush()
        except IntegrityError as exc:
            raise ConflictError("a knowledge base with this slug already exists in this workspace",
                                detail={"field": "slug"}) from exc
        response.headers["ETag"] = await _etag(session, str(k.id))
        return _kb_out(k)


@router.get("/knowledge-bases", dependencies=[Depends(require_permission("view"))])
async def list_kb(ws: str = Depends(resolve_workspace), session: AsyncSession = Depends(tenant_db),
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
    return Page[KnowledgeBaseOut](items=[_kb_out(k) for k in rows], next_cursor=next_cursor)


@router.get("/knowledge-bases/{kb_id}", dependencies=[Depends(require_permission("view"))])
async def get_kb(kb_id: str, response: Response, ws: str = Depends(resolve_workspace),
                 session: AsyncSession = Depends(tenant_db)) -> KnowledgeBaseOut:
    k = await _load(session, kb_id)
    if k is None:
        raise NotFound("knowledge base not found", detail={"kb_id": kb_id})
    response.headers["ETag"] = await _etag(session, kb_id)
    return _kb_out(k)


@router.patch("/knowledge-bases/{kb_id}", dependencies=[Depends(require_permission("knowledge.manage"))])
async def patch_kb(kb_id: str, body: KnowledgeBasePatch, response: Response,
                   ws: str = Depends(resolve_workspace), session: AsyncSession = Depends(tenant_db),
                   if_match: str | None = Header(default=None, alias="If-Match"),
                   _rl: None = Depends(rate_limit)) -> KnowledgeBaseOut:
    if not if_match:
        raise InputInvalid("If-Match header is required for edits", detail={"header": "If-Match"})
    try:
        etag_val = int(if_match)
    except (TypeError, ValueError):
        raise ConflictStale("malformed If-Match ETag", detail={"kb_id": kb_id})
    k = await _load(session, kb_id, for_update=True)
    if k is None:
        raise NotFound("knowledge base not found", detail={"kb_id": kb_id})
    if await _etag(session, kb_id) != str(etag_val):
        raise ConflictStale("stale If-Match ETag; the knowledge base was modified concurrently",
                            detail={"kb_id": kb_id})
    # content/retrieval surface = embedding_model + chunk_config (drift trigger, FR-002); name is cosmetic.
    content_before = (k.embedding_model, k.chunk_config)
    if body.name is not None:
        k.name = body.name
    if body.embedding_model is not None:
        k.embedding_model = body.embedding_model
    if body.chunk_config is not None:
        k.chunk_config = body.chunk_config
    await session.flush()
    if (k.embedding_model, k.chunk_config) != content_before:
        await drift.flag_bound_agents(session, "kb", kb_id)
    response.headers["ETag"] = await _etag(session, kb_id)
    return _kb_out(k)


@router.post("/knowledge-bases/{kb_id}/archive", dependencies=[Depends(require_permission("knowledge.manage"))])
async def archive_kb(kb_id: str, ws: str = Depends(resolve_workspace),
                     session: AsyncSession = Depends(tenant_db), _rl: None = Depends(rate_limit)) -> KnowledgeBaseOut:
    k = await _load(session, kb_id)
    if k is None:
        raise NotFound("knowledge base not found", detail={"kb_id": kb_id})
    k.archived_at = datetime.datetime.now(datetime.timezone.utc)
    await session.flush()
    return _kb_out(k)


@router.post("/knowledge-bases/{kb_id}/restore", dependencies=[Depends(require_permission("knowledge.manage"))])
async def restore_kb(kb_id: str, ws: str = Depends(resolve_workspace),
                     session: AsyncSession = Depends(tenant_db), _rl: None = Depends(rate_limit)) -> KnowledgeBaseOut:
    k = await _load(session, kb_id)
    if k is None:
        raise NotFound("knowledge base not found", detail={"kb_id": kb_id})
    k.archived_at = None
    await session.flush()
    return _kb_out(k)


@router.get("/ingestion-jobs", dependencies=[Depends(require_permission("view"))])
async def list_ingestion_jobs(ws: str = Depends(resolve_workspace),
                              session: AsyncSession = Depends(tenant_db)) -> Page[IngestionJobOut]:
    """Honest deferred (FR-009): real ingestion (documents/embeddings) is M7. No fabricated jobs are returned."""
    return Page[IngestionJobOut](items=[], next_cursor=None)
