"""SSE invoke router (M4, feature 005; published plane added feature 018).

Wraps the SAME `runtime.invoke` (P6) — resolves the frozen version snapshot (tenant-scoped), then streams its
normalized events re-encoded as SSE. No pipeline logic here. The snapshot is materialized BEFORE streaming so
the generator holds only the pure dataclass (not the request DB session). Fully mocked at the gateway in tests.

TWO planes share ONE body (`_run_invoke`) so they can never diverge (08 §244-250): the console/playground plane
(`/api/v1/...`, session auth, `source="playground"`) and the published plane (`/v1/...`, caller-token auth,
`source="published"`). The ONLY differences are the auth dependency and the run source; both call the same
`runtime.invoke`. An admin revocation is honored mid-stream via a throttled deny-set re-check (feature 018, D3).
"""
from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api import call_log, sse
from api.resilience_wiring import get_guard
from api.access_policy import PolicyPrincipal
from api.auth import denyset
from api.correlation import Correlation, correlation
from api.deps import (access_principal, assert_within_spend_cap, caller_db, enforce_spend_cap, ensure_use,
                      rate_limit, require_caller, require_permission, resolve_workspace, tenant_db)
from api.provider_keys import resolve_provider_key
from api.run_sink import persist_run_record
from api.retrieval_wiring import attach_knowledge, citations_payload
from api.errors import NotFound
from api.models import Agent, AgentVersion, RetrievalBinding
from api.schemas.invoke import InvokeIn
from api.settings import get_settings
from runtime.context import RunContext
from runtime.invoke import invoke as runtime_invoke
from runtime.providers.ir import EventType

router = APIRouter(prefix="/api/v1/agents", tags=["invoke"])                  # console/playground plane (session)
published_router = APIRouter(prefix="/v1/agents", tags=["invoke"])            # published plane (caller token, 018)


async def _load_version(session: AsyncSession, agent_id: str, version_id: str | None) -> AgentVersion | None:
    try:
        aid = uuid.UUID(agent_id)
    except ValueError:
        return None
    if version_id is None:
        agent = (await session.execute(select(Agent).where(Agent.id == aid))).scalar_one_or_none()
        if agent is None or agent.active_version_id is None:
            return None
        vid = agent.active_version_id
    else:
        try:
            vid = uuid.UUID(version_id)
        except ValueError:
            return None
    stmt = (
        select(AgentVersion)
        .where(AgentVersion.id == vid, AgentVersion.agent_id == aid)
        .options(
            selectinload(AgentVersion.retrieval_bindings).selectinload(RetrievalBinding.kb),
            selectinload(AgentVersion.tool_bindings),
            selectinload(AgentVersion.skill_bindings),
        )
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _run_invoke(*, session: AsyncSession, ws: str, ap: PolicyPrincipal, agent_id: str, body: InvokeIn,
                      corr: str, provider_corr: str, source: str, deny_subject: str | None = None,
                      deny_token_id: str | None = None) -> StreamingResponse:
    """Shared invoke body for BOTH planes (P6 — same runtime.invoke). Access-gate → load snapshot → resolve key
    → stream, re-checking the deny-set between frames so an in-flight revoke is cut (feature 018, FR-012/D3)."""
    # feature 013: deny USE fail-closed BEFORE loading the version / opening the stream — no Run row, no provider
    # call. A denied invoke is a 404 indistinguishable from an absent agent (P5 no-leak).
    try:
        aid = uuid.UUID(agent_id)
    except ValueError:
        raise NotFound("agent not found", detail={"agent_id": agent_id})
    agent_policy = (await session.execute(
        select(Agent.access_policy).where(Agent.id == aid))).scalar_one_or_none()
    if agent_policy is None:
        raise NotFound("agent not found", detail={"agent_id": agent_id})
    ensure_use(agent_policy, ap, not_found=NotFound("agent not found", detail={"agent_id": agent_id}))

    version = await _load_version(session, agent_id, body.version_id)
    if version is None:
        raise NotFound("agent version not found", detail={"agent_id": agent_id, "version_id": body.version_id})
    snapshot = version.to_snapshot()          # pure frozen dataclass — safe to use after the session closes

    # feature 014: resolve the workspace's active provider key just-in-time; fail closed (503) BEFORE streaming.
    provider = (snapshot.model_config or {}).get("provider", "")
    api_key = await resolve_provider_key(session, ws, provider)

    runs: list = []
    run_id = uuid.uuid4().hex
    ctx = RunContext(workspace_id=ws, source=source, api_key=api_key, guard=get_guard(),  # 025: process Guard
                     record_run=runs.append, correlation_id=corr, run_id=run_id, deadline_s=30.0)
    # Feature 023: one call-log line per model call (metadata by default; redacted text only when effective).
    _, content_logging = await call_log.effective_content_logging(session, ws)
    ctx.record_call = call_log.make_call_sink(workspace_id=ws, api_key=api_key, content_logging=content_logging)
    ctx.content_logging = content_logging
    ctx.provider_correlation_id = provider_corr   # FR-014: providers only ever see a platform-generated id
    # Feature 024: retrieval on the caller's RLS session with the caller's CURRENT access (both planes, same seam).
    ka = await attach_knowledge(session, ws, snapshot, ctx, ap)
    published = source == "published"

    recheck_s = get_settings().denyset_stream_recheck_s
    has_deny_target = deny_subject is not None or deny_token_id is not None

    async def _revoked_midstream(last: list[float]) -> bool:
        """Throttled deny-set re-check (feature 018, D3): at most every `recheck_s` seconds (0 ⇒ every frame).
        Fail closed — a deny-set outage mid-stream cuts the stream rather than letting it complete unchecked."""
        if not has_deny_target:
            return False
        now = time.monotonic()
        if recheck_s and (now - last[0]) < recheck_s:
            return False
        last[0] = now
        try:
            return await denyset.is_denied(deny_subject, deny_token_id)
        except Exception:                      # noqa: BLE001 — deny-set unreachable mid-stream → cut (fail closed)
            return True

    async def _event_stream():
        yield sse.frame("run_start", {"run_id": run_id, "version": snapshot.version_id,
                                      "model": (snapshot.model_config or {}).get("model", "")})
        last_check = [0.0]
        error_emitted = False
        async for ev in runtime_invoke(snapshot, body.inputs, ctx):
            if await _revoked_midstream(last_check):       # admin revoked the caller mid-stream → cut (FR-012)
                yield sse.error_frame("auth.required", "caller access revoked")
                return
            if ev.type is EventType.ERROR:
                error_emitted = True
            if ev.type is EventType.CITATIONS:
                yield sse.frame("citations", citations_payload(ev.data, denied=ka.denied if ka else set(),
                                                               published=published))
                continue
            encoded = sse.encode_event(ev)
            if encoded is not None:
                yield encoded
        run = runs[-1] if runs else None
        if run is not None:
            await persist_run_record(session, ws, run)   # durable run record on the request session (FR-013, P8)
        if run is not None and run.error and not error_emitted:
            yield sse.error_frame(run.error.get("type", "error"), run.error.get("message", ""))
        status = run.outcome.value if run is not None else "unknown"
        yield sse.frame("done", {"run_id": run.run_id if run is not None else "", "status": status})

    return StreamingResponse(_event_stream(), media_type="text/event-stream",
                             headers={"X-Correlation-Id": corr})   # echo resolved id (F13)


@router.post("/{agent_id}/invoke",
             # Order is load-bearing (feature 016): authz (403) → rate limit (429) → spend cap (402) → handler.
             dependencies=[Depends(require_permission("playground.use")),
                           Depends(rate_limit("invoke")),
                           Depends(enforce_spend_cap)])
async def invoke_agent(
    agent_id: str,
    body: InvokeIn,
    ws: str = Depends(resolve_workspace),
    session: AsyncSession = Depends(tenant_db),
    ap: PolicyPrincipal = Depends(access_principal),   # feature 013: access-policy caller view
    cor: Correlation = Depends(correlation),  # F13 inbound traceparent / X-Correlation-Id, else generated (+023 provider id)
):
    """Console/playground plane — session auth, `run_source="playground"`. An admin revocation of this subject
    is cut mid-stream via the deny-set re-check (the same subject `session_principal` already checks per request)."""
    return await _run_invoke(session=session, ws=ws, ap=ap, agent_id=agent_id, body=body, corr=cor.id,
                             provider_corr=cor.provider_id, source="playground", deny_subject=ap.subject)


@published_router.post("/{agent_id}/invoke")
async def published_invoke(
    agent_id: str,
    body: InvokeIn,
    caller=Depends(require_caller),                     # 018: caller-token auth (401/429 fail-closed); FastAPI-cached
    session: AsyncSession = Depends(caller_db),         # RLS session for the token's workspace (reuses `caller`)
    cor: Correlation = Depends(correlation),
):
    """Published plane — caller-token auth (role `executer`), `run_source="published"`, tenant pinned by the token.
    Authorization is the per-resource access policy (keyed on the token's teams), NOT console RBAC."""
    # The token is bound to exactly one agent (data-model §api_tokens) — reject a mismatched path agent as absent.
    if agent_id != caller.agent_id:
        raise NotFound("agent not found", detail={"agent_id": agent_id})
    await assert_within_spend_cap(session, caller.workspace_id)     # same spend gate as console (no plane bypass)
    ap = PolicyPrincipal(role="executer", teams=tuple(caller.teams), subject=None)
    return await _run_invoke(session=session, ws=caller.workspace_id, ap=ap, agent_id=agent_id, body=body,
                             corr=cor.id, provider_corr=cor.provider_id, source="published", deny_subject=caller.subject,
                             deny_token_id=caller.token_id)
