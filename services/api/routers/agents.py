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

from api import audit, drafts
from api.access_policy import PolicyPrincipal, can_use
from api.db_errors import check_active_version_now, integrity_mapped
from api.deps import (access_principal, ensure_manage, ensure_use, require_permission, idempotency_guard,
                      Principal, rate_limit, resolve_workspace, session_principal, tenant_db)
from api.errors import AccessDenied, AgentArchived, ConflictStale, InputInvalid, KbArchived, NotFound
from api.permissions import has_permission
from api.models import Agent, AgentVersion, KnowledgeBase, RetrievalBinding, Skill, SkillBinding, Tool, ToolBinding
from runtime.hashing import compute_snapshot_hash
from runtime.providers.profiles import _PROFILE_DIR
from api.pagination import Page, clamp_limit, decode_cursor, encode_cursor
from api.schemas.access_policy import policy_for_store, policy_out, stamp_private_owner, validate_access_policy
from api.schemas.agents import (AgentCreate, AgentOut, AgentPatch, DraftOut, RetrievalBindingOut, VersionCreate,
                                VersionOut)

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


# Feature 024: every path that renders versions eager-loads bindings + their KB (async: no lazy loads; masking needs
# the KB's live access policy).
# Feature 026: tool + skill bindings too — the resolved draft projects them, and a NULL-draft version clones them.
_VERSIONS = (
    selectinload(Agent.versions).selectinload(AgentVersion.retrieval_bindings).selectinload(RetrievalBinding.kb),
    selectinload(Agent.versions).selectinload(AgentVersion.tool_bindings),
    selectinload(Agent.versions).selectinload(AgentVersion.skill_bindings),
)


def _binding_out(b: RetrievalBinding, ap: PolicyPrincipal) -> RetrievalBindingOut:
    if not can_use(b.kb.access_policy, ap):                  # H2: never reveal a KB the viewer may not use
        return RetrievalBindingOut(kb_id=None, kb_name=None, injection=b.injection, retrieval_config=b.retrieval_config,
                                   embedding_model=None, kb_index_version=None, restricted=True)
    return RetrievalBindingOut(kb_id=str(b.kb_id), kb_name=b.kb.name, injection=b.injection,
                               retrieval_config=b.retrieval_config, embedding_model=b.kb.embedding_model,
                               kb_index_version=b.kb_index_version, restricted=False)


def _version_out(v: AgentVersion, ap: PolicyPrincipal, active_id: uuid.UUID | None = None) -> VersionOut:
    out = VersionOut(id=str(v.id), agent_id=str(v.agent_id), label=v.label, snapshot_hash=v.snapshot_hash,
                     prompt_hash=v.prompt_hash, publishable=v.publishable, created_at=v.created_at,
                     created_by=v.created_by, is_active=active_id is not None and v.id == active_id)
    out.retrieval_bindings = [_binding_out(b, ap) for b in sorted(v.retrieval_bindings, key=lambda b: str(b.kb_id))]
    return out


def _active_version(a: Agent) -> AgentVersion | None:
    return next((v for v in a.versions if v.id == a.active_version_id), None)


_BINDING_MODELS = {"kb_ids": KnowledgeBase, "tool_ids": Tool, "skill_ids": Skill}


async def _hidden_ids(session: AsyncSession, resolved_drafts: list[dict | None], ap: PolicyPrincipal) -> frozenset[str]:
    """Ids named by these drafts that the caller may NOT use (P5). One query per kind for the whole page (no N+1).
    An id with no visible row (deleted / other workspace) is NOT hidden: it names nothing the caller can't already
    see, and keeping it visible lets the console say "a bound knowledge base no longer exists"."""
    hidden: set[str] = set()
    for field, model in _BINDING_MODELS.items():
        ids = {uuid.UUID(i) for d in resolved_drafts if d for i in d[field]}
        if not ids:
            continue
        rows = (await session.execute(select(model.id, model.access_policy).where(model.id.in_(ids)))).all()
        hidden.update(str(r.id) for r in rows if not can_use(r.access_policy, ap))
    return frozenset(hidden)


def _agent_out(a: Agent, ap: PolicyPrincipal, hidden: frozenset[str]) -> AgentOut:
    """`hidden` = draft binding ids the caller may not use (from `_hidden_ids`) — required, so no caller can forget
    the masking. Version bindings are masked separately by `_binding_out`."""
    active = _active_version(a)
    resolved = drafts.resolve(a, active)
    if resolved is not None:
        masked = {f: [i for i in resolved[f] if i not in hidden] for f in _BINDING_MODELS}
        n_hidden = sum(len(resolved[f]) - len(masked[f]) for f in _BINDING_MODELS)
        resolved = {**resolved, **masked, "restricted_bindings": n_hidden}
    newest_first = sorted(a.versions, key=lambda v: (v.created_at, str(v.id)), reverse=True)
    return AgentOut(id=str(a.id), name=a.name, status=a.status,
                    active_version_id=str(a.active_version_id) if a.active_version_id else None,
                    workspace_id=str(a.workspace_id), created_at=a.created_at,
                    access_policy=policy_out(a.access_policy),
                    versions=[_version_out(v, ap, a.active_version_id) for v in newest_first],
                    description=a.description or "",
                    provider=resolved["provider"] if resolved else None,
                    model=resolved["model"] if resolved else None,
                    draft=DraftOut(**resolved) if resolved else None,
                    draft_dirty=drafts.dirty(a, active),
                    restore_status=a.status_before_archive if a.status == "archived" else None)


async def _load_agent(session: AsyncSession, agent_id: str) -> Agent | None:
    try:
        aid = uuid.UUID(agent_id)
    except ValueError:
        return None
    stmt = select(Agent).where(Agent.id == aid).options(*_VERSIONS).execution_options(populate_existing=True)
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
            created_by=principal.subject,   # 026 D2: the initial v1 records its creator too (found by T029 L3)
        )
        # The AUTHORITATIVE content hash the runtime/RunRecord use (compute over empty bindings pre-flush —
        # no I/O). Stored == to_snapshot() recomputes, so version-id ↔ run content-hash comparisons hold.
        v.retrieval_bindings, v.tool_bindings, v.skill_bindings = [], [], []
        v.snapshot_hash = compute_snapshot_hash(v)
        session.add(v)
        await session.flush()
        agent.active_version_id = v.id
        async with integrity_mapped():
            await check_active_version_now(session)
        await session.refresh(agent, ["versions"])
        response.headers["ETag"] = await _etag(session, str(agent.id))
        # a brand-new agent has no bindings, so masking cannot apply; the creator's own role is enough here
        return _agent_out(agent, PolicyPrincipal(role=principal.workspace_claims.get(ws, ""), teams=(),
                                                 subject=principal.subject), frozenset())


@router.get("", dependencies=[Depends(require_permission("view"))])
async def list_agents(
    ws: str = Depends(resolve_workspace),
    session: AsyncSession = Depends(tenant_db),
    ap: PolicyPrincipal = Depends(access_principal),   # feature 013
    limit: int | None = None,
    cursor: str | None = None,
) -> Page[AgentOut]:
    n = clamp_limit(limit)
    stmt = select(Agent).options(*_VERSIONS).order_by(Agent.created_at.desc(), Agent.id.desc())
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
    hidden = await _hidden_ids(session, [drafts.resolve(a, _active_version(a)) for a in rows], ap)
    return Page[AgentOut](items=[_agent_out(a, ap, hidden) for a in rows], next_cursor=next_cursor)


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
    return _agent_out(agent, ap, await _hidden_ids(session, [drafts.resolve(agent, _active_version(agent))], ap))


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
    cur = (await session.execute(
        select(Agent.access_policy, Agent.status, Agent.status_before_archive, Agent.active_version_id)
        .where(Agent.id == _aid))).first()
    if cur is None:
        raise NotFound("agent not found", detail={"agent_id": agent_id})
    ensure_manage(cur.access_policy, ap, not_found=NotFound("agent not found", detail={"agent_id": agent_id}))
    # 026 D4: an archived agent is read-only — the ONLY accepted PATCH is a bare status transition out of `archived`
    # (validated as a restore below). Checked before payload validation so the state, not the payload, explains it.
    if cur.status == "archived":
        other = (body.name, body.description, body.draft, body.access_policy, body.active_version_id)
        if any(f is not None for f in other) or body.status in (None, "archived"):
            raise AgentArchived("an archived agent is read-only; restore it first", detail={"agent_id": agent_id})
    # A re-point MUST target a version of THIS agent (validated/scoped) — never a foreign version or a
    # malformed id landing as a 500 (F3).
    new_av = None
    if body.active_version_id is not None:
        new_av = await _version_belongs(session, agent_id, body.active_version_id)
    # Feature 026 (D4) lifecycle. `cur` may be NEWER than the caller's ETag — harmless: the UPDATE below is guarded by
    # `xmin = If-Match`, so a decision made on a row the caller never saw can't commit (it 409s instead).
    sba_set, sba_val, events = False, None, []
    if body.status is not None and body.status != cur.status:
        if cur.status == "archived":
            restore_to = cur.status_before_archive or "draft"
            if body.status != restore_to:
                raise InputInvalid(f"an archived agent can only be restored to its prior status '{restore_to}'",
                                   detail={"field": "status", "restore_status": restore_to})
            sba_set, events = True, [("agent.restore", {"restored_to": restore_to})]
        elif body.status == "archived":
            sba_set, sba_val, events = True, cur.status, [("agent.archive", {"prior_status": cur.status})]
        elif body.status == "published":
            # 026 review-2 #2: publishing via PATCH is the same act as POST /publish — publisher-gated + audited.
            events = [("agent.publish", {"prior_status": cur.status,
                                         "version_id": new_av or cur.active_version_id})]
        else:
            events = [("agent.status", {"from_status": cur.status, "to_status": body.status})]
    if new_av is not None and new_av != cur.active_version_id:
        events.append(("agent.repoint", {"from_version_id": cur.active_version_id, "to_version_id": new_av}))
    # 026 (decision #9 + review-2 #2): the route's `agent.edit` (builder+) covers ordinary edits; EVERY lifecycle
    # transition carries the console's stricter gate server-side — archive/restore = `agent.delete` (admin); re-point,
    # publish/unpublish and any other status change = `agent.publish` (publisher). Only an actual transition is gated:
    # re-sending the current version/status stays a builder no-op.
    role = principal.workspace_claims.get(ws, "")
    for kind, _ in events:
        needed = "agent.delete" if kind in ("agent.archive", "agent.restore") else "agent.publish"
        if not has_permission(role, needed):
            raise AccessDenied("insufficient role for this operation",
                               detail={"required_permission": needed, "transition": kind})
    # feature 013: validate + owner-stamp the NEW policy. Named distinctly from the `ap` PolicyPrincipal dep
    # above so the caller view is never shadowed (a later ensure_*/can_use must still see the principal).
    new_policy = validate_access_policy(body.access_policy)   # None when omitted → coalesce keeps existing
    new_policy = stamp_private_owner(new_policy, principal.subject)   # FR-012: edit-to-private → stamp the editor
    new_draft = None
    if body.draft is not None:
        new_draft = drafts.normalize(body.draft)
        # P5: the caller never saw ids they may not use (masked on read), so a full replace must not drop them.
        before = await _load_agent(session, agent_id)
        prev = drafts.resolve(before, _active_version(before)) if before is not None else None
        if prev is not None:
            keep = await _hidden_ids(session, [prev], ap)
            for f in _BINDING_MODELS:
                new_draft[f] = sorted(set(new_draft[f]) | {i for i in prev[f] if i in keep})
    async with integrity_mapped():   # 025 US4: a pre-check race → the deferred same-agent FK refuses → 400, not 500
        res = await session.execute(
            text("update agents set name = coalesce(:name, name), status = coalesce(:status, status), "
                 "active_version_id = coalesce(:av, active_version_id), "
                 "access_policy = coalesce(cast(:policy as jsonb), access_policy), "
                 "description = coalesce(:description, description), "
                 "draft = coalesce(cast(:draft as jsonb), draft), "
                 "status_before_archive = case when :sba_set then cast(:sba as agent_status) "
                 "else status_before_archive end "
                 "where id = :id and xmin = cast(:etag as xid) returning id"),
            {"name": body.name, "status": body.status,
             "av": str(body.active_version_id) if body.active_version_id else None,
             "policy": _json.dumps(new_policy) if new_policy is not None else None,
             "description": body.description,
             "draft": _json.dumps(new_draft) if new_draft is not None else None,
             "sba_set": sba_set, "sba": sba_val,
             "id": agent_id, "etag": etag_val},
        )
        updated = res.first() is not None
        if updated and body.active_version_id is not None:
            await check_active_version_now(session)
    if not updated:
        # distinguish a stale ETag (row exists) from a missing/cross-tenant row (RLS-hidden).
        exists = (await session.execute(text("select 1 from agents where id = :id"), {"id": agent_id})).first()
        if exists:
            raise ConflictStale("stale If-Match ETag; the agent was modified concurrently",
                                detail={"agent_id": agent_id})
        raise NotFound("agent not found", detail={"agent_id": agent_id})
    # FR-023: audit in the SAME transaction — a later failure rolls the audit row back with the change.
    for action, meta in events:
        await audit.record(session, principal, action, "agent", _aid, meta)
    agent = await _load_agent(session, agent_id)
    response.headers["ETag"] = await _etag(session, agent_id)
    return _agent_out(agent, ap, await _hidden_ids(session, [drafts.resolve(agent, _active_version(agent))], ap))


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


def _missing_binding(kind: str, rid) -> NotFound:
    """FR-005 / D2: the id comes from the caller's OWN draft, so naming it leaks nothing. A binding the caller may not
    USE is reported identically (P5: denied ≡ absent)."""
    return NotFound(f"a bound {kind.replace('_', ' ')} no longer exists", detail={"binding": kind, "id": str(rid)})


_DEFAULT_INJECTION = "always_on"     # 024: retrieval-as-a-tool (US4) is parked, so always_on is the working mode


_EMPTY_ARGS = {"type": "object", "properties": {}}


def _tool_def(t: Tool) -> dict:
    """The provider-shaped def frozen into `schema_snapshot` (sent to the model as-is by `build_request`). Anthropic
    requires an `input_schema` object, so one is ALWAYS present: the tool's own `http_config.input_schema` when it is
    an object schema, else an empty-object schema (the registry has no schema column yet)."""
    s = (t.http_config or {}).get("input_schema")
    schema = dict(s) if isinstance(s, dict) and s.get("type") == "object" else dict(_EMPTY_ARGS)
    return {"name": t.name, "description": t.description, "input_schema": schema}


async def draft_bindings(session: AsyncSession, resolved: dict, base: AgentVersion | None, ap: PolicyPrincipal,
                          ws: str) -> tuple[list[RetrievalBinding], list[ToolBinding], list[SkillBinding]]:
    """Existence-check + snapshot every binding the draft names (D2). Tool/skill snapshots are taken from the LIVE
    registry row now — the version freezes them. A KB keeps the active version's retrieval settings when it was
    already bound there (the draft carries only ids), else the 024 defaults."""
    wsid = uuid.UUID(ws)
    prior = {b.kb_id: b for b in (base.retrieval_bindings if base is not None else ())}
    retrieval = []
    for kid in map(uuid.UUID, resolved["kb_ids"]):
        kb = (await session.execute(select(KnowledgeBase).where(KnowledgeBase.id == kid))).scalar_one_or_none()
        if kb is None:
            raise _missing_binding("knowledge_base", kid)
        ensure_use(kb.access_policy, ap, not_found=_missing_binding("knowledge_base", kid))
        if kb.archived_at is not None:
            raise KbArchived("the knowledge base is archived", detail={"kb_id": str(kid)})
        old = prior.get(kid)
        retrieval.append(RetrievalBinding(
            workspace_id=wsid, kb_id=kid, kb_index_version=kb.index_version, kb=kb,
            retrieval_config=dict(old.retrieval_config) if old else _retrieval_config({}, 0),
            injection=old.injection if old else _DEFAULT_INJECTION))
    tools = []
    for tid in map(uuid.UUID, resolved["tool_ids"]):
        t = (await session.execute(select(Tool).where(Tool.id == tid))).scalar_one_or_none()
        if t is None or t.archived_at is not None:
            raise _missing_binding("tool", tid)
        ensure_use(t.access_policy, ap, not_found=_missing_binding("tool", tid))
        tools.append(ToolBinding(
            workspace_id=wsid, kind=t.kind, tool_id=t.id,
            schema_snapshot=_tool_def(t),
            config_snapshot=dict(t.http_config or {}), risk_tier=t.risk_tier,
            requires_approval=t.risk_tier != "read"))
    skills = []
    for sid in map(uuid.UUID, resolved["skill_ids"]):
        sk = (await session.execute(select(Skill).where(Skill.id == sid))).scalar_one_or_none()
        if sk is None or sk.archived_at is not None:
            raise _missing_binding("skill", sid)
        ensure_use(sk.access_policy, ap, not_found=_missing_binding("skill", sid))
        skills.append(SkillBinding(workspace_id=wsid, skill_id=sk.id, skill_content_hash=sk.content_hash or ""))
    return retrieval, tools, skills


def _clone_bindings(base: AgentVersion) -> tuple[list[RetrievalBinding], list[ToolBinding], list[SkillBinding]]:
    """NULL draft ⇒ clone the active version EXACTLY, bindings included (G3: tool/skill were dropped before 026)."""
    retrieval = [RetrievalBinding(workspace_id=b.workspace_id, kb_id=b.kb_id, kb_index_version=b.kb_index_version,
                                  retrieval_config=dict(b.retrieval_config or {}), injection=b.injection, kb=b.kb)
                 for b in base.retrieval_bindings]
    tools = [ToolBinding(workspace_id=b.workspace_id, kind=b.kind, tool_id=b.tool_id, mcp_tool_id=b.mcp_tool_id,
                         subagent_config_id=b.subagent_config_id, schema_snapshot=b.schema_snapshot,
                         config_snapshot=b.config_snapshot, risk_tier=b.risk_tier,
                         requires_approval=b.requires_approval, auth_mode=b.auth_mode,
                         mcp_scopes=list(b.mcp_scopes) if b.mcp_scopes is not None else None)
             for b in base.tool_bindings]
    skills = [SkillBinding(workspace_id=b.workspace_id, skill_id=b.skill_id, skill_content_hash=b.skill_content_hash)
              for b in base.skill_bindings]
    return retrieval, tools, skills


@router.post("/{agent_id}/versions", status_code=201, dependencies=[Depends(require_permission("agent.edit")), Depends(rate_limit("default"))])
async def create_version(
    agent_id: str,
    ws: str = Depends(resolve_workspace),
    session: AsyncSession = Depends(tenant_db),
    principal: Principal = Depends(session_principal),
    ap: PolicyPrincipal = Depends(access_principal),
    label: str | None = None,
    body: VersionCreate | None = Body(default=None),
) -> VersionOut:
    """Feature 026 (D2): freeze the RESOLVED DRAFT into a new immutable version — config + tool/skill/KB bindings in
    ONE transaction (025's insert-window rule), stamped `created_by`. A NULL draft clones the active version exactly.
    The new version is NOT made active (re-point is a separate, audited PATCH). The 024 `retrieval_bindings` body,
    when given, still authors the KB bindings explicitly (it wins over the draft's kb_ids)."""
    agent = await _load_agent(session, agent_id)
    if agent is None or not agent.versions:
        raise NotFound("agent (or its base version) not found", detail={"agent_id": agent_id})
    # feature 013: creating a version is an edit → MANAGE gate (404-then-403) after RBAC.
    ensure_manage(agent.access_policy, ap,
                  not_found=NotFound("agent (or its base version) not found", detail={"agent_id": agent_id}))
    if agent.status == "archived":
        raise AgentArchived("an archived agent is read-only; restore it first", detail={"agent_id": agent_id})
    base = _active_version(agent) or sorted(agent.versions, key=lambda v: v.created_at)[-1]
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
    if agent.draft is None:
        retrieval, tools, skills = _clone_bindings(base)
    else:
        resolved = drafts.normalize(agent.draft)
        try:
            drafts.profile_doc(resolved["provider"], resolved["model"])
        except KeyError:   # a profile retired after the draft was saved — refuse typed, never a 500
            raise InputInvalid("the draft's model no longer has a capability profile",
                               detail={"provider": resolved["provider"], "model": resolved["model"]}) from None
        retrieval, tools, skills = await draft_bindings(session, resolved, base, ap, ws)
    if body is not None and body.retrieval_bindings is not None:
        retrieval = await _build_bindings(session, body.retrieval_bindings, ap, ws)
    if agent.draft is None:   # exact clone of the base's stored config (review #1/#2)
        v = drafts.clone_version(agent, base, label=new_label, created_by=principal.subject,
                                 retrieval=retrieval, tools=tools, skills=skills)
    else:
        v = drafts.version_from_draft(agent, resolved, label=new_label, base=base, created_by=principal.subject,
                                      retrieval=retrieval, tools=tools, skills=skills)
    session.add(v)
    async with integrity_mapped():   # 025 US4: a bound row deleted after the pre-check → typed 4xx, not 500
        await session.flush()
    return _version_out(v, ap, agent.active_version_id)
