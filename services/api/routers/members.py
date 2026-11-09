"""Member roster router (feature 006, US4).

View/add/remove members and set a STORED console role within the active workspace. Roles are not enforced across
endpoints here (FR-012); there is no invite/email flow or IdP sync. Removing a member hard-deletes the roster row
(Data Handling §members).
"""
from __future__ import annotations

import datetime
import uuid

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import require_permission, rate_limit, resolve_workspace, tenant_db
from api.errors import ConflictError, NotFound
from api.models import Member
from api.pagination import Page, clamp_limit, decode_cursor, encode_cursor
from api.schemas.members import MemberCreate, MemberOut, MemberPatch

router = APIRouter(prefix="/api/v1/members", tags=["members"])


def _member_out(m: Member) -> MemberOut:
    return MemberOut(id=str(m.id), subject=m.subject, display_name=m.display_name,
                     email=m.email, console_role=m.console_role, teams=list(m.teams or []))


async def _load(session: AsyncSession, member_id: str) -> Member | None:
    try:
        mid = uuid.UUID(member_id)
    except (ValueError, TypeError):
        return None
    return (await session.execute(select(Member).where(Member.id == mid))).scalar_one_or_none()


@router.get("", dependencies=[Depends(require_permission("view"))])
async def list_members(ws: str = Depends(resolve_workspace), session: AsyncSession = Depends(tenant_db),
                       limit: int | None = None, cursor: str | None = None) -> Page[MemberOut]:
    n = clamp_limit(limit)
    stmt = select(Member).order_by(Member.created_at.desc(), Member.id.desc())
    cur = decode_cursor(cursor)
    if cur is not None:
        sort_value, last_id = cur
        stmt = stmt.where(tuple_(Member.created_at, Member.id)
                          < tuple_(datetime.datetime.fromisoformat(sort_value), uuid.UUID(last_id)))
    rows = list((await session.execute(stmt.limit(n + 1))).scalars().all())
    next_cursor = None
    if len(rows) > n:
        last = rows[n - 1]
        next_cursor = encode_cursor(last.created_at.isoformat(), str(last.id))
        rows = rows[:n]
    return Page[MemberOut](items=[_member_out(m) for m in rows], next_cursor=next_cursor)


@router.post("", status_code=201, dependencies=[Depends(require_permission("member.manage"))])
async def add_member(body: MemberCreate, ws: str = Depends(resolve_workspace),
                     session: AsyncSession = Depends(tenant_db), _rl: None = Depends(rate_limit)) -> MemberOut:
    m = Member(workspace_id=uuid.UUID(ws), subject=body.subject, display_name=body.display_name,
               email=body.email, console_role=body.console_role, teams=list(body.teams))
    session.add(m)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ConflictError("this subject is already a member of the workspace",
                            detail={"field": "subject"}) from exc
    return _member_out(m)


@router.patch("/{member_id}", dependencies=[Depends(require_permission("member.manage"))])
async def patch_member(member_id: str, body: MemberPatch, ws: str = Depends(resolve_workspace),
                       session: AsyncSession = Depends(tenant_db), _rl: None = Depends(rate_limit)) -> MemberOut:
    m = await _load(session, member_id)
    if m is None:
        raise NotFound("member not found", detail={"member_id": member_id})
    for f in ("display_name", "email", "console_role", "teams"):
        v = getattr(body, f)
        if v is not None:
            setattr(m, f, v)
    await session.flush()
    return _member_out(m)


@router.delete("/{member_id}", status_code=204, dependencies=[Depends(require_permission("member.manage"))])
async def remove_member(member_id: str, response: Response, ws: str = Depends(resolve_workspace),
                        session: AsyncSession = Depends(tenant_db), _rl: None = Depends(rate_limit)) -> Response:
    m = await _load(session, member_id)
    if m is None:
        raise NotFound("member not found", detail={"member_id": member_id})
    await session.delete(m)                                  # hard delete (Data Handling §members)
    await session.flush()
    return Response(status_code=204)
