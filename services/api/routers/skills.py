"""Skills catalog router (feature 006, US1).

Workspace-scoped CRUD + soft-archive/restore + publish. Publish stamps an **unsigned provenance stub** (who/when)
— never a cryptographic signature (FR-004). Editing a published skill's content reverts it to `draft` and flags
bound agents for re-eval (FR-002/FR-004). xmin ETag concurrency (D7); idempotent create (D8).
"""
from __future__ import annotations

import datetime
import hashlib
import json as _json
import uuid

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy import select, text, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api import drift
from api.access_policy import PolicyPrincipal, can_use
from api.deps import (access_principal, ensure_manage, ensure_use, require_permission, idempotency_guard,
                      rate_limit, resolve_workspace, session_principal, tenant_db)
from api.deps import Principal
from api.errors import ConflictError, ConflictStale, InputInvalid, NotFound
from api.models import Skill
from api.pagination import Page, clamp_limit, decode_cursor, encode_cursor
from api.schemas.access_policy import policy_for_store, policy_out, stamp_private_owner, validate_access_policy
from api.schemas.skills import SkillCreate, SkillOut, SkillPatch

router = APIRouter(prefix="/api/v1/skills", tags=["skills"])


def _content_hash(instructions: str, resources: list) -> str:
    blob = _json.dumps({"i": instructions, "r": resources}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _skill_out(s: Skill) -> SkillOut:
    return SkillOut(id=str(s.id), slug=s.slug, name=s.name, description=s.description,
                    instructions=s.instructions, resources=s.resources, content_hash=s.content_hash,
                    status=s.status, provenance=s.provenance, archived=s.archived_at is not None,
                    created_at=s.created_at, access_policy=policy_out(s.access_policy))


async def _etag(session: AsyncSession, row_id: str) -> str:
    row = (await session.execute(text("select xmin::text as etag from skills where id = :id"),
                                 {"id": row_id})).first()
    return row.etag if row else ""


async def _load(session: AsyncSession, skill_id: str, *, for_update: bool = False) -> Skill | None:
    try:
        sid = uuid.UUID(skill_id)
    except (ValueError, TypeError):
        return None
    stmt = select(Skill).where(Skill.id == sid)
    if for_update:
        stmt = stmt.with_for_update()        # lock the row so the xmin compare+write is race-free (F2)
    return (await session.execute(stmt)).scalar_one_or_none()


@router.post("", status_code=201, dependencies=[Depends(require_permission("skill.manage")), Depends(rate_limit("default"))])
async def create_skill(
    body: SkillCreate, response: Response, ws: str = Depends(resolve_workspace),
    session: AsyncSession = Depends(tenant_db),
    principal: Principal = Depends(session_principal),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> SkillOut:
    access_policy = policy_for_store(body.access_policy)      # feature 013: validate → input.invalid; default workspace
    access_policy = stamp_private_owner(access_policy, principal.subject)   # FR-012: private → stamp creator as owner
    async with idempotency_guard(ws, idempotency_key):
        s = Skill(workspace_id=uuid.UUID(ws), slug=body.slug, name=body.name, description=body.description,
                  instructions=body.instructions, resources=body.resources,
                  content_hash=_content_hash(body.instructions, body.resources), status="draft",
                  access_policy=access_policy)
        session.add(s)
        try:
            await session.flush()
        except IntegrityError as exc:                       # F3: UNIQUE(workspace_id, slug) → 409, not 500
            raise ConflictError("a skill with this slug already exists in this workspace",
                                detail={"field": "slug"}) from exc
        response.headers["ETag"] = await _etag(session, str(s.id))
        return _skill_out(s)


@router.get("", dependencies=[Depends(require_permission("view"))])
async def list_skills(ws: str = Depends(resolve_workspace), session: AsyncSession = Depends(tenant_db),
                      ap: PolicyPrincipal = Depends(access_principal),   # feature 013
                      limit: int | None = None, cursor: str | None = None,
                      include_archived: bool = False) -> Page[SkillOut]:
    n = clamp_limit(limit)
    stmt = select(Skill).order_by(Skill.created_at.desc(), Skill.id.desc())
    if not include_archived:
        stmt = stmt.where(Skill.archived_at.is_(None))
    cur = decode_cursor(cursor)
    if cur is not None:
        sort_value, last_id = cur
        stmt = stmt.where(tuple_(Skill.created_at, Skill.id)
                          < tuple_(datetime.datetime.fromisoformat(sort_value), uuid.UUID(last_id)))
    rows = list((await session.execute(stmt.limit(n + 1))).scalars().all())
    next_cursor = None
    if len(rows) > n:
        last = rows[n - 1]
        next_cursor = encode_cursor(last.created_at.isoformat(), str(last.id))
        rows = rows[:n]
    rows = [s for s in rows if can_use(s.access_policy, ap)]   # feature 013: omit non-usable (P5 no-leak)
    return Page[SkillOut](items=[_skill_out(s) for s in rows], next_cursor=next_cursor)


@router.get("/{skill_id}", dependencies=[Depends(require_permission("view"))])
async def get_skill(skill_id: str, response: Response, ws: str = Depends(resolve_workspace),
                    session: AsyncSession = Depends(tenant_db),
                    ap: PolicyPrincipal = Depends(access_principal)) -> SkillOut:   # feature 013
    s = await _load(session, skill_id)
    if s is None:
        raise NotFound("skill not found", detail={"skill_id": skill_id})
    # feature 013: deny USE as a 404 byte-identical to the absent-row 404 (P5 no-leak).
    ensure_use(s.access_policy, ap, not_found=NotFound("skill not found", detail={"skill_id": skill_id}))
    response.headers["ETag"] = await _etag(session, skill_id)
    return _skill_out(s)


@router.patch("/{skill_id}", dependencies=[Depends(require_permission("skill.manage")), Depends(rate_limit("default"))])
async def patch_skill(
    skill_id: str, body: SkillPatch, response: Response, ws: str = Depends(resolve_workspace),
    session: AsyncSession = Depends(tenant_db),
    principal: Principal = Depends(session_principal),
    ap: PolicyPrincipal = Depends(access_principal),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> SkillOut:
    if not if_match:
        raise InputInvalid("If-Match header is required for edits", detail={"header": "If-Match"})
    try:
        etag_val = int(if_match)
    except (TypeError, ValueError):
        raise ConflictStale("malformed If-Match ETag", detail={"skill_id": skill_id})
    s = await _load(session, skill_id, for_update=True)
    if s is None:
        raise NotFound("skill not found", detail={"skill_id": skill_id})
    # feature 013: MANAGE gate (404-then-403) before the ETag compare (no-leak on non-usable).
    ensure_manage(s.access_policy, ap, not_found=NotFound("skill not found", detail={"skill_id": skill_id}))
    if await _etag(session, skill_id) != str(etag_val):
        raise ConflictStale("stale If-Match ETag; the skill was modified concurrently",
                            detail={"skill_id": skill_id})
    content_changed = False
    if body.name is not None:
        s.name = body.name
    if body.description is not None:
        s.description = body.description
    if body.instructions is not None and body.instructions != s.instructions:
        s.instructions = body.instructions
        content_changed = True
    if body.resources is not None and body.resources != s.resources:
        s.resources = body.resources
        content_changed = True
    if body.access_policy is not None:                        # feature 013 (not a content/drift change)
        s.access_policy = stamp_private_owner(validate_access_policy(body.access_policy), principal.subject)
    if content_changed:
        s.content_hash = _content_hash(s.instructions, s.resources)
        # A content edit to a PUBLISHED skill reverts it to draft (must re-publish) + flags bound agents (FR-004/FR-002).
        if s.status == "published":
            s.status = "draft"
            s.provenance = None
        await session.flush()
        await drift.flag_bound_agents(session, "skill", skill_id)
    else:
        await session.flush()
    response.headers["ETag"] = await _etag(session, skill_id)
    return _skill_out(s)


@router.post("/{skill_id}/publish", dependencies=[Depends(require_permission("skill.manage")), Depends(rate_limit("default"))])
async def publish_skill(skill_id: str, ws: str = Depends(resolve_workspace),
                        session: AsyncSession = Depends(tenant_db),
                        principal: Principal = Depends(session_principal),
                        ap: PolicyPrincipal = Depends(access_principal)) -> SkillOut:
    s = await _load(session, skill_id)
    if s is None:
        raise NotFound("skill not found", detail={"skill_id": skill_id})
    ensure_manage(s.access_policy, ap, not_found=NotFound("skill not found", detail={"skill_id": skill_id}))
    if s.archived_at is not None:                           # F5: cannot publish an archived skill (restore first)
        raise InputInvalid("cannot publish an archived skill; restore it first",
                           detail={"skill_id": skill_id})
    s.status = "published"
    # Unsigned provenance stub (FR-004) — records who/when, NOT a cryptographic signature.
    s.provenance = {"by": principal.subject, "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "signed": False}
    await session.flush()
    return _skill_out(s)


@router.post("/{skill_id}/archive", dependencies=[Depends(require_permission("skill.manage")), Depends(rate_limit("default"))])
async def archive_skill(skill_id: str, ws: str = Depends(resolve_workspace),
                        session: AsyncSession = Depends(tenant_db),
                        ap: PolicyPrincipal = Depends(access_principal)) -> SkillOut:
    s = await _load(session, skill_id)
    if s is None:
        raise NotFound("skill not found", detail={"skill_id": skill_id})
    ensure_manage(s.access_policy, ap, not_found=NotFound("skill not found", detail={"skill_id": skill_id}))
    s.archived_at = datetime.datetime.now(datetime.timezone.utc)
    await session.flush()
    return _skill_out(s)


@router.post("/{skill_id}/restore", dependencies=[Depends(require_permission("skill.manage")), Depends(rate_limit("default"))])
async def restore_skill(skill_id: str, ws: str = Depends(resolve_workspace),
                        session: AsyncSession = Depends(tenant_db),
                        ap: PolicyPrincipal = Depends(access_principal)) -> SkillOut:
    s = await _load(session, skill_id)
    if s is None:
        raise NotFound("skill not found", detail={"skill_id": skill_id})
    ensure_manage(s.access_policy, ap, not_found=NotFound("skill not found", detail={"skill_id": skill_id}))
    s.archived_at = None
    await session.flush()
    return _skill_out(s)
