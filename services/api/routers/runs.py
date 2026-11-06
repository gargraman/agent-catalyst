"""Run history + dashboard metrics router (feature 006, US5).

Read-only over the workspace's `runs` (RLS-scoped). History is newest-first + keyset-paginated (FR-014).
Dashboard metrics aggregate a rolling 30-day window in daily buckets (FR-015), computed from real run records
only — zero-filled on an empty window, never fabricated (P8).
"""
from __future__ import annotations

import datetime
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import require_permission, resolve_workspace, tenant_db
from api.errors import NotFound
from api.models import Run
from api.pagination import Page, clamp_limit, decode_cursor, encode_cursor
from api.schemas.runs import DashboardOut, EvalStatsOut, RunOut, RunsStatsOut

router = APIRouter(prefix="/api/v1", tags=["runs"])

_WINDOW_DAYS = 30
_CALLER_SOURCES = ("playground", "published")   # eval runs excluded from call-volume (FR-015)


def _run_out(r: Run) -> RunOut:
    return RunOut(id=str(r.id), agent_id=str(r.agent_id) if r.agent_id else None,
                  version_id=str(r.version_id) if r.version_id else None, model=r.model,
                  usage=r.usage, latency_ms=r.latency_ms, cost_usd=float(r.cost_usd), outcome=r.outcome,
                  source=r.source, correlation_id=r.correlation_id, created_at=r.created_at)


@router.get("/runs", dependencies=[Depends(require_permission("view"))])
async def list_runs(ws: str = Depends(resolve_workspace), session: AsyncSession = Depends(tenant_db),
                    limit: int | None = None, cursor: str | None = None) -> Page[RunOut]:
    n = clamp_limit(limit)
    stmt = select(Run).order_by(Run.created_at.desc(), Run.id.desc())
    cur = decode_cursor(cursor)
    if cur is not None:
        sort_value, last_id = cur
        stmt = stmt.where(tuple_(Run.created_at, Run.id)
                          < tuple_(datetime.datetime.fromisoformat(sort_value), uuid.UUID(last_id)))
    rows = list((await session.execute(stmt.limit(n + 1))).scalars().all())
    next_cursor = None
    if len(rows) > n:
        last = rows[n - 1]
        next_cursor = encode_cursor(last.created_at.isoformat(), str(last.id))
        rows = rows[:n]
    return Page[RunOut](items=[_run_out(r) for r in rows], next_cursor=next_cursor)


@router.get("/runs/{run_id}", dependencies=[Depends(require_permission("view"))])
async def get_run(run_id: str, ws: str = Depends(resolve_workspace),
                  session: AsyncSession = Depends(tenant_db)) -> RunOut:
    try:
        rid = uuid.UUID(run_id)
    except (ValueError, TypeError):
        raise NotFound("run not found", detail={"run_id": run_id})
    r = (await session.execute(select(Run).where(Run.id == rid))).scalar_one_or_none()
    if r is None:
        raise NotFound("run not found", detail={"run_id": run_id})
    return _run_out(r)


def _utcnow_naive() -> datetime.datetime:
    # created_at is stored in a timestamptz column but mapped naive (like every created_at here, which the
    # keyset cursors rely on); compare against a naive-UTC value so asyncpg doesn't reject a tz-aware bind.
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


async def _window_runs(session: AsyncSession) -> list[Run]:
    # Pilot-scale aggregation: load the 30-day window and roll up in Python. Bounded for one pilot tenant; at
    # higher volume this should become a SQL GROUP BY (date_trunc('day'), source, outcome, sum(cost)) — deferred
    # to the M6 observability feature which owns real metrics infra. (code-review F5, accepted deferral.)
    since = _utcnow_naive() - datetime.timedelta(days=_WINDOW_DAYS)
    return list((await session.execute(select(Run).where(Run.created_at >= since))).scalars().all())


@router.get("/metrics/dashboard", dependencies=[Depends(require_permission("view"))])
async def dashboard(ws: str = Depends(resolve_workspace),
                    session: AsyncSession = Depends(tenant_db)) -> DashboardOut:
    today = _utcnow_naive().date()
    buckets = [(today - datetime.timedelta(days=i)) for i in range(_WINDOW_DAYS - 1, -1, -1)]
    idx = {d: i for i, d in enumerate(buckets)}
    spend = [0.0] * _WINDOW_DAYS
    calls = [0] * _WINDOW_DAYS
    total_cost, total_calls = 0.0, 0
    for r in await _window_runs(session):
        d = r.created_at.date()
        if d in idx:
            spend[idx[d]] += float(r.cost_usd)
            total_cost += float(r.cost_usd)
            if r.source in _CALLER_SOURCES:
                calls[idx[d]] += 1
                total_calls += 1
    return DashboardOut(window_days=_WINDOW_DAYS, buckets=[d.isoformat() for d in buckets],
                        spend=spend, calls=calls, total_cost_usd=round(total_cost, 6), total_calls=total_calls)


@router.get("/metrics/runs", dependencies=[Depends(require_permission("view"))])
async def runs_stats(ws: str = Depends(resolve_workspace),
                     session: AsyncSession = Depends(tenant_db)) -> RunsStatsOut:
    rows = await _window_runs(session)
    by_outcome: dict = {}
    by_source: dict = {}
    for r in rows:
        by_outcome[r.outcome] = by_outcome.get(r.outcome, 0) + 1
        by_source[r.source] = by_source.get(r.source, 0) + 1
    return RunsStatsOut(total=len(rows), by_outcome=by_outcome, by_source=by_source)


@router.get("/metrics/evals", dependencies=[Depends(require_permission("view"))])
async def eval_stats(ws: str = Depends(resolve_workspace),
                     session: AsyncSession = Depends(tenant_db)) -> EvalStatsOut:
    rows = [r for r in await _window_runs(session) if r.source == "eval"]
    by_outcome: dict = {}
    for r in rows:
        by_outcome[r.outcome] = by_outcome.get(r.outcome, 0) + 1
    return EvalStatsOut(total_eval_runs=len(rows), by_outcome=by_outcome)
