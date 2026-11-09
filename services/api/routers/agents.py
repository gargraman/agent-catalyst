"""Agents CRUD + versioning router (M4, feature 005, US3).

Create/get/list/PATCH + POST-version, all workspace-scoped through the RLS session (P5). Optimistic concurrency
uses the Postgres `xmin` row-version as the ETag (D7, migration-free). Idempotent create via the in-flight
guard (D8). DELETE/duplicate are deferred.
"""
from __future__ import annotations

import datetime
import hashlib
import json as _json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy import select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.access_policy import PolicyPrincipal, can_use
from api.deps import (access_principal, ensure_manage, ensure_use, require_permission, idempotency_guard,
                      Principal, rate_limit, resolve_workspace, session_principal, tenant_db)
from api.errors import ConflictStale, InputInvalid, NotFound
from api.models import Agent, AgentVersion
from runtime.hashing import compute_snapshot_hash
from runtime.providers.profiles import _PROFILE_DIR
from api.pagination import Page, clamp_limit, decode_cursor, encode_cursor
from api.schemas.access_policy import policy_for_store, policy_out, stamp_private_owner, validate_access_policy
from api.schemas.agents import AgentCreate, AgentOut, AgentPatch, VersionOut

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


def _version_out(v: AgentVersion) -> VersionOut:
    return VersionOut(id=str(v.id), agent_id=str(v.agent_id), label=v.label, snapshot_hash=v.snapshot_hash,
                      prompt_hash=v.prompt_hash, publishable=v.publishable, created_at=v.created_at)


def _agent_out(a: Agent) -> AgentOut:
    return AgentOut(id=str(a.id), name=a.name, status=a.status,
                    active_version_id=str(a.active_version_id) if a.active_version_id else None,
                    workspace_id=str(a.workspace_id), created_at=a.created_at,
                    access_policy=policy_out(a.access_policy),
                    versions=[_version_out(v) for v in sorted(a.versions, key=lambda v: v.created_at)])


async def _load_agent(session: AsyncSession, agent_id: str) -> Agent | None:
    try:
        aid = uuid.UUID(agent_id)
    except ValueError:
        return None
    stmt = select(Agent).where(Agent.id == aid).options(selectinload(Agent.versions))
    return (await session.execute(stmt)).scalar_one_or_none()


async def _version_belongs(session: AsyncSession, agent_id: str, version_id: str) -> uuid.UUID:
    """Validate that `version_id` is a well-formed UUID AND belongs to THIS agent (same workspace via RLS).
    Prevents pointing an agent at another agent's version, and a malformed/absent id → typed 400/404 (not a
    500 / FK IntegrityError)."""
    try:
        vid = uuid.UUID(version_id)
        aid = uuid.UUID(agent_id)
    except (ValueError, TypeError):
        raise InputInvalid("malformed version id", detail={"version_id": version_id})
    row = (await session.execute(
        select(AgentVersion.id).where(AgentVersion.id == vid, AgentVersion.agent_id == aid))).first()
    if row is None:
        raise NotFound("version not found for this agent",
                       detail={"agent_id": agent_id, "version_id": version_id})
    return vid


async def _etag(session: AsyncSession, agent_id: str) -> str:
    row = (await session.execute(text("select xmin::text as etag from agents where id = :id"),
                                 {"id": agent_id})).first()
    return row.etag if row else ""


def _prompt_hash(system_prompt: str) -> str:
    return hashlib.sha256(system_prompt.encode()).hexdigest()[:16]


def _load_profile_snapshot(provider: str, model: str) -> dict:
    """Freeze the capability/pricing profile for (provider, model) from the versioned profiles_data, so a
    created version is invocable (its runtime profile resolves, P6/P7). Fails closed on an unknown model."""
    for path in sorted(Path(_PROFILE_DIR).glob("*.json")):
        doc = _json.loads(path.read_text())
        if doc.get("provider") == provider and doc.get("model") == model:
            return doc
    raise InputInvalid("unknown provider/model (no capability profile)",
                       detail={"provider": provider, "model": model})


@router.post("", status_code=201, dependencies=[Depends(require_permission("agent.create"))])
async def create_agent(
    body: AgentCreate,
    response: Response,
    ws: str = Depends(resolve_workspace),
    session: AsyncSession = Depends(tenant_db),
    principal: Principal = Depends(session_principal),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    _rl: None = Depends(rate_limit),
) -> AgentOut:
    if "input_schema" not in (body.io_contract or {}):
        raise InputInvalid("io_contract must contain an input_schema object",
                           detail={"field": "io_contract.input_schema"})
    profile = _load_profile_snapshot(body.provider, body.model)   # fail closed on unknown model; makes it invocable
    access_policy = policy_for_store(body.access_policy)           # feature 013: validate → input.invalid; default workspace
    access_policy = stamp_private_owner(access_policy, principal.subject)   # FR-012: private → stamp creator as owner
    async with idempotency_guard(ws, idempotency_key):
        agent = Agent(workspace_id=uuid.UUID(ws), name=body.name, status="draft", access_policy=access_policy)
        session.add(agent)
        await session.flush()
        v = AgentVersion(
            workspace_id=uuid.UUID(ws), agent_id=agent.id, label="v1",
            system_prompt=body.system_prompt, prompt_hash=_prompt_hash(body.system_prompt),
            model_config_={"provider": body.provider, "model": body.model, "params": {}},
            profile_snapshot=profile, io_contract=body.io_contract, snapshot_hash="",
            # Set the config defaults EXPLICITLY: column server-defaults apply at flush, but the hash is
            # computed pre-flush, so an unset field would hash as None here yet {} once loaded — diverging
            # from to_snapshot(). Setting {} makes the pre-flush hash equal the authoritative recomputed one.
            guardrail_config={}, memory_config={}, context_config={},
        )
        # The AUTHORITATIVE content hash the runtime/RunRecord use (compute over empty bindings pre-flush —
        # no I/O). Stored == to_snapshot() recomputes, so version-id ↔ run content-hash comparisons hold.
        v.retrieval_bindings, v.tool_bindings, v.skill_bindings = [], [], []
        v.snapshot_hash = compute_snapshot_hash(v)
        session.add(v)
        await session.flush()
        agent.active_version_id = v.id
        await session.flush()
        await session.refresh(agent, ["versions"])
        response.headers["ETag"] = await _etag(session, str(agent.id))
        return _agent_out(agent)


@router.get("", dependencies=[Depends(require_permission("view"))])
async def list_agents(
    ws: str = Depends(resolve_workspace),
    session: AsyncSession = Depends(tenant_db),
    ap: PolicyPrincipal = Depends(access_principal),   # feature 013
    limit: int | None = None,
    cursor: str | None = None,
) -> Page[AgentOut]:
    n = clamp_limit(limit)
    stmt = select(Agent).options(selectinload(Agent.versions)).order_by(Agent.created_at.desc(), Agent.id.desc())
    cur = decode_cursor(cursor)
    if cur is not None:
        # keyset on the SAME ordering key (created_at, id) as the ORDER BY — a row-value seek, so the next
        # page can't drop/repeat rows (id alone is a random uuid4, unrelated to created_at order).
        sort_value, last_id = cur
        cur_dt = datetime.datetime.fromisoformat(sort_value)
        stmt = stmt.where(tuple_(Agent.created_at, Agent.id) < tuple_(cur_dt, uuid.UUID(last_id)))
    rows = list((await session.execute(stmt.limit(n + 1))).scalars().all())
    next_cursor = None
    if len(rows) > n:
        last = rows[n - 1]
        next_cursor = encode_cursor(last.created_at.isoformat(), str(last.id))
        rows = rows[:n]
    # feature 013: omit resources the caller can't USE (P5 no-leak — a hidden row is never listed). Post-filter
    # of the page (G4): a short page is acceptable; keyset pagination still advances by created_at,id.
    rows = [a for a in rows if can_use(a.access_policy, ap)]
    return Page[AgentOut](items=[_agent_out(a) for a in rows], next_cursor=next_cursor)


@router.get("/{agent_id}", dependencies=[Depends(require_permission("view"))])
async def get_agent(
    agent_id: str,
    response: Response,
    ws: str = Depends(resolve_workspace),
    session: AsyncSession = Depends(tenant_db),
    ap: PolicyPrincipal = Depends(access_principal),   # feature 013
) -> AgentOut:
    agent = await _load_agent(session, agent_id)
    if agent is None:
        raise NotFound("agent not found", detail={"agent_id": agent_id})
    # feature 013: deny USE as a 404 byte-identical to the absent-row 404 (P5 no-leak).
    ensure_use(agent.access_policy, ap, not_found=NotFound("agent not found", detail={"agent_id": agent_id}))
    response.headers["ETag"] = await _etag(session, agent_id)
    return _agent_out(agent)


@router.patch("/{agent_id}", dependencies=[Depends(require_permission("agent.edit"))])
async def patch_agent(
    agent_id: str,
    body: AgentPatch,
    response: Response,
    ws: str = Depends(resolve_workspace),
    session: AsyncSession = Depends(tenant_db),
    principal: Principal = Depends(session_principal),
    ap: PolicyPrincipal = Depends(access_principal),
    if_match: str | None = Header(default=None, alias="If-Match"),
    _rl: None = Depends(rate_limit),
) -> AgentOut:
    if not if_match:
        raise InputInvalid("If-Match header is required for edits", detail={"header": "If-Match"})
    try:
        etag_val = int(if_match)                       # xmin is a 32-bit xid; asyncpg binds it as an int
    except (TypeError, ValueError):
        raise ConflictStale("malformed If-Match ETag", detail={"agent_id": agent_id})
    # feature 013: MANAGE gate (404-then-403) BEFORE probing version/ETag — a non-usable caller gets the
    # absent-row 404; usable-but-not-manageable gets 403. Fires after RBAC (require_permission) so a role 403
    # still wins. Load the policy first (light select; NOT NULL column → None ⇒ absent row).
    try:
        _aid = uuid.UUID(agent_id)
    except ValueError:
        raise NotFound("agent not found", detail={"agent_id": agent_id})
    _pol = (await session.execute(select(Agent.access_policy).where(Agent.id == _aid))).scalar_one_or_none()
    if _pol is None:
        raise NotFound("agent not found", detail={"agent_id": agent_id})
    ensure_manage(_pol, ap, not_found=NotFound("agent not found", detail={"agent_id": agent_id}))
    # A re-point MUST target a version of THIS agent (validated/scoped) — never a foreign version or a
    # malformed id landing as a 500 (F3).
    if body.active_version_id is not None:
        await _version_belongs(session, agent_id, body.active_version_id)
    # feature 013: validate + owner-stamp the NEW policy. Named distinctly from the `ap` PolicyPrincipal dep
    # above so the caller view is never shadowed (a later ensure_*/can_use must still see the principal).
    new_policy = validate_access_policy(body.access_policy)   # None when omitted → coalesce keeps existing
    new_policy = stamp_private_owner(new_policy, principal.subject)   # FR-012: edit-to-private → stamp the editor
    res = await session.execute(
        text("update agents set name = coalesce(:name, name), status = coalesce(:status, status), "
             "active_version_id = coalesce(:av, active_version_id), "
             "access_policy = coalesce(cast(:policy as jsonb), access_policy) "
             "where id = :id and xmin = cast(:etag as xid) returning id"),
        {"name": body.name, "status": body.status,
         "av": str(body.active_version_id) if body.active_version_id else None,
         "policy": _json.dumps(new_policy) if new_policy is not None else None,
         "id": agent_id, "etag": etag_val},
    )
    if res.first() is None:
        # distinguish a stale ETag (row exists) from a missing/cross-tenant row (RLS-hidden).
        exists = (await session.execute(text("select 1 from agents where id = :id"), {"id": agent_id})).first()
        if exists:
            raise ConflictStale("stale If-Match ETag; the agent was modified concurrently",
                                detail={"agent_id": agent_id})
        raise NotFound("agent not found", detail={"agent_id": agent_id})
    agent = await _load_agent(session, agent_id)
    response.headers["ETag"] = await _etag(session, agent_id)
    return _agent_out(agent)


@router.post("/{agent_id}/versions", status_code=201, dependencies=[Depends(require_permission("agent.edit"))])
async def create_version(
    agent_id: str,
    ws: str = Depends(resolve_workspace),
    session: AsyncSession = Depends(tenant_db),
    ap: PolicyPrincipal = Depends(access_principal),
    label: str | None = None,
    _rl: None = Depends(rate_limit),
) -> VersionOut:
    agent = await _load_agent(session, agent_id)
    if agent is None or not agent.versions:
        raise NotFound("agent (or its base version) not found", detail={"agent_id": agent_id})
    # feature 013: creating a version is an edit → MANAGE gate (404-then-403) after RBAC.
    ensure_manage(agent.access_policy, ap,
                  not_found=NotFound("agent (or its base version) not found", detail={"agent_id": agent_id}))
    latest = sorted(agent.versions, key=lambda v: v.created_at)[-1]
    # Label uniqueness (F5): reject an explicit duplicate; auto-derive the lowest free vN (count+1 breaks once
    # a custom label exists). A durable unique(agent_id,label) constraint is deferred to M4-remainder.
    existing = {v.label for v in agent.versions}
    if label is not None:
        if label in existing:
            raise InputInvalid("version label already exists", detail={"label": label})
        new_label = label
    else:
        k = 1
        while f"v{k}" in existing:
            k += 1
        new_label = f"v{k}"
    clone = AgentVersion(
        workspace_id=latest.workspace_id, agent_id=latest.agent_id, label=new_label,
        system_prompt=latest.system_prompt, prompt_hash=latest.prompt_hash, snapshot_hash="",
        model_config_=latest.model_config_, profile_snapshot=latest.profile_snapshot,
        io_contract=latest.io_contract, guardrail_config=latest.guardrail_config,
        memory_config=latest.memory_config, context_config=latest.context_config,
    )
    # Recompute the authoritative snapshot_hash (same config as latest → same hash, consistent with F4).
    clone.retrieval_bindings, clone.tool_bindings, clone.skill_bindings = [], [], []
    clone.snapshot_hash = compute_snapshot_hash(clone)
    session.add(clone)
    await session.flush()
    return _version_out(clone)
