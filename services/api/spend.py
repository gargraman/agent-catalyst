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

from api.models import Run, WorkspaceSettings

# Mirrors frontend/src/lib/data.ts `defaultLimits().monthlySpendUsd`. An unset cap is a finite default,
# never "unlimited" (fail-closed spirit).
DEFAULT_MONTHLY_CAP_USD = Decimal("2000")


def _month_start_utc(now: datetime.datetime | None = None) -> datetime.datetime:
    """First day of the current calendar month at 00:00, **naive** (UTC). Naive to compare apples-to-apples
    with the `timestamp` (no tz) `runs.created_at` column. `now` is injectable for deterministic tests."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return datetime.datetime(now.year, now.month, 1)  # naive, at UTC month boundary


async def month_to_date_spend(session: AsyncSession, workspace_id: str) -> Decimal:
    """Sum of recorded run cost for the workspace since the start of the current calendar month (UTC).

    Double-scoped: the RLS GUC on `session` plus an explicit `workspace_id` predicate. Raises on a ledger/DB
    failure — callers must NOT swallow it (fail-closed per FR-009): a refused computation must not become a
    paid call.
    """
    stmt = select(func.coalesce(func.sum(Run.cost_usd), 0)).where(
        Run.workspace_id == uuid.UUID(workspace_id),
        Run.created_at >= _month_start_utc(),
    )
    total = (await session.execute(stmt)).scalar_one()
    return Decimal(str(total))


async def active_cap_usd(session: AsyncSession, workspace_id: str) -> Decimal:
    """The workspace's monthly spend cap from `workspace_settings.limits.monthlySpendUsd`; the documented
    default when the row or key is absent."""
    ws = (await session.execute(
        select(WorkspaceSettings).where(WorkspaceSettings.workspace_id == uuid.UUID(workspace_id))
    )).scalar_one_or_none()
    if ws is not None and isinstance(ws.limits, dict) and ws.limits.get("monthlySpendUsd") is not None:
        return Decimal(str(ws.limits["monthlySpendUsd"]))
    return DEFAULT_MONTHLY_CAP_USD
