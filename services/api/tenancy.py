"""Workspace-scope / RLS session var helper (09 §7).

Sets the per-transaction `app.workspace_id` GUC so the RLS policies (migrations 0002/0004) filter
every tenant-owned table. `is_local=true` scopes it to the current transaction, so a pooled
connection (PgBouncer txn-mode) never leaks one request's tenant into the next. Framework-agnostic;
FastAPI deps wrap it later (deps.py, M4).
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _normalize_ws(workspace_id: str | UUID | None) -> str:
    """Canonical UUID string, or '' for a missing/malformed value.

    Fail-closed (FR-004): an empty string makes the RLS predicate NULLIF(...,'')::uuid → NULL →
    zero rows. Without this, a non-empty but malformed scope (e.g. None → 'None', or any non-uuid)
    would reach `::uuid` and raise 'invalid input syntax for type uuid' (a 500) instead of returning
    zero rows. Valid scopes pass through unchanged.
    """
    if workspace_id is None:
        return ""
    try:
        return str(UUID(str(workspace_id)))
    except (ValueError, AttributeError, TypeError):
        return ""


async def set_workspace_scope(session: AsyncSession, workspace_id: str | UUID | None) -> None:
    """Pin the tenant for the current transaction. Must run inside an open transaction."""
    await session.execute(
        text("select set_config('app.workspace_id', :ws, true)"),
        {"ws": _normalize_ws(workspace_id)},
    )
