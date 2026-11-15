"""Durable monthly spend accounting (feature 009, M6 / constitution finding F7).

Pure data-access helpers shared by the enforcement gate (`api.deps.enforce_spend_cap`) and the
observability view (`GET /api/v1/workspace/spend`) — one source of truth so the two never disagree
(SC-004). Month-to-date spend is summed on demand from the durable `runs.cost_usd` ledger; there is no
evictable counter, so cache loss cannot raise the ceiling (F7). No HTTP imports here — the P6/P3 control
decision lives one layer up in `deps.py`.
"""
from __future__ import annotations

import datetime
import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import IngestionJob, Run, WorkspaceSettings

# Mirrors frontend/src/lib/data.ts `defaultLimits().monthlySpendUsd`. An unset cap is a finite default,
# never "unlimited" (fail-closed spirit).
DEFAULT_MONTHLY_CAP_USD = Decimal("2000")


def _month_start_utc(now: datetime.datetime | None = None) -> datetime.datetime:
    """First day of the current calendar month at 00:00, **naive** (UTC). Naive to compare apples-to-apples
    with the `timestamp` (no tz) `runs.created_at` column. `now` is injectable for deterministic tests."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return datetime.datetime(now.year, now.month, 1)  # naive, at UTC month boundary


async def month_to_date_breakdown(session: AsyncSession, workspace_id: str) -> tuple[Decimal, Decimal]:
    """(runs, ingestion) recorded cost for the workspace since the start of the current calendar month (UTC).

    Feature 024: ingestion embedding cost (`ingestion_jobs.cost_usd`) is real spend and counts toward the same cap.
    Double-scoped (RLS GUC + explicit workspace predicate). Raises on a ledger/DB failure — never swallowed (FR-009).
    `runs.created_at` is naive UTC; `ingestion_jobs.created_at` is timestamptz — each compared in its own form.
    """
    ws = uuid.UUID(workspace_id)
    start = _month_start_utc()
    runs = (await session.execute(select(func.coalesce(func.sum(Run.cost_usd), 0)).where(
        Run.workspace_id == ws, Run.created_at >= start))).scalar_one()
    ingestion = (await session.execute(select(func.coalesce(func.sum(IngestionJob.cost_usd), 0)).where(
        IngestionJob.workspace_id == ws,
        IngestionJob.created_at >= start.replace(tzinfo=datetime.timezone.utc)))).scalar_one()
    return Decimal(str(runs)), Decimal(str(ingestion))


async def month_to_date_spend(session: AsyncSession, workspace_id: str) -> Decimal:
    """Month-to-date spend = runs + ingestion (feature 009 + 024). Fail-closed: raises, callers must not swallow."""
    runs, ingestion = await month_to_date_breakdown(session, workspace_id)
    return runs + ingestion


async def active_cap_usd(session: AsyncSession, workspace_id: str) -> Decimal:
    """The workspace's monthly spend cap from `workspace_settings.limits.monthlySpendUsd`; the documented
    default when the row or key is absent."""
    ws = (await session.execute(
        select(WorkspaceSettings).where(WorkspaceSettings.workspace_id == uuid.UUID(workspace_id))
    )).scalar_one_or_none()
    if ws is not None and isinstance(ws.limits, dict) and ws.limits.get("monthlySpendUsd") is not None:
        return Decimal(str(ws.limits["monthlySpendUsd"]))
    return DEFAULT_MONTHLY_CAP_USD
