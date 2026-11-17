"""Eval-run + advisory publish router (M4, feature 005, US4).

Eval-run executes the M3 `evals.run_eval` inline over the SAME runtime (P6), with the suite supplied in the
body (D5); it persists nothing. Publish is ADVISORY (D6/owner decision #3): it returns the eval verdict but
NEVER raises `publish.eval_gate` — the uncalibrated judge gates nothing.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.resilience_wiring import get_guard
from api.access_policy import PolicyPrincipal
from api import audit, call_log
from api.correlation import Correlation, correlation
from api.deps import (Principal, access_principal, enforce_spend_cap, ensure_manage, ensure_use, require_permission,
                      rate_limit, resolve_workspace, session_principal, tenant_db)
from api.provider_keys import resolve_provider_key
from api.run_sink import persist_run_record
from api.retrieval_wiring import attach_knowledge, ensure_eval_can_use_knowledge
from api.db_errors import check_active_version_now, integrity_mapped
from api.errors import AgentArchived, ConflictStale, NotFound
from api.models import Agent, EvalRun
from api.routers.agents import _version_belongs
from api.routers.invoke import _load_version
from api.schemas.evals import (Advisory, DimensionOut, EvalRunIn, EvalRunOut, FailingCaseOut, PublishIn,
                               PublishOut)
from evals.drift import EvalPolicy
from evals.runner import run_eval
from evals.types import BaselineAggregate, EvalCase, RubricDimension
from runtime.context import RunContext

router = APIRouter(prefix="/api/v1/agents", tags=["evals"])


@router.post("/{agent_id}/eval-runs",
             dependencies=[Depends(require_permission("eval.run")), Depends(rate_limit("eval")), Depends(enforce_spend_cap)])
async def create_eval_run(
    agent_id: str,
    body: EvalRunIn,
    response: Response,
    ws: str = Depends(resolve_workspace),
    session: AsyncSession = Depends(tenant_db),
    ap: PolicyPrincipal = Depends(access_principal),   # feature 013: access-policy caller view
    cor: Correlation = Depends(correlation),  # F13 inbound correlation id (else generated) + 023 provider id
) -> EvalRunOut:
    corr, provider_corr = cor.id, cor.provider_id
    response.headers["X-Correlation-Id"] = corr   # echo resolved id
    # feature 013: eval-run is a USE of the agent (it invokes it) — deny fail-closed BEFORE loading the version
    # or calling runtime (no Run row, no provider call). 404 indistinguishable from an absent agent (P5).
    try:
        aid = uuid.UUID(agent_id)
    except ValueError:
        raise NotFound("agent not found", detail={"agent_id": agent_id})
    # Read ONLY the access_policy column for the gate (not the whole row); _load_version loads the version next.
    # NOT NULL column → None ⇒ absent row (same light-select pattern as patch_agent).
    row = (await session.execute(
        select(Agent.access_policy, Agent.status).where(Agent.id == aid))).first()
    if row is None:
        raise NotFound("agent not found", detail={"agent_id": agent_id})
    ensure_use(row.access_policy, ap,
               not_found=NotFound("agent not found", detail={"agent_id": agent_id}))
    # 026 review-2 #7 (D4): an archived agent stops answering on EVERY plane — eval included (no case, no spend).
    if row.status == "archived":
        raise AgentArchived("an archived agent is read-only; restore it first", detail={"agent_id": agent_id})

    version = await _load_version(session, agent_id, body.version_id)
    if version is None:
        raise NotFound("agent version not found", detail={"agent_id": agent_id, "version_id": body.version_id})
    candidate_snapshot = version.to_snapshot()
    # Feature 024 / H1: the caller must be able to USE every bound KB — before any case runs (no run, no cost).
    await ensure_eval_can_use_knowledge(session, candidate_snapshot, ap)

    cases = [
        EvalCase(
            id=c.id, input=c.input, workspace_id=ws,
            rubric=tuple(RubricDimension(name=d.name, weight=d.weight) for d in c.rubric),
            required_fields=tuple(c.required_fields) if c.required_fields else None,
            expected=c.expected,
        )
        for c in body.cases
    ]
    baseline = BaselineAggregate(
        quality=body.baseline.quality, cost_usd=body.baseline.cost_usd,
        latency_ms=body.baseline.latency_ms, per_case=body.baseline.per_case, workspace_id=ws,
    )
    policy = EvalPolicy(
        min_cases=body.policy.min_cases, quality_threshold=body.policy.quality_threshold,
        efficiency_budget_pct=body.policy.efficiency_budget_pct,
        allow_low_confidence_override=body.policy.allow_low_confidence_override,
    )
    # feature 014: resolve the workspace's active provider key for the candidate's provider from custody (same
    # seam as invoke) — fail closed (503 config_error) before any provider/judge call if none resolves.
    provider = (candidate_snapshot.model_config or {}).get("provider", "")
    api_key = await resolve_provider_key(session, ws, provider)

    runs: list = []
    ctx = RunContext(workspace_id=ws, source="eval", api_key=api_key, guard=get_guard(),  # 025: process Guard
                     record_run=runs.append, correlation_id=corr, deadline_s=30.0)
    # Feature 023: one call-log line per model call (metadata by default; redacted text only when effective).
    _, content_logging = await call_log.effective_content_logging(session, ws)
    ctx.record_call = call_log.make_call_sink(workspace_id=ws, api_key=api_key, content_logging=content_logging)
    ctx.content_logging = content_logging
    ctx.provider_correlation_id = provider_corr   # FR-014: providers only ever see a platform-generated id
    # The gate above proved access; retrieval reads the frozen snapshot so a mid-run policy change can't fork results.
    await attach_knowledge(session, ws, candidate_snapshot, ctx, None, bypass_access=True)

    result = await run_eval(cases, candidate_snapshot, baseline, policy, ctx, workspace_id=ws)

    # Persist only the CANDIDATE invocations as durable eval runs (source='eval'). The shared record_run sink
    # also collects the JUDGE invocations (judge.py reuses the same ctx; its snapshot has version_id="judge"),
    # but a judge call is a scoring detail, not an eval run of the agent — recording it would double-count eval
    # history/metrics and add judge cost to spend. Filter to the candidate version (F1).
    candidate_vid = str(candidate_snapshot.version_id)
    for rec in runs:
        if str(rec.version_id) == candidate_vid:
            await persist_run_record(session, ws, rec)

    return EvalRunOut(
        verdict=result.verdict, reason=result.reason, quality=result.quality,
        quality_delta=result.quality_delta, cost_delta=result.cost_delta, latency_delta=result.latency_delta,
        cases=result.cases,
        dimensions=[DimensionOut(name=d.name, candidate=d.candidate, baseline=d.baseline, delta=d.delta)
                    for d in result.dimensions],
        failing_cases=[FailingCaseOut(case_id=f.case_id, reason=f.reason, hard=f.hard)
                       for f in result.failing_cases],
        pairwise_rate=result.pairwise_rate,
    )


@router.post("/{agent_id}/publish", dependencies=[Depends(require_permission("agent.publish")), Depends(rate_limit("default"))])
async def publish_version(
    agent_id: str,
    body: PublishIn,
    ws: str = Depends(resolve_workspace),
    session: AsyncSession = Depends(tenant_db),
    ap: PolicyPrincipal = Depends(access_principal),
    principal: Principal = Depends(session_principal),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> PublishOut:
    try:
        aid = uuid.UUID(agent_id)
    except ValueError:
        raise NotFound("agent not found", detail={"agent_id": agent_id})
    q = select(Agent).where(Agent.id == aid)
    agent = (await session.execute(q.with_for_update() if if_match else q)).scalar_one_or_none()
    if agent is None:
        raise NotFound("agent not found", detail={"agent_id": agent_id})
    # feature 013: publish is a MANAGE op (404-then-403) — after the agent.publish RBAC gate.
    ensure_manage(agent.access_policy, ap, not_found=NotFound("agent not found", detail={"agent_id": agent_id}))
    # 026 US3 review #1: publish re-points active_version_id, so — like PATCH re-point — it honours If-Match when sent
    # (a stale console tab must not silently undo a newer re-point). Optional: API callers without it keep the
    # pre-026 behaviour. The row is locked above, so the xmin compare can't race a concurrent PATCH.
    if if_match:
        cur = (await session.execute(text("select xmin::text as etag from agents where id = :id"),
                                     {"id": agent_id})).scalar_one()
        if if_match.strip() != cur:
            raise ConflictStale("stale If-Match ETag; the agent was modified concurrently", detail={"agent_id": agent_id})
    # The published version MUST belong to THIS agent (validated/scoped) — never a foreign version, never a
    # malformed id landing as a 500 / FK error (F2).
    # 026 review-2 #1: publishing must not resurrect an archived agent — restore (admin, agent.delete) comes first.
    if agent.status == "archived":
        raise AgentArchived("an archived agent is read-only; restore it first", detail={"agent_id": agent_id})
    vid = await _version_belongs(session, agent_id, body.version_id)
    prior = agent.status
    # ADVISORY (D6): publish proceeds regardless of eval state — NEVER raise publish.eval_gate here.
    agent.status = "published"
    agent.active_version_id = vid
    async with integrity_mapped():   # 025 US4: pre-check race → deferred same-agent FK → 400 input.invalid
        await check_active_version_now(session)
    # 026 D5: the advisory is READ from persisted runs — no provider call, zero cost, never blocks.
    run = (await session.execute(
        select(EvalRun).where(EvalRun.version_id == uuid.UUID(str(vid)), EvalRun.status == "completed")
        .order_by(EvalRun.finished_at.desc().nulls_last(), EvalRun.id.desc()).limit(1))).scalar_one_or_none()
    advisory = (Advisory(verdict=run.verdict, reason_code=run.reason_code, eval_run_id=str(run.id),
                         suite_id=str(run.suite_id)) if run is not None
                else Advisory(verdict="low_confidence", reason_code="no_eval_run"))
    # FR-023: audited in the SAME transaction as the change. D10: meta is ids only (the verdict joins via the run).
    await audit.record(session, principal, "agent.publish", "agent", aid,
                       {"prior_status": prior, "version_id": vid, "eval_run_id": advisory.eval_run_id})
    return PublishOut(agent_id=agent_id, version_id=body.version_id, status="published", advisory=advisory,
                      advisory_verdict=advisory.verdict)
