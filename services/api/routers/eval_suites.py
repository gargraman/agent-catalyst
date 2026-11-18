"""Eval suites, cases and the frozen baseline — server-held (feature 026 US4; contracts/api.md §C/§D).

Suites and cases live on the server so an eval run can never be fed cases, a baseline or a policy by the client
(FR-015 / owner O3). `content_hash` is computed here (D8 identity). The `baseline` shown on a suite is resolved by
`api.baseline.resolve_baseline` — the same rule the next run compares against.

Gates follow the existing deps order: `require_permission` (header-only, 403 before any lookup) → agent lookup →
`ensure_use` (reads) / `ensure_manage` (writes). A suite of another workspace is invisible under RLS → 404.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Response
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api import audit
from api.access_policy import PolicyPrincipal
from api.baseline import case_content_hash
from api.db_errors import integrity_mapped
from api.deps import (Principal, access_principal, ensure_manage, ensure_use, rate_limit, require_permission,
                      resolve_workspace, session_principal, tenant_db)
from api.errors import EvalSuiteHasRuns, InputInvalid, NotFound
from api.eval_store import EVAL_CASE_CAP, SuiteBaseline, suite_baselines
from api.models import Agent, EvalBaseline, EvalCase, EvalRun, EvalSuite
from api.schemas.evals import (BaselineFreezeIn, BaselineOut, EvalCaseIn, EvalCaseOut, EvalSuiteDetailOut, EvalSuiteIn,
                               EvalSuiteOut)

agent_router = APIRouter(prefix="/api/v1/agents", tags=["eval-suites"])
router = APIRouter(prefix="/api/v1/eval-suites", tags=["eval-suites"])

_AUTHOR = [Depends(require_permission("eval.author")), Depends(rate_limit("default"))]
_GOVERN = [Depends(require_permission("eval.govern")), Depends(rate_limit("default"))]
_VIEW = [Depends(require_permission("view"))]


# ---- loaders + gates ----------------------------------------------------------------------------------------------

def _uuid(value: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError):
        raise NotFound(f"{what} not found", detail={f"{what.replace(' ', '_')}_id": value}) from None


async def _agent(session: AsyncSession, agent_id: str | uuid.UUID, ap: PolicyPrincipal, *, manage: bool) -> Agent:
    nf = NotFound("agent not found", detail={"agent_id": str(agent_id)})
    agent = await session.get(Agent, _uuid(str(agent_id), "agent"))
    if agent is None:
        raise nf
    (ensure_manage if manage else ensure_use)(agent.access_policy, ap, not_found=nf)
    return agent


async def _suite(session: AsyncSession, suite_id: str, ap: PolicyPrincipal, *, manage: bool,
                 lock: bool = False) -> tuple[EvalSuite, Agent]:
    q = select(EvalSuite).where(EvalSuite.id == _uuid(suite_id, "eval suite"))
    suite = (await session.execute(q.with_for_update() if lock else q)).scalar_one_or_none()
    if suite is None:
        raise NotFound("eval suite not found", detail={"suite_id": suite_id})
    try:
        agent = await _agent(session, suite.agent_id, ap, manage=manage)
    except NotFound:
        raise NotFound("eval suite not found", detail={"suite_id": suite_id}) from None   # same body as absent
    return suite, agent


async def _case(session: AsyncSession, suite: EvalSuite, case_id: str) -> EvalCase:
    case = await session.get(EvalCase, _uuid(case_id, "eval case"))
    if case is None or case.suite_id != suite.id:
        raise NotFound("eval case not found", detail={"case_id": case_id})
    return case


async def _case_counts(session: AsyncSession, suite_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not suite_ids:
        return {}
    rows = await session.execute(select(EvalCase.suite_id, func.count()).where(EvalCase.suite_id.in_(suite_ids))
                                 .group_by(EvalCase.suite_id))
    return {sid: n for sid, n in rows.all()}


# ---- projections --------------------------------------------------------------------------------------------------

def _baseline_out(sb: SuiteBaseline | None) -> BaselineOut:
    if sb is None or sb.resolved.origin == "none":
        return BaselineOut(origin="none")
    run = sb.resolved.run
    return BaselineOut(origin=sb.resolved.origin, eval_run_id=str(run.id), version_id=str(run.version_id),
                       frozen_by=sb.frozen.frozen_by if sb.frozen else None,
                       frozen_at=sb.frozen.frozen_at if sb.frozen else None)


def _case_out(c: EvalCase) -> EvalCaseOut:
    return EvalCaseOut(id=str(c.id), suite_id=str(c.suite_id), input=c.input, rubric=c.rubric,
                       required_fields=list(c.required_fields or []), expected=c.expected,
                       content_hash=c.content_hash, updated_at=c.updated_at)


async def _suite_out(session: AsyncSession, suite: EvalSuite, agent: Agent) -> EvalSuiteOut:
    counts = await _case_counts(session, [suite.id])
    bl = await suite_baselines(session, [suite.id], agent.active_version_id)
    return EvalSuiteOut(id=str(suite.id), agent_id=str(suite.agent_id), name=suite.name,
                        case_count=counts.get(suite.id, 0), updated_at=suite.updated_at,
                        baseline=_baseline_out(bl.get(suite.id)))


async def _touch(session: AsyncSession, suite: EvalSuite) -> None:
    suite.updated_at = func.now()
    await session.flush()
    await session.refresh(suite, ["updated_at"])


# ---- suites -------------------------------------------------------------------------------------------------------

@agent_router.get("/{agent_id}/eval-suites", dependencies=_VIEW)
async def list_suites(agent_id: str, session: AsyncSession = Depends(tenant_db),
                      ap: PolicyPrincipal = Depends(access_principal)) -> list[EvalSuiteOut]:
    agent = await _agent(session, agent_id, ap, manage=False)
    suites = list((await session.execute(select(EvalSuite).where(EvalSuite.agent_id == agent.id)
                                         .order_by(EvalSuite.created_at, EvalSuite.id))).scalars())
    ids = [s.id for s in suites]
    counts, bls = await _case_counts(session, ids), await suite_baselines(session, ids, agent.active_version_id)
    return [EvalSuiteOut(id=str(s.id), agent_id=str(s.agent_id), name=s.name, case_count=counts.get(s.id, 0),
                         updated_at=s.updated_at, baseline=_baseline_out(bls.get(s.id))) for s in suites]


@agent_router.post("/{agent_id}/eval-suites", status_code=201, dependencies=_AUTHOR)
async def create_suite(agent_id: str, body: EvalSuiteIn, ws: str = Depends(resolve_workspace),
                       session: AsyncSession = Depends(tenant_db),
                       ap: PolicyPrincipal = Depends(access_principal)) -> EvalSuiteOut:
    agent = await _agent(session, agent_id, ap, manage=True)
    suite = EvalSuite(workspace_id=uuid.UUID(ws), agent_id=agent.id, name=body.name)
    session.add(suite)
    async with integrity_mapped():                       # only uq_eval_suites_agent_name → 409; others stay honest 500s
        await session.flush()
    await session.refresh(suite)
    return await _suite_out(session, suite, agent)


@router.get("/{suite_id}", dependencies=_VIEW)
async def get_suite(suite_id: str, session: AsyncSession = Depends(tenant_db),
                    ap: PolicyPrincipal = Depends(access_principal)) -> EvalSuiteDetailOut:
    suite, agent = await _suite(session, suite_id, ap, manage=False)
    cases = list((await session.execute(select(EvalCase).where(EvalCase.suite_id == suite.id)
                                        .order_by(EvalCase.created_at, EvalCase.id))).scalars())
    out = await _suite_out(session, suite, agent)
    return EvalSuiteDetailOut(**out.model_dump(), cases=[_case_out(c) for c in cases])


@router.patch("/{suite_id}", dependencies=_AUTHOR)
async def rename_suite(suite_id: str, body: EvalSuiteIn, session: AsyncSession = Depends(tenant_db),
                       ap: PolicyPrincipal = Depends(access_principal)) -> EvalSuiteOut:
    suite, agent = await _suite(session, suite_id, ap, manage=True)
    suite.name = body.name
    async with integrity_mapped():
        await _touch(session, suite)
    return await _suite_out(session, suite, agent)


@router.delete("/{suite_id}", status_code=204, dependencies=_AUTHOR)
async def delete_suite(suite_id: str, session: AsyncSession = Depends(tenant_db),
                       ap: PolicyPrincipal = Depends(access_principal)) -> Response:
    suite, _ = await _suite(session, suite_id, ap, manage=True, lock=True)
    if await session.scalar(select(exists().where(EvalRun.suite_id == suite.id))):
        raise EvalSuiteHasRuns("this suite has eval runs (evidence) and cannot be deleted",
                               detail={"suite_id": suite_id})
    await session.delete(suite)                          # cases cascade (fk_eval_cases_suite ON DELETE CASCADE)
    await session.flush()
    return Response(status_code=204)


# ---- cases --------------------------------------------------------------------------------------------------------

def _apply_case(case: EvalCase, body: EvalCaseIn) -> None:
    rubric = [d.model_dump() for d in body.rubric]
    case.input, case.rubric = body.input, rubric
    case.required_fields, case.expected = list(body.required_fields), body.expected
    case.content_hash = case_content_hash(body.input, rubric, body.required_fields, body.expected)


@router.post("/{suite_id}/cases", status_code=201, dependencies=_AUTHOR)
async def create_case(suite_id: str, body: EvalCaseIn, ws: str = Depends(resolve_workspace),
                      session: AsyncSession = Depends(tenant_db),
                      ap: PolicyPrincipal = Depends(access_principal)) -> EvalCaseOut:
    suite, _ = await _suite(session, suite_id, ap, manage=True)
    n = await session.scalar(select(func.count()).select_from(EvalCase).where(EvalCase.suite_id == suite.id))
    if n >= EVAL_CASE_CAP:                               # review #6: never author a suite a run would refuse
        raise InputInvalid(f"an eval suite is capped at {EVAL_CASE_CAP} cases; split the suite",
                           detail={"suite_id": suite_id, "cases": n, "max": EVAL_CASE_CAP})
    case = EvalCase(workspace_id=uuid.UUID(ws), suite_id=suite.id)
    _apply_case(case, body)
    session.add(case)
    await _touch(session, suite)
    await session.refresh(case)
    return _case_out(case)


@router.put("/{suite_id}/cases/{case_id}", dependencies=_AUTHOR)
async def replace_case(suite_id: str, case_id: str, body: EvalCaseIn, session: AsyncSession = Depends(tenant_db),
                       ap: PolicyPrincipal = Depends(access_principal)) -> EvalCaseOut:
    suite, _ = await _suite(session, suite_id, ap, manage=True)
    case = await _case(session, suite, case_id)
    _apply_case(case, body)
    case.updated_at = func.now()
    await _touch(session, suite)
    await session.refresh(case)
    return _case_out(case)


@router.delete("/{suite_id}/cases/{case_id}", status_code=204, dependencies=_AUTHOR)
async def delete_case(suite_id: str, case_id: str, session: AsyncSession = Depends(tenant_db),
                      ap: PolicyPrincipal = Depends(access_principal)) -> Response:
    suite, _ = await _suite(session, suite_id, ap, manage=True)
    case = await _case(session, suite, case_id)
    await session.delete(case)                           # results keep case_id (not an FK): evidence outlives a case
    await _touch(session, suite)
    return Response(status_code=204)


# ---- frozen baseline (§D; admin-only, audited in the same transaction) -----------------------------------------

@router.put("/{suite_id}/baseline", dependencies=_GOVERN)
async def freeze_baseline(suite_id: str, body: BaselineFreezeIn, session: AsyncSession = Depends(tenant_db),
                          ap: PolicyPrincipal = Depends(access_principal),
                          principal: Principal = Depends(session_principal)) -> BaselineOut:
    suite, _ = await _suite(session, suite_id, ap, manage=True, lock=True)
    invalid = InputInvalid("the baseline must be a completed eval run of this suite",
                           detail={"eval_run_id": body.eval_run_id})
    try:
        rid = uuid.UUID(body.eval_run_id)
    except ValueError:
        raise invalid from None
    run = await session.get(EvalRun, rid)                # RLS: another workspace's run reads as absent
    if run is None or run.suite_id != suite.id or run.status != "completed":
        raise invalid
    pointer = await session.get(EvalBaseline, suite.id)
    previous = pointer.eval_run_id if pointer else None
    if pointer is None:
        pointer = EvalBaseline(workspace_id=suite.workspace_id, suite_id=suite.id, eval_run_id=rid,
                               frozen_by=principal.subject)
        session.add(pointer)
    else:
        pointer.eval_run_id, pointer.frozen_by, pointer.frozen_at = rid, principal.subject, func.now()
    async with integrity_mapped():                       # eval_baselines_run_check trigger backs up the check above
        await session.flush()
    await session.refresh(pointer)
    await audit.record(session, principal, "baseline.freeze", "eval_suite", suite.id,
                       {"previous_run_id": previous, "new_run_id": rid})
    return BaselineOut(origin="frozen", eval_run_id=str(rid), version_id=str(run.version_id),
                       frozen_by=pointer.frozen_by, frozen_at=pointer.frozen_at)


@router.delete("/{suite_id}/baseline", status_code=204, dependencies=_GOVERN)
async def clear_baseline(suite_id: str, session: AsyncSession = Depends(tenant_db),
                         ap: PolicyPrincipal = Depends(access_principal),
                         principal: Principal = Depends(session_principal)) -> Response:
    suite, _ = await _suite(session, suite_id, ap, manage=True, lock=True)
    pointer = await session.get(EvalBaseline, suite.id)
    if pointer is not None:                              # idempotent: audit only when something was cleared
        previous = pointer.eval_run_id
        await session.delete(pointer)
        await session.flush()
        await audit.record(session, principal, "baseline.clear", "eval_suite", suite.id,
                           {"previous_run_id": previous})
    return Response(status_code=204)
