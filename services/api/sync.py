"""IdP group → members-store sync + notification projection (feature 020).

The **only** writer that reconciles a user's AC role/team from their IdP group membership. This is a write path
into the `members` store (the authZ authority under RLS) at login — **not** a live per-request group→role
resolver (FR-021): `require_permission`/access-policy keep reading `members` via `membership.role_for`/
`teams_for` unchanged. A best-effort `group_members` projection feeds notification fan-out and is NEVER read to
grant access (FR-023).

Semantics (owner-ruled):
- `groups is None` → the IdP can't enumerate (claim-less pilot, D3): **no-op, never revoke**.
- `groups` authoritative → resolve through `workspaces.idp_binding` (name/GUID/attr) and **reconcile** the member
  row to match — including **clearing** a previously-synced role/team when the mapping shrinks/empties (D4
  revoke); the row is kept so `role_for` returns the fail-closed default. A first-time no-match writes nothing
  (FR-022).

Both write functions run under a tenant-scoped session (RLS, P5). This module logs no secrets (FR-011).
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.sql import func

from api.models import GroupMember, Member, Workspace

log = logging.getLogger("api.sync")

# AC console roles the IdP may grant, ASCENDING. `admin` is a platform-level claim, never IdP-group-synced, so it
# is deliberately absent — a sync can never mint or clear an admin (FR-022 fail-closed by omission).
_ROLE_RANK = ("viewer", "builder", "publisher")
_DEFAULT_ROLE = "viewer"


def _as_uuid(x) -> uuid.UUID:
    return x if isinstance(x, uuid.UUID) else uuid.UUID(str(x))


def _resolve_binding(binding: dict | None, groups) -> tuple[str | None, list[str]]:
    """Resolve an authoritative `groups` list through a workspace `idp_binding` → (role | None, sorted teams).
    The highest-ranked role whose native ref the user holds wins; teams are every mapped team the user holds.
    No naming convention is assumed — refs are compared verbatim (name / GUID / attr, FR-021)."""
    gset = {g for g in (groups or []) if g}
    roles_map = (binding or {}).get("roles") or {}
    teams_map = (binding or {}).get("teams") or {}
    role: str | None = None
    for r in _ROLE_RANK:
        ref = roles_map.get(r)
        if ref and ref in gset:
            role = r                                   # keep climbing → highest held role wins
    teams = sorted(team for team, ref in teams_map.items() if ref and ref in gset)
    return role, teams


def _relevant_refs(binding: dict | None, groups) -> list[str]:
    """The native refs this workspace's binding cares about that the user actually holds — the scope of the
    `group_members` projection (data-model: group_ref MATCHES idp_binding values), sorted & deduped."""
    gset = {g for g in (groups or []) if g}
    roles_map = (binding or {}).get("roles") or {}
    teams_map = (binding or {}).get("teams") or {}
    refs = {r for r in roles_map.values() if r} | {r for r in teams_map.values() if r}
    return sorted(refs & gset)


async def _binding_for(session, workspace_id) -> dict:
    """Read a workspace's `idp_binding` (workspaces = tenant root, readable in a tenant session)."""
    return await session.scalar(select(Workspace.idp_binding).where(Workspace.id == _as_uuid(workspace_id))) or {}


async def sync_member_from_groups(session, workspace_id, subject, groups) -> None:
    """Reconcile `members.console_role`+`teams` for `subject` from an AUTHORITATIVE `groups` list, resolved
    through this workspace's `idp_binding`. `groups is None` → no-op, never revoke (D3). Runs under the caller's
    tenant-scoped session (RLS). First-time no-match → writes nothing (FR-022); an authoritative shrink/empty on
    an existing row clears the synced role/team to the fail-closed default, keeping the row (D4 revoke)."""
    if groups is None:
        return                                          # D3: can't enumerate → never touch the members store
    binding = await _binding_for(session, workspace_id)
    role, teams = _resolve_binding(binding, groups)
    # RLS scopes `members` to this workspace, so matching on subject alone is tenant-safe (mirrors membership.py).
    member = await session.scalar(select(Member).where(Member.subject == subject))
    if member is None:
        if role is None and not teams:
            return                                      # FR-022: first-time no-match grants nothing
        session.add(Member(workspace_id=_as_uuid(workspace_id), subject=subject,
                           console_role=role or _DEFAULT_ROLE, teams=teams))
    else:
        member.console_role = role or _DEFAULT_ROLE     # D4: revoke → clear to default when no role maps
        member.teams = teams                            # D4: reconcile (shrink clears dropped teams)
    await session.flush()


async def upsert_group_projection(session, workspace_id, subject, refs, source) -> None:
    """Upsert the cache-only `group_members` rows for `subject` (one per native ref). `source` ∈
    {`login_seen`, `members_of`}. NEVER read to grant access (FR-023). No-op on an empty ref list."""
    if not refs:
        return
    stmt = pg_insert(GroupMember).values([
        {"workspace_id": _as_uuid(workspace_id), "group_ref": ref, "subject": subject, "source": source}
        for ref in refs
    ])
    stmt = stmt.on_conflict_do_update(
        index_elements=["workspace_id", "group_ref", "subject"],
        set_={"source": stmt.excluded.source, "seen_at": func.now()},   # refresh provenance + freshness
    )
    await session.execute(stmt)


async def refresh_group_projection(session, workspace_id, provider=None) -> None:
    """Scheduled projection refresh (callable; no scheduler infra yet — D5). For each native ref in this
    workspace's `idp_binding`, enumerate `members_of(ref)` on the (capable) provider and upsert `group_members`
    with `source='members_of'`. `members_of` absent or returning None → skip (unsupported). Never authZ (FR-023)."""
    members_of = getattr(provider, "members_of", None) if provider is not None else None
    if members_of is None:
        return
    binding = await _binding_for(session, workspace_id)
    roles_map = binding.get("roles") or {}
    teams_map = binding.get("teams") or {}
    refs = sorted({r for r in roles_map.values() if r} | {r for r in teams_map.values() if r})
    for ref in refs:
        subjects = await members_of(ref)
        if subjects is None:
            continue                                    # this group isn't enumerable → leave the projection as-is
        for subj in subjects:
            await upsert_group_projection(session, workspace_id, subj, [ref], source="members_of")
    await session.flush()


async def _groups_for(provider, subject):
    """Capability-detected `groups_for` (D2): absent → None (claim-less IdP), else the provider's result."""
    fn = getattr(provider, "groups_for", None)
    if fn is None:
        return None
    return await fn(subject)


async def run_login_sync(subject, provider) -> None:
    """Login-hook orchestration (019's `/auth/callback`, after `issue_session`). Resolve the subject's native
    groups on the active provider; for every workspace with a configured `idp_binding`, reconcile the member row
    and upsert the login projection. A claim-less provider (`groups_for` absent/None) is a whole no-op that never
    revokes (D3). **Best-effort**: any failure is logged (no secrets) and swallowed — a sync hiccup must not break
    login, since the authZ reads fail closed anyway."""
    # Local imports keep module import infra-free (no engine/settings touched until a login actually runs).
    from api.db import auth_lookup_session, tenant_session
    try:
        groups = await _groups_for(provider, subject)
        if groups is None:
            return                                      # D3: nothing to sync, never revoke
        # Enumerate workspaces that actually configured a binding (workspaces = tenant root, no RLS → one read).
        async with auth_lookup_session() as s:
            bound = [(wid, b) for wid, b in (await s.execute(
                select(Workspace.id, Workspace.idp_binding))).all() if b]
        for ws_id, binding in bound:
            async with tenant_session(ws_id) as s:
                await sync_member_from_groups(s, ws_id, subject, groups)
                await upsert_group_projection(s, ws_id, subject, _relevant_refs(binding, groups),
                                              source="login_seen")
    except Exception:                                   # noqa: BLE001 — best-effort; login must not fail on sync
        log.warning("login group-sync failed for a subject (best-effort; login proceeds)", exc_info=True)
