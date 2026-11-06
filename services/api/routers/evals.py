"""Eval-run + advisory publish router (M4, feature 005, US4).

Eval-run executes the M3 `evals.run_eval` inline over the SAME runtime (P6), with the suite supplied in the
body (D5); it persists nothing. Publish is ADVISORY (D6/owner decision #3): it returns the eval verdict but
NEVER raises `publish.eval_gate` — the uncalibrated judge gates nothing.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import PROVIDER_API_KEY, rate_limit, resolve_workspace, tenant_db
from api.run_sink import persist_run_record
from api.errors import NotFound
from api.models import Agent
from api.routers.agents import _version_belongs
from api.routers.invoke import _load_version
from api.schemas.evals import DimensionOut, EvalRunIn, EvalRunOut, FailingCaseOut, PublishIn, PublishOut
from evals.drift import EvalPolicy
from evals.runner import run_eval
from evals.types import BaselineAggregate, EvalCase, RubricDimension
from runtime.context import RunContext

router = APIRouter(prefix="/api/v1/agents", tags=["evals"])


@router.post("/{agent_id}/eval-runs")
async def create_eval_run(
    agent_id: str,
    body: EvalRunIn,
    ws: str = Depends(resolve_workspace),
    session: AsyncSession = Depends(tenant_db),
    _rl: None = Depends(rate_limit),
) -> EvalRunOut:
    version = await _load_version(session, agent_id, body.version_id)
    if version is None:
        raise NotFound("agent version not found", detail={"agent_id": agent_id, "version_id": body.version_id})
    candidate_snapshot = version.to_snapshot()

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
    runs: list = []
    ctx = RunContext(workspace_id=ws, source="eval", api_key=PROVIDER_API_KEY,
                     record_run=runs.append, deadline_s=30.0)

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


@router.post("/{agent_id}/publish")
async def publish_version(
    agent_id: str,
    body: PublishIn,
    ws: str = Depends(resolve_workspace),
    session: AsyncSession = Depends(tenant_db),
    _rl: None = Depends(rate_limit),
) -> PublishOut:
    try:
        aid = uuid.UUID(agent_id)
    except ValueError:
        raise NotFound("agent not found", detail={"agent_id": agent_id})
    agent = (await session.execute(select(Agent).where(Agent.id == aid))).scalar_one_or_none()
    if agent is None:
        raise NotFound("agent not found", detail={"agent_id": agent_id})
    # The published version MUST belong to THIS agent (validated/scoped) — never a foreign version, never a
    # malformed id landing as a 500 / FK error (F2).
    vid = await _version_belongs(session, agent_id, body.version_id)
    # ADVISORY (D6): publish proceeds regardless of eval state — NEVER raise publish.eval_gate here.
    agent.status = "published"
    agent.active_version_id = vid
    await session.flush()
    return PublishOut(agent_id=agent_id, version_id=body.version_id, status="published", advisory_verdict=None)
