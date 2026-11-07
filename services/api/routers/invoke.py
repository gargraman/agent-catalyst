"""SSE invoke router (M4, feature 005, US1).

Wraps the SAME `runtime.invoke` (P6) — resolves the frozen version snapshot (tenant-scoped), then streams its
normalized events re-encoded as SSE. No pipeline logic here. The snapshot is materialized BEFORE streaming so
the generator holds only the pure dataclass (not the request DB session). Fully mocked at the gateway in tests.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api import sse
from api.correlation import correlation_id
from api.deps import enforce_spend_cap, require_permission, PROVIDER_API_KEY, rate_limit, resolve_workspace, tenant_db
from api.run_sink import persist_run_record
from api.errors import NotFound
from api.models import Agent, AgentVersion, RetrievalBinding
from api.schemas.invoke import InvokeIn
from runtime.context import RunContext
from runtime.invoke import invoke as runtime_invoke
from runtime.providers.ir import EventType

router = APIRouter(prefix="/api/v1/agents", tags=["invoke"])


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


@router.post("/{agent_id}/invoke",
             dependencies=[Depends(require_permission("playground.use")), Depends(enforce_spend_cap)])
async def invoke_agent(
    agent_id: str,
    body: InvokeIn,
    ws: str = Depends(resolve_workspace),
    session: AsyncSession = Depends(tenant_db),
    corr: str = Depends(correlation_id),      # F13: inbound traceparent / X-Correlation-Id, else generated
    _rl: None = Depends(rate_limit),
):
    version = await _load_version(session, agent_id, body.version_id)
    if version is None:
        raise NotFound("agent version not found", detail={"agent_id": agent_id, "version_id": body.version_id})
    snapshot = version.to_snapshot()          # pure frozen dataclass — safe to use after the session closes

    runs: list = []
    # Mint the run id here so the run_start frame and the final run (done frame) name the SAME id; correlation
    # is a separate concept carried via ctx.correlation_id + the X-Correlation-Id header (code-review fix).
    run_id = uuid.uuid4().hex
    ctx = RunContext(workspace_id=ws, source="playground", api_key=PROVIDER_API_KEY,
                     record_run=runs.append, correlation_id=corr, run_id=run_id, deadline_s=30.0)

    async def _event_stream():
        yield sse.frame("run_start", {"run_id": run_id, "version": snapshot.version_id,
                                      "model": (snapshot.model_config or {}).get("model", "")})
        error_emitted = False
        async for ev in runtime_invoke(snapshot, body.inputs, ctx):
            if ev.type is EventType.ERROR:
                error_emitted = True
            encoded = sse.encode_event(ev)
            if encoded is not None:
                yield encoded
        run = runs[-1] if runs else None
        if run is not None:
            await persist_run_record(session, ws, run)   # durable run record on the request session (FR-013, P8)
        # Surface a pre-call rejection (invalid_input/context_overflow/config_error) that produced no ERROR event.
        if run is not None and run.error and not error_emitted:
            yield sse.error_frame(run.error.get("type", "error"), run.error.get("message", ""))
        status = run.outcome.value if run is not None else "unknown"
        yield sse.frame("done", {"run_id": run.run_id if run is not None else "", "status": status})

    return StreamingResponse(_event_stream(), media_type="text/event-stream",
                             headers={"X-Correlation-Id": corr})   # echo resolved id (F13)
