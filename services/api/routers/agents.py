"""Agents CRUD + versioning router (M4, feature 005, US3).

Create/get/list/PATCH + POST-version, all workspace-scoped through the RLS session (P5). Optimistic concurrency
uses the Postgres `xmin` row-version as the ETag (D7, migration-free). Idempotent create via the in-flight
guard (D8). DELETE/duplicate are deferred.

TODO(backend): DELETE /{agent_id} and POST /{agent_id}/duplicate are still missing — the console calls both
  (frontend store.ts deleteAgent/duplicateAgent) and, for a live agent, they are local-only until these exist.
"""
from __future__ import annotations

import datetime
import hashlib
import json as _json
import uuid
from pathlib import Path

from fastapi import APIRouter, Body, Depends, Header, Response
from sqlalchemy import select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.access_policy import PolicyPrincipal, can_use
from api.deps import (access_principal, ensure_manage, ensure_use, require_permission, idempotency_guard,
                      Principal, rate_limit, resolve_workspace, session_principal, tenant_db)
from api.errors import ConflictStale, InputInvalid, KbArchived, NotFound
from api.models import Agent, AgentVersion, KnowledgeBase, RetrievalBinding
from runtime.hashing import compute_snapshot_hash
from runtime.providers.profiles import _PROFILE_DIR
from api.pagination import Page, clamp_limit, decode_cursor, encode_cursor
from api.schemas.access_policy import policy_for_store, policy_out, stamp_private_owner, validate_access_policy
from api.schemas.agents import AgentCreate, AgentOut, AgentPatch, RetrievalBindingOut, VersionCreate, VersionOut

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


# Feature 024: every path that renders versions eager-loads bindings + their KB (async: no lazy loads; masking needs
# the KB's live access policy).
_VERSIONS = selectinload(Agent.versions).selectinload(AgentVersion.retrieval_bindings).selectinload(RetrievalBinding.kb)


def _binding_out(b: RetrievalBinding, ap: PolicyPrincipal) -> RetrievalBindingOut:
    if not can_use(b.kb.access_policy, ap):                  # H2: never reveal a KB the viewer may not use
        return RetrievalBindingOut(kb_id=None, kb_name=None, injection=b.injection, retrieval_config=b.retrieval_config,
                                   embedding_model=None, kb_index_version=None, restricted=True)
    return RetrievalBindingOut(kb_id=str(b.kb_id), kb_name=b.kb.name, injection=b.injection,
                               retrieval_config=b.retrieval_config, embedding_model=b.kb.embedding_model,
                               kb_index_version=b.kb_index_version, restricted=False)


def _version_out(v: AgentVersion, ap: PolicyPrincipal) -> VersionOut:
    out = VersionOut(id=str(v.id), agent_id=str(v.agent_id), label=v.label, snapshot_hash=v.snapshot_hash,
                     prompt_hash=v.prompt_hash, publishable=v.publishable, created_at=v.created_at)
    out.retrieval_bindings = [_binding_out(b, ap) for b in sorted(v.retrieval_bindings, key=lambda b: str(b.kb_id))]
    return out


def _agent_out(a: Agent, ap: PolicyPrincipal) -> AgentOut:
    return AgentOut(id=str(a.id), name=a.name, status=a.status,
                    active_version_id=str(a.active_version_id) if a.active_version_id else None,
                    workspace_id=str(a.workspace_id), created_at=a.created_at,
                    access_policy=policy_out(a.access_policy),
                    versions=[_version_out(v, ap) for v in sorted(a.versions, key=lambda v: v.created_at)])


async def _load_agent(session: AsyncSession, agent_id: str) -> Agent | None:
    try:
        aid = uuid.UUID(agent_id)
    except ValueError:
        return None
    stmt = select(Agent).where(Agent.id == aid).options(_VERSIONS)
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


@router.post("", status_code=201, dependencies=[Depends(require_permission("agent.create")), Depends(rate_limit("default"))])
async def create_agent(
    body: AgentCreate,
    response: Response,
    ws: str = Depends(resolve_workspace),
    session: AsyncSession = Depends(tenant_db),
    principal: Principal = Depends(session_principal),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
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
        # a brand-new agent has no bindings, so masking cannot apply; the creator's own role is enough here
        return _agent_out(agent, PolicyPrincipal(role=principal.workspace_claims.get(ws, ""), teams=(),
                                                 subject=principal.subject))


@router.get("", dependencies=[Depends(require_permission("view"))])
async def list_agents(
    ws: str = Depends(resolve_workspace),
    session: AsyncSession = Depends(tenant_db),
    ap: PolicyPrincipal = Depends(access_principal),   # feature 013
    limit: int | None = None,
    cursor: str | None = None,
) -> Page[AgentOut]:
    n = clamp_limit(limit)
    stmt = select(Agent).options(_VERSIONS).order_by(Agent.created_at.desc(), Agent.id.desc())
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
    return Page[AgentOut](items=[_agent_out(a, ap) for a in rows], next_cursor=next_cursor)


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
    return _agent_out(agent, ap)


@router.patch("/{agent_id}", dependencies=[Depends(require_permission("agent.edit")), Depends(rate_limit("default"))])
async def patch_agent(
    agent_id: str,
    body: AgentPatch,
    response: Response,
    ws: str = Depends(resolve_workspace),
    session: AsyncSession = Depends(tenant_db),
    principal: Principal = Depends(session_principal),
    ap: PolicyPrincipal = Depends(access_principal),
    if_match: str | None = Header(default=None, alias="If-Match"),
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
    return _agent_out(agent, ap)


_CFG_KEYS = frozenset({"top_k", "similarity_threshold", "max_ctx_tokens", "hybrid", "reranker", "metadata_filters"})
_MAX_BINDINGS = 5


def _bad(field: str, why: str) -> InputInvalid:
    return InputInvalid(f"invalid retrieval binding: {why}", detail={"field": field})


def _num(v, field: str, lo, hi, *, integer: bool):
    if isinstance(v, bool) or not isinstance(v, (int,) if integer else (int, float)):
        raise _bad(field, "must be a number" if not integer else "must be an integer")
    if not lo <= v <= hi:
        raise _bad(field, f"must be within {lo}..{hi}")
    return v


def _retrieval_config(raw, i: int) -> dict:
    """Canonical retrieval_config (data-model §5). Unknown keys are refused, not silently dropped."""
    f = f"retrieval_bindings[{i}].retrieval_config"
    if not isinstance(raw, dict):
        raise _bad(f, "must be an object")
    unknown = set(raw) - _CFG_KEYS
    if unknown:
        raise _bad(f, f"unknown keys {sorted(unknown)}")
    reranker = raw.get("reranker")
    filters = raw.get("metadata_filters", {})
    hybrid = raw.get("hybrid", False)
    if reranker is not None and not isinstance(reranker, str):
        raise _bad(f + ".reranker", "must be a string or null")
    if not isinstance(filters, dict):
        raise _bad(f + ".metadata_filters", "must be an object")
    if not isinstance(hybrid, bool):
        raise _bad(f + ".hybrid", "must be a boolean")
    return {"top_k": _num(raw.get("top_k", 6), f + ".top_k", 1, 20, integer=True),
            "similarity_threshold": float(_num(raw.get("similarity_threshold", 0.2), f + ".similarity_threshold",
                                               0, 1, integer=False)),
            "max_ctx_tokens": _num(raw.get("max_ctx_tokens", 2000), f + ".max_ctx_tokens", 100, 8000, integer=True),
            "hybrid": hybrid, "reranker": reranker, "metadata_filters": filters}


async def _build_bindings(session: AsyncSession, raw: list, ap: PolicyPrincipal, ws: str) -> list[RetrievalBinding]:
    if len(raw) > _MAX_BINDINGS:
        raise _bad("retrieval_bindings", f"at most {_MAX_BINDINGS} knowledge bases per version")
    seen: set[str] = set()
    out = []
    for i, b in enumerate(raw):
        if not isinstance(b, dict) or set(b) - {"kb_id", "injection", "retrieval_config"}:
            raise _bad(f"retrieval_bindings[{i}]", "expected {kb_id, injection, retrieval_config}")
        kb_raw, injection = b.get("kb_id"), b.get("injection", "always_on")
        if injection not in ("always_on", "tool"):
            raise _bad(f"retrieval_bindings[{i}].injection", "must be always_on or tool")
        cfg = _retrieval_config(b.get("retrieval_config", {}), i)
        try:
            kid = uuid.UUID(str(kb_raw))
        except (ValueError, TypeError):
            raise _bad(f"retrieval_bindings[{i}].kb_id", "must be a knowledge base id") from None
        if str(kid) in seen:
            raise _bad(f"retrieval_bindings[{i}].kb_id", "duplicate knowledge base")
        seen.add(str(kid))
        not_found = NotFound("knowledge base not found", detail={"kb_id": str(kb_raw)})
        kb = (await session.execute(select(KnowledgeBase).where(KnowledgeBase.id == kid))).scalar_one_or_none()
        if kb is None:
            raise not_found
        ensure_manage(kb.access_policy, ap, not_found=not_found)        # binding is an edit of the KB's use: 404→403
        if kb.archived_at is not None:
            raise KbArchived("the knowledge base is archived", detail={"kb_id": str(kid)})
        out.append(RetrievalBinding(workspace_id=uuid.UUID(ws), kb_id=kid, kb_index_version=kb.index_version,
                                    retrieval_config=cfg, injection=injection, kb=kb))
    return out


@router.post("/{agent_id}/versions", status_code=201, dependencies=[Depends(require_permission("agent.edit")), Depends(rate_limit("default"))])
async def create_version(
    agent_id: str,
    ws: str = Depends(resolve_workspace),
    session: AsyncSession = Depends(tenant_db),
    ap: PolicyPrincipal = Depends(access_principal),
    label: str | None = None,
    body: VersionCreate | None = Body(default=None),
) -> VersionOut:
    agent = await _load_agent(session, agent_id)
    if agent is None or not agent.versions:
        raise NotFound("agent (or its base version) not found", detail={"agent_id": agent_id})
    # feature 013: creating a version is an edit → MANAGE gate (404-then-403) after RBAC.
    ensure_manage(agent.access_policy, ap,
                  not_found=NotFound("agent (or its base version) not found", detail={"agent_id": agent_id}))
    latest = sorted(agent.versions, key=lambda v: v.created_at)[-1]
    if body is not None and body.label is not None:
        label = body.label
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
    # Feature 024 (research D12): bindings authored here; omitted ⇒ COPY the latest version's (G2 — a new version
    # must not silently drop knowledge). Tool/skill bindings keep their pre-024 behaviour.
    if body is not None and body.retrieval_bindings is not None:
        retrieval = await _build_bindings(session, body.retrieval_bindings, ap, ws)
    else:
        retrieval = [RetrievalBinding(workspace_id=b.workspace_id, kb_id=b.kb_id, kb_index_version=b.kb_index_version,
                                      retrieval_config=dict(b.retrieval_config or {}), injection=b.injection, kb=b.kb)
                     for b in latest.retrieval_bindings]
    clone.retrieval_bindings, clone.tool_bindings, clone.skill_bindings = retrieval, [], []
    clone.snapshot_hash = compute_snapshot_hash(clone)
    session.add(clone)
    await session.flush()
    return _version_out(clone, ap)
