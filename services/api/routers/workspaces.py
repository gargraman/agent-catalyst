"""Workspace administration router (feature 006, US4).

Settings/limits/eval-policy + rename, within the active (authorized) workspace. Workspace-root create/delete is a
platform-admin plane and is out of scope. RBAC enforcement of console roles is deferred to M5 (FR-012).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import rate_limit, resolve_workspace, tenant_db
from api.errors import ConflictStale, InputInvalid, NotFound
from api.models import Workspace, WorkspaceSettings
from api.schemas.workspaces import WorkspaceOut, WorkspacePatch, WorkspaceSettingsOut, WorkspaceSettingsPatch

router = APIRouter(prefix="/api/v1/workspace", tags=["workspace"])


def _settings_out(s: WorkspaceSettings) -> WorkspaceSettingsOut:
    return WorkspaceSettingsOut(workspace_id=str(s.workspace_id), default_provider=s.default_provider,
                                default_model=s.default_model, limits=s.limits, eval_policy=s.eval_policy)


def _defaults_out(ws: str) -> WorkspaceSettingsOut:
    return WorkspaceSettingsOut(workspace_id=ws, default_provider="", default_model="", limits={}, eval_policy={})


async def _etag(session: AsyncSession, ws: str) -> str:
    row = (await session.execute(
        text("select xmin::text as etag from workspace_settings where workspace_id = :id"), {"id": ws})).first()
    return row.etag if row else ""


async def _load_settings(session: AsyncSession, ws: str, *, for_update: bool = False) -> WorkspaceSettings | None:
    stmt = select(WorkspaceSettings).where(WorkspaceSettings.workspace_id == uuid.UUID(ws))
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


@router.get("/settings")
async def get_settings(response: Response, ws: str = Depends(resolve_workspace),
                       session: AsyncSession = Depends(tenant_db)) -> WorkspaceSettingsOut:
    # READ-ONLY (F4): if no settings row exists yet, return in-memory defaults with a sentinel ETag — never write
    # on a GET (would break read idempotency / fail on a read replica). The row is created lazily on first PATCH.
    s = await _load_settings(session, ws)
    if s is None:
        response.headers["ETag"] = "0"
        return _defaults_out(ws)
    response.headers["ETag"] = await _etag(session, ws)
    return _settings_out(s)


@router.patch("/settings")
async def patch_settings(body: WorkspaceSettingsPatch, response: Response,
                         ws: str = Depends(resolve_workspace), session: AsyncSession = Depends(tenant_db),
                         if_match: str | None = Header(default=None, alias="If-Match"),
                         _rl: None = Depends(rate_limit)) -> WorkspaceSettingsOut:
    if not if_match:
        raise InputInvalid("If-Match header is required for edits", detail={"header": "If-Match"})
    try:
        etag_val = int(if_match)
    except (TypeError, ValueError):
        raise ConflictStale("malformed If-Match ETag", detail={"workspace_id": ws})
    s = await _load_settings(session, ws, for_update=True)
    if s is None:
        # First write: create the settings row (the GET returned defaults with ETag "0"). No prior row to
        # conflict with; a concurrent create loses the PK race → IntegrityError → stale conflict.
        s = WorkspaceSettings(workspace_id=uuid.UUID(ws), limits={}, eval_policy={})
        _apply(s, body)
        session.add(s)
        try:
            await session.flush()
        except IntegrityError as exc:
            raise ConflictStale("settings were created concurrently; re-read and retry",
                                detail={"workspace_id": ws}) from exc
    else:
        if await _etag(session, ws) != str(etag_val):
            raise ConflictStale("stale If-Match ETag; settings were modified concurrently",
                                detail={"workspace_id": ws})
        _apply(s, body)
        await session.flush()
    response.headers["ETag"] = await _etag(session, ws)
    return _settings_out(s)


def _apply(s: WorkspaceSettings, body: WorkspaceSettingsPatch) -> None:
    if body.default_provider is not None:
        s.default_provider = body.default_provider
    if body.default_model is not None:
        s.default_model = body.default_model
    if body.limits is not None:
        s.limits = body.limits
    if body.eval_policy is not None:
        s.eval_policy = body.eval_policy


@router.patch("")
async def rename_workspace(body: WorkspacePatch, ws: str = Depends(resolve_workspace),
                           session: AsyncSession = Depends(tenant_db), _rl: None = Depends(rate_limit)) -> WorkspaceOut:
    # Scoped to the active (authorized) workspace only; the workspaces root has no RLS so we match by id explicitly.
    w = await session.get(Workspace, uuid.UUID(ws))
    if w is None:
        raise NotFound("workspace not found", detail={"workspace_id": ws})
    if body.name is not None:
        w.name = body.name
    if body.group_prefix is not None:
        w.group_prefix = body.group_prefix
    await session.flush()
    return WorkspaceOut(id=str(w.id), name=w.name, group_prefix=w.group_prefix)
