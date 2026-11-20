"""Eval-run + advisory publish router (M4, feature 005, US4).

Eval-run executes the M3 `evals.run_eval` inline over the SAME runtime (P6). Since 026 US4 the suite, baseline and
policy are server-held and every run is persisted (`eval_runs` + `eval_results`); history reads those rows. Publish is ADVISORY (D6/owner decision #3): it returns the eval verdict but
NEVER raises `publish.eval_gate` — the uncalibrated judge gates nothing.
"""
from __future__ import annotations

import datetime
import math
import statistics
import uuid

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy import select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from api.resilience_wiring import get_guard
from api.access_policy import PolicyPrincipal, can_use
from api import audit, call_log
from api.correlation import Correlation, correlation
from api.deps import (Principal, access_principal, enforce_spend_cap, ensure_manage, ensure_use, require_permission,
                      rate_limit, resolve_workspace, session_principal, tenant_db)
from api.provider_keys import resolve_provider_key
from api.run_sink import persist_run_record
from api.retrieval_wiring import attach_knowledge, ensure_eval_can_use_knowledge
from api.db_errors import check_active_version_now, integrity_mapped
from api.baseline import baseline_aggregate, case_diff, compared_quality, decide
from api.errors import AgentArchived, ConflictStale, InputInvalid, NotFound
from api.eval_store import (EVAL_CASE_CAP, TIMEOUT_REASON, applied_policy, persist_completed, persist_failed, suite_baselines,
                            to_engine_policy)
from api.models import Agent, AgentVersion, EvalResult, EvalRun, EvalSuite, WorkspaceSettings
from api.models import EvalCase as EvalSuiteCase
from api.pagination import Page, clamp_limit, decode_cursor, encode_cursor
from api.routers.agents import _version_belongs
from api.routers.invoke import _load_version
from api.schemas.evals import (Advisory, DimensionOut, EvalResultOut, EvalRunDetailOut, EvalRunIn, EvalRunOut,
                               FailingCaseOut, PublishIn, PublishOut, RunBaselineOut)
from evals.drift import efficiency_delta
from evals.runner import run_eval
from evals.types import BaselineAggregate, EvalCase, RubricDimension
from runtime.context import RunContext

router = APIRouter(prefix="/api/v1/agents", tags=["evals"])




def _uuid_or_404(value: str, what: str, key: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError):
        raise NotFound(f"{what} not found", detail={key: value}) from None


def _mean(xs) -> float | None:
    xs = [float(x) for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def _finite(x: float | None) -> float | None:
    return x if x is not None and math.isfinite(x) else None


def _scored(cs) -> bool:
    """A case the judge actually scored (a fail-closed / hard-failed case is a non-score, not a quality 0)."""
    return cs.judge is not None and not cs.judge.low_confidence


@router.post("/{agent_id}/eval-runs", status_code=201,
             dependencies=[Depends(require_permission("eval.run")), Depends(rate_limit("eval")), Depends(enforce_spend_cap)])
async def create_eval_run(
    agent_id: str,
    body: EvalRunIn,
    response: Response,
    ws: str = Depends(resolve_workspace),
    session: AsyncSession = Depends(tenant_db),
    ap: PolicyPrincipal = Depends(access_principal),   # feature 013: access-policy caller view
    principal: Principal = Depends(session_principal),
    cor: Correlation = Depends(correlation),  # F13 inbound correlation id (else generated) + 023 provider id
) -> EvalRunDetailOut:
    """026 US4: the suite, baseline and policy all come from the SERVER (O3 — body extras dropped unread); the run is
    persisted (completed or failed) with its per-case evidence, in this request's single transaction."""
    corr, provider_corr = cor.id, cor.provider_id
    response.headers["X-Correlation-Id"] = corr   # echo resolved id
    # feature 013: eval-run is a USE of the agent (it invokes it) — deny fail-closed BEFORE loading the version
    # or calling runtime (no Run row, no provider call). 404 indistinguishable from an absent agent (P5).
    aid = _uuid_or_404(agent_id, "agent", "agent_id")
    row = (await session.execute(
        select(Agent.access_policy, Agent.status, Agent.active_version_id).where(Agent.id == aid))).first()
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
    sid = _uuid_or_404(body.suite_id, "eval suite", "suite_id")
    # FOR KEY SHARE (the lock an FK takes) holds the suite for the run's whole transaction: a concurrent delete
    # (FOR UPDATE) waits and then sees this run's row → 409, instead of winning and turning our insert into a 500 that
    # rolls back the recorded provider spend (review #4). Non-key UPDATEs (rename, case edits) are NOT blocked.
    suite = (await session.execute(
        select(EvalSuite).where(EvalSuite.id == sid, EvalSuite.agent_id == aid)
        .with_for_update(read=True, key_share=True))).scalar_one_or_none()
    if suite is None:                                    # absent, another agent's, or another workspace's (RLS)
        raise NotFound("eval suite not found", detail={"suite_id": body.suite_id})
    case_rows = list((await session.execute(select(EvalSuiteCase).where(EvalSuiteCase.suite_id == sid)
                                            .order_by(EvalSuiteCase.created_at, EvalSuiteCase.id))).scalars())
    if not case_rows:
        raise InputInvalid("the eval suite has no cases", detail={"suite_id": body.suite_id})
    if len(case_rows) > EVAL_CASE_CAP:
        raise InputInvalid(f"an eval run is capped at {EVAL_CASE_CAP} cases; split the suite",
                           detail={"suite_id": body.suite_id, "cases": len(case_rows), "max": EVAL_CASE_CAP})

    candidate_snapshot = version.to_snapshot()
    # Feature 024 / H1: the caller must be able to USE every bound KB — before any case runs (no run, no cost).
    await ensure_eval_can_use_knowledge(session, candidate_snapshot, ap)

    # Server-held policy (decision b) + baseline (D7) + case identity (D8).
    settings_row = await session.get(WorkspaceSettings, uuid.UUID(ws))
    applied = applied_policy(settings_row.eval_policy if settings_row is not None else None)
    policy = to_engine_policy(applied)
    resolved = (await suite_baselines(session, [sid], row.active_version_id))[sid].resolved
    brun = resolved.run
    bresults = list((await session.execute(
        select(EvalResult).where(EvalResult.eval_run_id == brun.id))).scalars()) if brun is not None else []
    diff = case_diff(case_rows, bresults)
    bagg = baseline_aggregate(bresults, diff)

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

    # The runtime JSON-checks the final answer ONLY when output_format == "json" (runtime/invoke.py; bound tools add
    # tool-call checks, never a JSON answer) — so anything else, including an absent key, is a text agent here too.
    output_format = "json" if (candidate_snapshot.io_contract or {}).get("output_format") == "json" else "text"
    cases = [
        EvalCase(
            id=str(c.id), input=c.input, workspace_id=ws,
            rubric=tuple(RubricDimension(name=d["name"], weight=float(d.get("weight", 1.0))) for d in c.rubric),
            # Server-authored cases state their contract explicitly: an empty list means "no required fields",
            # never the MVP triage defaults; the candidate's output_format decides whether prose is valid.
            required_fields=tuple(c.required_fields or ()), expected=c.expected,
            output_format=output_format,
        )
        for c in case_rows
    ]
    # The engine still needs an aggregate to run; the VERDICT is re-decided below over the common cases only.
    engine_baseline = BaselineAggregate(quality=bagg.quality or 0.0, cost_usd=bagg.cost_usd,
                                        latency_ms=int(bagg.latency_ms or 0), per_case=bagg.per_case,
                                        workspace_id=ws)
    started = datetime.datetime.now(datetime.timezone.utc)
    result = await run_eval(cases, candidate_snapshot, engine_baseline, policy, ctx, workspace_id=ws,
                            stop_on_timeout=True)

    # Persist only the CANDIDATE invocations as durable runs (source='eval'). The shared record_run sink also
    # collects the JUDGE invocations (judge.py reuses the same ctx; its snapshot has version_id="judge"), but a judge
    # call is a scoring detail, not an eval run of the agent — recording it would double-count history and spend (F1).
    candidate_vid = str(candidate_snapshot.version_id)
    for rec in runs:
        if str(rec.version_id) == candidate_vid:
            await persist_run_record(session, ws, rec)

    common = {str(c.id) for c in case_rows if diff.in_comparison(c.id, c.content_hash)}
    has_baseline = resolved.origin != "none"
    run = EvalRun(
        workspace_id=uuid.UUID(ws), agent_id=aid, suite_id=sid, version_id=uuid.UUID(candidate_vid),
        baseline_run_id=brun.id if has_baseline else None,
        baseline_origin=resolved.origin if has_baseline else None,
        compared_cases=diff.compared, new_cases=diff.new, dropped_cases=diff.dropped, total_cases=diff.total,
        policy=applied, judge_version=result.judge_version or None, created_by=principal.subject,
        started_at=started, finished_at=datetime.datetime.now(datetime.timezone.utc), dimensions=[],
    )
    # E6: a provider deadline mid-run → a FAILED run (the failure is the evidence), no eval_results, no verdict.
    if any(cs.outcome == "timeout" for cs in result.case_scores):
        run.reason_code, run.reason = "timeout", TIMEOUT_REASON
        await persist_failed(session, run)
        return (await _project(session, [run], detail=True))[0]

    scores = result.case_scores
    cmp_scores = [cs for cs in scores if str(cs.case_id) in common]
    quality_all = _mean(cs.quality for cs in scores if _scored(cs))
    # Quality is compared over cases scored on BOTH sides (review #3) — never two different subsets.
    quality_b, quality_cmp = compared_quality(
        {str(cs.case_id): (cs.quality if _scored(cs) else None) for cs in cmp_scores}, bresults, diff)
    cost_all = float(sum(float(cs.candidate_cost_usd or 0.0) for cs in scores))
    cost_cmp = float(sum(float(cs.candidate_cost_usd or 0.0) for cs in cmp_scores))
    latency_all = _mean(cs.candidate_latency_ms for cs in scores)
    latency_cmp = _mean(cs.candidate_latency_ms for cs in cmp_scores)
    decision = decide(origin=resolved.origin, diff=diff, det_all_green=not any(cs.det.hard_fail for cs in scores),
                      judge_available=any(_scored(cs) for cs in cmp_scores) or not has_baseline,
                      quality_b=quality_b, quality_c=quality_cmp, cost_b=bagg.cost_usd, cost_c=cost_cmp,
                      policy=policy)
    run.status, run.verdict, run.reason_code, run.reason = (
        "completed", decision.verdict, decision.reason_code, decision.reason)
    run.quality, run.cost_usd, run.latency_ms = quality_all, cost_all, latency_all
    if has_baseline:
        run.quality_delta = (quality_cmp - quality_b
                             if quality_cmp is not None and quality_b is not None else None)
        run.cost_delta_pct = _finite(efficiency_delta(cost_cmp, bagg.cost_usd).cost_pct)
        run.latency_delta_ms = (latency_cmp - bagg.latency_ms
                                if latency_cmp is not None and bagg.latency_ms is not None else None)
    run.dimensions = [{"name": d.name, "candidate": d.candidate, "baseline": d.baseline, "delta": d.delta}
                      for d in result.dimensions]
    run.pairwise_rate = result.pairwise_rate
    failing = {str(f.case_id): f.reason for f in result.failing_cases}
    hashes = {str(c.id): c.content_hash for c in case_rows}
    results = [EvalResult(
        case_id=uuid.UUID(str(cs.case_id)), case_hash=hashes[str(cs.case_id)],
        run_id=uuid.UUID(cs.run_id) if _is_uuid(cs.run_id) else None,
        passed_deterministic=bool(cs.det.passed), quality=cs.quality if _scored(cs) else None,
        rubric_scores=dict(cs.judge.rubric_scores) if cs.judge is not None else {},
        pairwise_winner=cs.judge.pairwise_winner if cs.judge is not None else None,
        judge_low_confidence=bool(cs.judge.low_confidence) if cs.judge is not None else False,
        failure_reason=failing.get(str(cs.case_id)), hard_failure=bool(cs.det.hard_fail),
        candidate_output=cs.candidate_output, cost_usd=cs.candidate_cost_usd, cost_is_estimate=cs.cost_is_estimate,
        latency_ms=cs.candidate_latency_ms, in_comparison=str(cs.case_id) in common,
    ) for cs in scores]
    await persist_completed(session, run, results)
    return (await _project(session, [run], detail=True))[0]


def _is_uuid(value) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError):
        return False


# ---- projection (shared by create / history / detail) ---------------------------------------------------------

def _f(x) -> float | None:
    return float(x) if x is not None else None


async def _project(session: AsyncSession, runs: list[EvalRun], *, detail: bool = False) -> list:
    if not runs:
        return []
    suite_names = dict((await session.execute(
        select(EvalSuite.id, EvalSuite.name).where(EvalSuite.id.in_({r.suite_id for r in runs})))).all())
    base_ids = {r.baseline_run_id for r in runs if r.baseline_run_id is not None}
    base_versions = dict((await session.execute(
        select(EvalRun.id, EvalRun.version_id).where(EvalRun.id.in_(base_ids)))).all()) if base_ids else {}
    labels = dict((await session.execute(select(AgentVersion.id, AgentVersion.label).where(
        AgentVersion.id.in_({r.version_id for r in runs} | set(base_versions.values()))))).all())
    by_run: dict = {}
    if detail:
        for res in (await session.execute(select(EvalResult).where(EvalResult.eval_run_id.in_([r.id for r in runs]))
                                          .order_by(EvalResult.case_id))).scalars():
            by_run.setdefault(res.eval_run_id, []).append(res)
    out = []
    for r in runs:
        base = (RunBaselineOut(origin=r.baseline_origin, eval_run_id=str(r.baseline_run_id),
                               version_label=labels.get(base_versions.get(r.baseline_run_id)))
                if r.baseline_run_id is not None else None)
        fields = dict(
            id=str(r.id), agent_id=str(r.agent_id), suite_id=str(r.suite_id), suite_name=suite_names.get(r.suite_id),
            version_id=str(r.version_id), version_label=labels.get(r.version_id), status=r.status,
            verdict=r.verdict, reason_code=r.reason_code, reason=r.reason, quality=_f(r.quality),
            quality_delta=_f(r.quality_delta), cost_usd=_f(r.cost_usd), cost_delta_pct=_f(r.cost_delta_pct),
            latency_ms=_f(r.latency_ms), latency_delta_ms=_f(r.latency_delta_ms), compared_cases=r.compared_cases,
            new_cases=r.new_cases, dropped_cases=r.dropped_cases, total_cases=r.total_cases, baseline=base,
            created_by=r.created_by, finished_at=r.finished_at,
        )
        if not detail:
            out.append(EvalRunOut(**fields))
            continue
        res = by_run.get(r.id, [])
        out.append(EvalRunDetailOut(
            **fields,
            dimensions=[DimensionOut(**d) for d in (r.dimensions or [])],
            failing_cases=[FailingCaseOut(case_id=str(x.case_id), reason=x.failure_reason, hard=x.hard_failure)
                           for x in res if x.failure_reason],
            results=[EvalResultOut(case_id=str(x.case_id), passed_deterministic=x.passed_deterministic,
                                   quality=_f(x.quality), in_comparison=x.in_comparison,
                                   failure_reason=x.failure_reason, cost_usd=_f(x.cost_usd),
                                   latency_ms=x.latency_ms) for x in res],
            policy=r.policy or {}, judge_version=r.judge_version, pairwise_rate=_f(r.pairwise_rate),
        ))
    return out


# ---- history (T056): keyset on (finished_at DESC, id DESC) ----------------------------------------------------

def _keyset(stmt, cursor: str | None):
    cur = decode_cursor(cursor)
    if cur is None:
        return stmt
    sort_value, last_id = cur
    try:
        key = (datetime.datetime.fromisoformat(sort_value), uuid.UUID(last_id))
    except (ValueError, TypeError) as exc:
        raise InputInvalid("invalid cursor", detail={"cursor": cursor}) from exc
    return stmt.where(tuple_(EvalRun.finished_at, EvalRun.id) < tuple_(*key))


def _page(rows: list, n: int) -> tuple[list, str | None]:
    if len(rows) <= n:
        return rows, None
    last = rows[n - 1]
    return rows[:n], encode_cursor(last.finished_at.isoformat(), str(last.id))


_NEWEST = (EvalRun.finished_at.desc(), EvalRun.id.desc())


@router.get("/{agent_id}/eval-runs", dependencies=[Depends(require_permission("view"))])
async def list_agent_eval_runs(agent_id: str, session: AsyncSession = Depends(tenant_db),
                               ap: PolicyPrincipal = Depends(access_principal),
                               limit: int | None = None, cursor: str | None = None) -> Page[EvalRunOut]:
    aid = _uuid_or_404(agent_id, "agent", "agent_id")
    policy = (await session.execute(select(Agent.access_policy).where(Agent.id == aid))).scalar_one_or_none()
    nf = NotFound("agent not found", detail={"agent_id": agent_id})
    if policy is None:
        raise nf
    ensure_use(policy, ap, not_found=nf)
    n = clamp_limit(limit)
    stmt = _keyset(select(EvalRun).where(EvalRun.agent_id == aid), cursor).order_by(*_NEWEST).limit(n + 1)
    rows, nxt = _page(list((await session.execute(stmt)).scalars()), n)
    return Page[EvalRunOut](items=await _project(session, rows), next_cursor=nxt)


runs_router = APIRouter(prefix="/api/v1/eval-runs", tags=["evals"])


@runs_router.get("", dependencies=[Depends(require_permission("view"))])
async def list_eval_runs(session: AsyncSession = Depends(tenant_db), ap: PolicyPrincipal = Depends(access_principal),
                         limit: int | None = None, cursor: str | None = None) -> Page[EvalRunOut]:
    """Workspace-wide (RLS), post-filtered to agents the caller can USE — a short page is acceptable (G4, as
    `list_agents`); the keyset still advances on (finished_at, id), so nothing hidden is ever listed."""
    n = clamp_limit(limit)
    stmt = _keyset(select(EvalRun, Agent.access_policy).join(Agent, Agent.id == EvalRun.agent_id), cursor)
    pairs = (await session.execute(stmt.order_by(*_NEWEST).limit(n + 1))).all()
    rows, nxt = _page([r for r, _ in pairs], n)
    visible = {r.id for r, pol in pairs if can_use(pol, ap)}
    return Page[EvalRunOut](items=await _project(session, [r for r in rows if r.id in visible]), next_cursor=nxt)


@runs_router.get("/{run_id}", dependencies=[Depends(require_permission("view"))])
async def get_eval_run(run_id: str, session: AsyncSession = Depends(tenant_db),
                       ap: PolicyPrincipal = Depends(access_principal)) -> EvalRunDetailOut:
    rid = _uuid_or_404(run_id, "eval run", "eval_run_id")
    nf = NotFound("eval run not found", detail={"eval_run_id": run_id})
    pair = (await session.execute(select(EvalRun, Agent.access_policy).join(Agent, Agent.id == EvalRun.agent_id)
                                  .where(EvalRun.id == rid))).first()
    if pair is None:
        raise nf
    ensure_use(pair[1], ap, not_found=nf)
    return (await _project(session, [pair[0]], detail=True))[0]


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
