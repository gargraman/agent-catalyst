"""Tool + MCP catalog router (feature 006, US1).

Workspace-scoped CRUD + soft-archive/restore for HTTP tools and MCP servers/tools, through the RLS session
(P5). Optimistic concurrency via the Postgres `xmin` ETag (D7); idempotent create (D8). Editing a tool's
auth-mode/scopes flags bound agents for re-eval (FR-002). http_config is non-secret only (FR-021, enforced in
the schema validator).
"""
from __future__ import annotations

import datetime
import uuid

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy import select, text, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api import drift
from api.deps import idempotency_guard, rate_limit, resolve_workspace, tenant_db
from api.errors import ConflictError, ConflictStale, InputInvalid, NotFound
from api.models import McpServer, McpTool, Tool
from api.pagination import Page, clamp_limit, decode_cursor, encode_cursor
from api.schemas.tools import (
    McpServerCreate, McpServerOut, McpServerPatch, McpToolCreate, McpToolOut, McpToolPatch,
    ToolCreate, ToolOut, ToolPatch,
)

router = APIRouter(prefix="/api/v1", tags=["tools"])


# ---- shared helpers ------------------------------------------------------------------------------
async def _etag(session: AsyncSession, table: str, row_id: str) -> str:
    row = (await session.execute(text(f"select xmin::text as etag from {table} where id = :id"),
                                 {"id": row_id})).first()
    return row.etag if row else ""


def _as_uuid(val: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(val)
    except (ValueError, TypeError):
        return None


def _require_etag(if_match: str | None, what: str) -> int:
    if not if_match:
        raise InputInvalid("If-Match header is required for edits", detail={"header": "If-Match"})
    try:
        return int(if_match)
    except (TypeError, ValueError):
        raise ConflictStale("malformed If-Match ETag", detail={"resource": what})


async def _flush_unique(session: AsyncSession, what: str, field: str) -> None:
    """Flush, mapping a UNIQUE-violation to a typed 409 (not an unhandled 500). F3."""
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ConflictError(f"a {what} with this {field} already exists in this workspace",
                            detail={"field": field}) from exc


# ---- Tool projection -----------------------------------------------------------------------------
def _tool_out(t: Tool) -> ToolOut:
    return ToolOut(id=str(t.id), name=t.name, description=t.description, kind=t.kind,
                   http_config=t.http_config, risk_tier=t.risk_tier,
                   archived=t.archived_at is not None, created_at=t.created_at)


@router.post("/tools", status_code=201)
async def create_tool(
    body: ToolCreate, response: Response,
    ws: str = Depends(resolve_workspace), session: AsyncSession = Depends(tenant_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    _rl: None = Depends(rate_limit),
) -> ToolOut:
    async with idempotency_guard(ws, idempotency_key):
        t = Tool(workspace_id=uuid.UUID(ws), name=body.name, description=body.description,
                 kind=body.kind, http_config=body.http_config, risk_tier=body.risk_tier)
        session.add(t)
        await _flush_unique(session, "tool", "name")
        response.headers["ETag"] = await _etag(session, "tools", str(t.id))
        return _tool_out(t)


@router.get("/tools")
async def list_tools(
    ws: str = Depends(resolve_workspace), session: AsyncSession = Depends(tenant_db),
    limit: int | None = None, cursor: str | None = None, include_archived: bool = False,
) -> Page[ToolOut]:
    n = clamp_limit(limit)
    stmt = select(Tool).order_by(Tool.created_at.desc(), Tool.id.desc())
    if not include_archived:
        stmt = stmt.where(Tool.archived_at.is_(None))
    cur = decode_cursor(cursor)
    if cur is not None:
        sort_value, last_id = cur
        stmt = stmt.where(tuple_(Tool.created_at, Tool.id)
                          < tuple_(datetime.datetime.fromisoformat(sort_value), uuid.UUID(last_id)))
    rows = list((await session.execute(stmt.limit(n + 1))).scalars().all())
    next_cursor = None
    if len(rows) > n:
        last = rows[n - 1]
        next_cursor = encode_cursor(last.created_at.isoformat(), str(last.id))
        rows = rows[:n]
    return Page[ToolOut](items=[_tool_out(t) for t in rows], next_cursor=next_cursor)


async def _load_tool(session: AsyncSession, tool_id: str, *, for_update: bool = False) -> Tool | None:
    tid = _as_uuid(tool_id)
    if tid is None:
        return None
    stmt = select(Tool).where(Tool.id == tid)
    if for_update:
        stmt = stmt.with_for_update()        # lock the row so the xmin compare+write is race-free (F2)
    return (await session.execute(stmt)).scalar_one_or_none()


@router.get("/tools/{tool_id}")
async def get_tool(tool_id: str, response: Response, ws: str = Depends(resolve_workspace),
                   session: AsyncSession = Depends(tenant_db)) -> ToolOut:
    t = await _load_tool(session, tool_id)
    if t is None:
        raise NotFound("tool not found", detail={"tool_id": tool_id})
    response.headers["ETag"] = await _etag(session, "tools", tool_id)
    return _tool_out(t)


def _auth_surface(cfg: dict) -> tuple:
    """The drift-triggering slice of http_config (FR-002): auth-mode + OAuth scopes."""
    return (cfg.get("authMode"), cfg.get("oauthScopes"))


@router.patch("/tools/{tool_id}")
async def patch_tool(
    tool_id: str, body: ToolPatch, response: Response,
    ws: str = Depends(resolve_workspace), session: AsyncSession = Depends(tenant_db),
    if_match: str | None = Header(default=None, alias="If-Match"), _rl: None = Depends(rate_limit),
) -> ToolOut:
    etag_val = _require_etag(if_match, "tool")
    t = await _load_tool(session, tool_id, for_update=True)
    if t is None:
        raise NotFound("tool not found", detail={"tool_id": tool_id})
    if await _etag(session, "tools", tool_id) != str(etag_val):
        raise ConflictStale("stale If-Match ETag; the tool was modified concurrently",
                            detail={"tool_id": tool_id})
    auth_before = _auth_surface(t.http_config)
    if body.name is not None:
        t.name = body.name
    if body.description is not None:
        t.description = body.description
    if body.risk_tier is not None:
        t.risk_tier = body.risk_tier
    if body.http_config is not None:
        t.http_config = body.http_config
    await session.flush()
    # Drift: only an auth-mode/scope change flags bound agents (FR-002); a cosmetic edit does not.
    if body.http_config is not None and _auth_surface(t.http_config) != auth_before:
        await drift.flag_bound_agents(session, "tool", tool_id)
    response.headers["ETag"] = await _etag(session, "tools", tool_id)
    return _tool_out(t)


@router.post("/tools/{tool_id}/archive")
async def archive_tool(tool_id: str, ws: str = Depends(resolve_workspace),
                       session: AsyncSession = Depends(tenant_db), _rl: None = Depends(rate_limit)) -> ToolOut:
    t = await _load_tool(session, tool_id)
    if t is None:
        raise NotFound("tool not found", detail={"tool_id": tool_id})
    t.archived_at = datetime.datetime.now(datetime.timezone.utc)
    await session.flush()
    return _tool_out(t)


@router.post("/tools/{tool_id}/restore")
async def restore_tool(tool_id: str, ws: str = Depends(resolve_workspace),
                       session: AsyncSession = Depends(tenant_db), _rl: None = Depends(rate_limit)) -> ToolOut:
    t = await _load_tool(session, tool_id)
    if t is None:
        raise NotFound("tool not found", detail={"tool_id": tool_id})
    t.archived_at = None
    await session.flush()
    return _tool_out(t)


# ---- MCP servers + tools -------------------------------------------------------------------------
def _mcp_tool_out(mt: McpTool) -> McpToolOut:
    return McpToolOut(id=str(mt.id), server_id=str(mt.server_id), name=mt.name,
                      description=mt.description, enabled=mt.enabled, risk_tier=mt.risk_tier)


def _mcp_out(m: McpServer) -> McpServerOut:
    return McpServerOut(id=str(m.id), name=m.name, url=m.url, transport=m.transport, status=m.status,
                        archived=m.archived_at is not None, created_at=m.created_at,
                        tools=[_mcp_tool_out(t) for t in m.tools])


async def _load_mcp(session: AsyncSession, server_id: str, *, for_update: bool = False) -> McpServer | None:
    sid = _as_uuid(server_id)
    if sid is None:
        return None
    stmt = select(McpServer).where(McpServer.id == sid).options(selectinload(McpServer.tools))
    if for_update:
        stmt = stmt.with_for_update(of=McpServer)   # lock the server row (selectinload runs separate SELECTs)
    return (await session.execute(stmt)).scalar_one_or_none()


@router.post("/mcp-servers", status_code=201)
async def create_mcp_server(
    body: McpServerCreate, response: Response, ws: str = Depends(resolve_workspace),
    session: AsyncSession = Depends(tenant_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    _rl: None = Depends(rate_limit),
) -> McpServerOut:
    async with idempotency_guard(ws, idempotency_key):
        m = McpServer(workspace_id=uuid.UUID(ws), name=body.name, url=body.url, transport=body.transport)
        session.add(m)
        await _flush_unique(session, "mcp server", "name")
        await session.refresh(m, ["tools"])
        response.headers["ETag"] = await _etag(session, "mcp_servers", str(m.id))
        return _mcp_out(m)


@router.get("/mcp-servers")
async def list_mcp_servers(ws: str = Depends(resolve_workspace), session: AsyncSession = Depends(tenant_db),
                           limit: int | None = None, cursor: str | None = None,
                           include_archived: bool = False) -> Page[McpServerOut]:
    n = clamp_limit(limit)
    stmt = select(McpServer).options(selectinload(McpServer.tools)).order_by(
        McpServer.created_at.desc(), McpServer.id.desc())
    if not include_archived:
        stmt = stmt.where(McpServer.archived_at.is_(None))
    cur = decode_cursor(cursor)
    if cur is not None:
        sort_value, last_id = cur
        stmt = stmt.where(tuple_(McpServer.created_at, McpServer.id)
                          < tuple_(datetime.datetime.fromisoformat(sort_value), uuid.UUID(last_id)))
    rows = list((await session.execute(stmt.limit(n + 1))).scalars().all())
    next_cursor = None
    if len(rows) > n:
        last = rows[n - 1]
        next_cursor = encode_cursor(last.created_at.isoformat(), str(last.id))
        rows = rows[:n]
    return Page[McpServerOut](items=[_mcp_out(m) for m in rows], next_cursor=next_cursor)


@router.get("/mcp-servers/{server_id}")
async def get_mcp_server(server_id: str, response: Response, ws: str = Depends(resolve_workspace),
                         session: AsyncSession = Depends(tenant_db)) -> McpServerOut:
    m = await _load_mcp(session, server_id)
    if m is None:
        raise NotFound("mcp server not found", detail={"server_id": server_id})
    response.headers["ETag"] = await _etag(session, "mcp_servers", server_id)
    return _mcp_out(m)


@router.patch("/mcp-servers/{server_id}")
async def patch_mcp_server(server_id: str, body: McpServerPatch, response: Response,
                           ws: str = Depends(resolve_workspace), session: AsyncSession = Depends(tenant_db),
                           if_match: str | None = Header(default=None, alias="If-Match"),
                           _rl: None = Depends(rate_limit)) -> McpServerOut:
    etag_val = _require_etag(if_match, "mcp server")       # optimistic concurrency, like tools/skills
    m = await _load_mcp(session, server_id, for_update=True)
    if m is None:
        raise NotFound("mcp server not found", detail={"server_id": server_id})
    if await _etag(session, "mcp_servers", server_id) != str(etag_val):
        raise ConflictStale("stale If-Match ETag; the mcp server was modified concurrently",
                            detail={"server_id": server_id})
    for f in ("name", "url", "transport", "status"):
        v = getattr(body, f)
        if v is not None:
            setattr(m, f, v)
    await session.flush()
    response.headers["ETag"] = await _etag(session, "mcp_servers", server_id)
    return _mcp_out(m)


@router.post("/mcp-servers/{server_id}/archive")
async def archive_mcp_server(server_id: str, ws: str = Depends(resolve_workspace),
                             session: AsyncSession = Depends(tenant_db), _rl: None = Depends(rate_limit)) -> McpServerOut:
    m = await _load_mcp(session, server_id)
    if m is None:
        raise NotFound("mcp server not found", detail={"server_id": server_id})
    m.archived_at = datetime.datetime.now(datetime.timezone.utc)
    await session.flush()
    return _mcp_out(m)


@router.post("/mcp-servers/{server_id}/tools", status_code=201)
async def add_mcp_tool(server_id: str, body: McpToolCreate, response: Response,
                       ws: str = Depends(resolve_workspace), session: AsyncSession = Depends(tenant_db),
                       _rl: None = Depends(rate_limit)) -> McpToolOut:
    m = await _load_mcp(session, server_id)
    if m is None:
        raise NotFound("mcp server not found", detail={"server_id": server_id})
    mt = McpTool(workspace_id=uuid.UUID(ws), server_id=m.id, name=body.name, description=body.description,
                 enabled=body.enabled, risk_tier=body.risk_tier)
    session.add(mt)
    await _flush_unique(session, "mcp tool", "name")        # UNIQUE(server_id, name)
    response.headers["ETag"] = await _etag(session, "mcp_tools", str(mt.id))
    return _mcp_tool_out(mt)


# The grant/content surface of an MCP tool that folds into a bound agent's authorization (drift trigger, FR-002).
# name/description are cosmetic and must NOT flag.
def _mcp_tool_surface(mt: McpTool) -> tuple:
    return (mt.enabled, mt.risk_tier)


@router.patch("/mcp-tools/{tool_id}")
async def patch_mcp_tool(tool_id: str, body: McpToolPatch, response: Response,
                         ws: str = Depends(resolve_workspace), session: AsyncSession = Depends(tenant_db),
                         if_match: str | None = Header(default=None, alias="If-Match"),
                         _rl: None = Depends(rate_limit)) -> McpToolOut:
    etag_val = _require_etag(if_match, "mcp tool")          # optimistic concurrency
    tid = _as_uuid(tool_id)
    mt = None if tid is None else (await session.execute(
        select(McpTool).where(McpTool.id == tid).with_for_update())).scalar_one_or_none()   # lock (F2)
    if mt is None:
        raise NotFound("mcp tool not found", detail={"tool_id": tool_id})
    if await _etag(session, "mcp_tools", tool_id) != str(etag_val):
        raise ConflictStale("stale If-Match ETag; the mcp tool was modified concurrently",
                            detail={"tool_id": tool_id})
    surface_before = _mcp_tool_surface(mt)
    for f in ("name", "description", "enabled", "risk_tier"):
        v = getattr(body, f)
        if v is not None:
            setattr(mt, f, v)
    await session.flush()
    # F2: only a grant-surface change (enabled/risk_tier) flags bound agents; a cosmetic edit must not.
    if _mcp_tool_surface(mt) != surface_before:
        await drift.flag_bound_agents(session, "mcp_tool", tool_id)
    response.headers["ETag"] = await _etag(session, "mcp_tools", tool_id)
    return _mcp_tool_out(mt)
