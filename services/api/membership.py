"""Membership-store authorization read (feature 012).

Resolves the caller's Agent-Catalyst role from the existing `members` table (feature 006). Called with a
tenant-scoped session (RLS GUC = the target workspace), so the query only ever sees that workspace's roster —
no cross-tenant scan, no RLS bypass (research R3). This is authZ identity resolution; `require_permission`
(feature 008) still enforces the role. Read-only; no schema change.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import Member


async def role_for(session: AsyncSession, subject: str) -> str | None:
    """The caller's `console_role` in the current tenant session's workspace, or None if not a member."""
    return (
        await session.execute(select(Member.console_role).where(Member.subject == subject))
    ).scalar_one_or_none()


async def teams_for(session: AsyncSession, subject: str) -> list[str]:
    """The caller's authZ `teams` in the current tenant session's workspace (feature 013). Returns `[]` when the
    caller has no member row — fail-closed and non-raising, so dev principals / callers without a seeded member
    keep working on default-`workspace` resources and are simply denied every `restricted`/`private` team gate."""
    row = (
        await session.execute(select(Member.teams).where(Member.subject == subject))
    ).scalar_one_or_none()
    return list(row or [])
