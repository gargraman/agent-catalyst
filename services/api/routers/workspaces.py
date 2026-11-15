"""Workspace administration router (feature 006, US4).

Settings/limits/eval-policy + rename, within the active (authorized) workspace. Workspace-root create/delete is a
platform-admin plane and is out of scope. RBAC enforcement of console roles is deferred to M5 (FR-012).
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api import call_log, membership, spend
from api.correlation import correlation_id
from api.db import auth_lookup_session
from api.deps import Principal, require_permission, rate_limit, resolve_workspace, session_principal, tenant_db
from api.errors import ConflictStale, InputInvalid, NotFound
from api.models import Workspace, WorkspaceSettings
from api.settings import get_settings as app_settings   # `get_settings` is a route handler below
from api.schemas.workspaces import (
    TelemetrySettingsIn, TelemetrySettingsOut, WorkspaceMembershipOut, WorkspaceOut, WorkspacePatch,
    WorkspaceSettingsOut, WorkspaceSettingsPatch, WorkspaceSpendOut,
)

_audit = logging.getLogger("api.security")

router = APIRouter(prefix="/api/v1/workspace", tags=["workspace"])

# Discovery lives at the PLURAL path /api/v1/workspaces (distinct from the singular active-workspace admin router
# above). It is the ONLY workspace-scoped-exempt console read: it runs BEFORE a workspace is selected, so it takes
# no X-Workspace-Id and uses `session_principal` (authenticated) rather than `resolve_workspace`.
discovery_router = APIRouter(prefix="/api/v1/workspaces", tags=["workspace"])


@discovery_router.get("")
async def list_my_workspaces(
    principal: Principal = Depends(session_principal),
) -> list[WorkspaceMembershipOut]:
    """The workspaces the signed-in caller may enter — each with the console role they hold. Source for the
    console's workspace switcher. Pre-selection and cross-tenant by nature: resolved from the members store via
    the `member_workspaces()` SECURITY DEFINER function (migration 0011) on an unscoped session, so a caller only
    ever sees workspaces they are a member of and the app role never gains a broad RLS bypass (09 §7). When real
    OIDC replaces the dev provider, membership is populated by the IdP group sync (feature 020); this endpoint is
    unchanged."""
    async with auth_lookup_session() as s:
        rows = await membership.workspaces_for(s, principal.subject)
    return [
        WorkspaceMembershipOut(id=str(r.id), name=r.name, group_prefix=r.group_prefix, role=r.console_role)
        for r in rows
    ]


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


@router.get("/spend", dependencies=[Depends(require_permission("view"))])
async def get_spend(ws: str = Depends(resolve_workspace),
                    session: AsyncSession = Depends(tenant_db)) -> WorkspaceSpendOut:
    """Feature 009 (M6/US3): month-to-date spend vs the active monthly cap, from the same durable helpers the
    spend-cap gate enforces (one source of truth — SC-004). Workspace-scoped; `view` permission."""
    runs, ingestion = await spend.month_to_date_breakdown(session, ws)
    spent = runs + ingestion
    cap = await spend.active_cap_usd(session, ws)
    return WorkspaceSpendOut(monthToDateUsd=float(spent), capUsd=float(cap), remainingUsd=float(cap - spent),
                             breakdown={"runs": f"{runs:.6f}", "ingestion": f"{ingestion:.6f}"})


@router.get("/settings", dependencies=[Depends(require_permission("view"))])
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


@router.patch("/settings", dependencies=[Depends(require_permission("settings.manage")), Depends(rate_limit("default"))])
async def patch_settings(body: WorkspaceSettingsPatch, response: Response,
                         ws: str = Depends(resolve_workspace), session: AsyncSession = Depends(tenant_db),
                         if_match: str | None = Header(default=None, alias="If-Match")) -> WorkspaceSettingsOut:
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


@router.get("/telemetry", dependencies=[Depends(require_permission("view"))])
async def get_telemetry(ws: str = Depends(resolve_workspace),
                        session: AsyncSession = Depends(tenant_db)) -> TelemetrySettingsOut:
    """Feature 023: the workspace's content-logging switch + whether call-log lines actually carry redacted text
    (`effective` includes the non-prod dev override). Read-only — never creates the settings row."""
    stored, effective = await call_log.effective_content_logging(session, ws)
    return TelemetrySettingsOut(content_logging=stored, effective=effective)


@router.put("/telemetry",
            dependencies=[Depends(require_permission("settings.manage")), Depends(rate_limit("default"))])
async def put_telemetry(body: TelemetrySettingsIn, ws: str = Depends(resolve_workspace),
                        principal: Principal = Depends(session_principal),
                        corr: str = Depends(correlation_id),
                        session: AsyncSession = Depends(tenant_db)) -> TelemetrySettingsOut:
    """Feature 023 (FR-002b): admin-only opt-in for redacted prompt/output text in this workspace's call-log lines.
    Every CHANGE writes one audit line (who, when, which workspace, old→new); a same-value PUT writes none. The
    durable `audit_logs` table is deferred project-wide (018) — structured log line now."""
    s = await _load_settings(session, ws, for_update=True)
    old = bool(s.content_logging) if s is not None else False
    if s is None and body.content_logging:
        # Only create the row on a real change: a no-op PUT must not flip the settings ETag off "0".
        s = WorkspaceSettings(workspace_id=uuid.UUID(ws), limits={}, eval_policy={}, content_logging=body.content_logging)
        session.add(s)
        try:
            await session.flush()
        except IntegrityError as exc:
            raise ConflictStale("settings were created concurrently; re-read and retry",
                                detail={"workspace_id": ws}) from exc
    elif s is not None and old != body.content_logging:
        s.content_logging = body.content_logging
        await session.flush()
    if old != body.content_logging:
        _audit.info(json.dumps({
            "event": "telemetry.content_logging.changed", "subject": principal.subject, "workspace_id": ws,
            "old": old, "new": body.content_logging, "correlation_id": corr,
            "ts": datetime.now(timezone.utc).isoformat(),
        }, separators=(",", ":")))
    # Same rule as call_log.effective_content_logging, without re-reading the row we just wrote.
    effective = body.content_logging or app_settings().telemetry_content_override
    return TelemetrySettingsOut(content_logging=body.content_logging, effective=effective)


@router.patch("", dependencies=[Depends(require_permission("settings.manage")), Depends(rate_limit("default"))])
async def rename_workspace(body: WorkspacePatch, ws: str = Depends(resolve_workspace),
                           session: AsyncSession = Depends(tenant_db)) -> WorkspaceOut:
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
