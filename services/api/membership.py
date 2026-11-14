"""Membership-store authorization read (feature 012).

Resolves the caller's Agent-Catalyst role from the existing `members` table (feature 006). Called with a
tenant-scoped session (RLS GUC = the target workspace), so the query only ever sees that workspace's roster —
no cross-tenant scan, no RLS bypass (research R3). This is authZ identity resolution; `require_permission`
(feature 008) still enforces the role. Read-only; no schema change.
"""
from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import Member


async def workspaces_for(session: AsyncSession, subject: str):
    """Every workspace `subject` holds a member row in, with their `console_role` — resolved ACROSS tenants for
    the pre-selection workspace switcher (GET /api/v1/workspaces). `members` is RLS-scoped per workspace, so this
    goes through the `member_workspaces()` SECURITY DEFINER function (migration 0011) on the UNSCOPED
    `auth_lookup_session`: the function is the single narrow definer (same pattern as `api_token_resolve`), and it
    only ever returns rows for the one `subject` passed in — ac_app never gains a broad RLS bypass. Returns rows
    with `.id`, `.name`, `.group_prefix`, `.console_role`, ordered by name."""
    # TODO(backend/perf): `member_workspaces()` filters on `members.subject` alone, but the only covering index is the
    #   UNIQUE (workspace_id, subject) — subject is its 2nd column, so this is a full scan of members across ALL
    #   tenants on every console load. Add `CREATE INDEX ix_members_subject ON members (subject)` (new migration)
    #   before members grows past toy size. (Discovery runs once per console boot, so it's cheap today.)
    return (
        await session.execute(
            text("SELECT id, name, group_prefix, console_role FROM member_workspaces(:subject)"),
            {"subject": subject},
        )
    ).all()


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
