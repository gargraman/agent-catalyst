"""Feature 020 — IdP group → members-store sync + notification projection (TDD, P9).

The sync is the ONLY writer that reconciles a user's AC role/team from IdP group membership, at login, into the
`members` store (the authZ authority under RLS). There is NO live per-request group→role resolver (FR-021): the
access stack keeps reading `members` via `membership.role_for`/`teams_for`. A best-effort `group_members`
projection feeds notification fan-out and is NEVER an authZ source (FR-023).

Integration: needs the compose Postgres (RLS applies via `tenant_session`). A fake enumerating adapter stands in
for an IdP that exposes groups (Okta/Entra/generic-OIDC); the claim-less pilot (`trellix-iam`) has no enumeration
capability at all, so the login sync is a tested no-op (D3). No live IdP.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from api import membership, sync
from api.db import tenant_session
from api.models import GroupMember
from tests._api_helpers import seed_member, seed_workspace

pytestmark = pytest.mark.integration

# Native IdP refs — NO naming convention assumed (FR-021): an Okta group NAME, an Entra object-GUID, a SAML
# attribute value. The binding maps AC role/team → whichever native ref the org's IdP uses.
R_VIEWER = "Okta-AC-Viewers"
R_BUILDER = "Okta-AC-Builders"
R_PUB = "11112222-3333-4444-5555-666677778888"     # Entra object-GUID (no prefix convention)
T_SOC = "saml-team=SOC"                              # SAML attribute value
T_DET = "grp-detect"

BINDING = {
    "roles": {"viewer": R_VIEWER, "builder": R_BUILDER, "publisher": R_PUB},
    "teams": {"SOC": T_SOC, "Detection": T_DET},
}


# ---- T006: fake enumerating adapter + claim-less stand-in ----------------------------------------
class FakeIdP:
    """An IdP that CAN enumerate (D2 capability): scripted `groups_for`/`members_of`. Returning None means
    'can't tell us' (distinct from []); a list is authoritative."""
    id = "fake-idp"

    def __init__(self, groups: dict | None = None, members: dict | None = None):
        self._groups = groups or {}
        self._members = members or {}

    async def groups_for(self, subject):
        return self._groups.get(subject)            # None when the subject isn't scripted

    async def members_of(self, group_ref):
        return self._members.get(group_ref)         # None when the group isn't scripted


class ClaimlessIdP:
    """A trellix-iam-style provider: `verify` only, NO group-enumeration capability (D3)."""
    id = "trellix-iam"

    async def verify(self, request):                # pragma: no cover - not exercised by the sync path
        raise NotImplementedError


# ---- T007 / FR-021, AS1: name-mapped role + team synced into the members store -------------------
async def test_sync_sets_role_team_from_name_mapping():
    ws = await seed_workspace(idp_binding=BINDING)
    async with tenant_session(ws) as s:
        await sync.sync_member_from_groups(s, ws, "alice", [R_BUILDER, T_SOC])
    async with tenant_session(ws) as s:
        assert await membership.role_for(s, "alice") == "builder"
        assert await membership.teams_for(s, "alice") == ["SOC"]


# ---- T008 / FR-021, AS4: GUID + attribute refs resolve with no name-prefix assumption ------------
async def test_sync_resolves_guid_and_attr_refs():
    ws = await seed_workspace(idp_binding=BINDING)
    async with tenant_session(ws) as s:
        await sync.sync_member_from_groups(s, ws, "bob", [R_PUB, T_SOC])    # Entra GUID role + SAML attr team
    async with tenant_session(ws) as s:
        assert await membership.role_for(s, "bob") == "publisher"
        assert await membership.teams_for(s, "bob") == ["SOC"]


# ---- T009 / FR-022, AS2: a no-mapping-match login grants nothing (fail-closed) -------------------
async def test_no_match_grants_nothing():
    ws = await seed_workspace(idp_binding=BINDING)
    async with tenant_session(ws) as s:
        await sync.sync_member_from_groups(s, ws, "carol", ["totally-unrelated-group"])
    async with tenant_session(ws) as s:
        assert await membership.role_for(s, "carol") is None               # no member row written at all


# ---- T010 / FR-021: no live group→role path — groups alone never grant; only the members store ---
async def test_no_live_group_to_role_path():
    ws = await seed_workspace(idp_binding=BINDING)
    # A user whose groups WOULD map, but who never went through the sync writer → still nothing. There is no
    # per-request resolver that turns groups into a role; the members store is the only authority.
    async with tenant_session(ws) as s:
        assert await membership.role_for(s, "dora") is None
    async with tenant_session(ws) as s:
        await sync.sync_member_from_groups(s, ws, "dora", [R_VIEWER])
    async with tenant_session(ws) as s:
        assert await membership.role_for(s, "dora") == "viewer"            # only after the write
    # structural: the sync module exposes no per-request group→role resolver
    assert not hasattr(sync, "role_for")
    assert not hasattr(sync, "resolve_role_for_request")


# ---- T011 / FR-023, AS3: login projection is cache-only, never an authZ source -------------------
async def test_projection_login_seen_not_authz():
    ws = await seed_workspace(idp_binding=BINDING)
    async with tenant_session(ws) as s:
        await sync.upsert_group_projection(s, ws, "erin", [R_BUILDER, T_SOC], source="login_seen")
    async with tenant_session(ws) as s:
        rows = (await s.execute(select(GroupMember).where(GroupMember.subject == "erin"))).scalars().all()
        assert {r.group_ref for r in rows} == {R_BUILDER, T_SOC}
        assert all(r.source == "login_seen" for r in rows)
        # the projection row grants NOTHING — erin has no member row → no role (FR-023)
        assert await membership.role_for(s, "erin") is None


# ---- T012 / FR-023, AS3: scheduled refresh enumerates members_of → source='members_of' -----------
async def test_scheduled_refresh_enumerates():
    ws = await seed_workspace(idp_binding=BINDING)
    provider = FakeIdP(members={T_SOC: ["frank", "grace"], R_BUILDER: ["frank"]})
    async with tenant_session(ws) as s:
        await sync.refresh_group_projection(s, ws, provider=provider)
    async with tenant_session(ws) as s:
        rows = (await s.execute(select(GroupMember))).scalars().all()
        got = {(r.group_ref, r.subject, r.source) for r in rows}
    assert (T_SOC, "frank", "members_of") in got
    assert (T_SOC, "grace", "members_of") in got
    assert (R_BUILDER, "frank", "members_of") in got


# ---- T013 / D3: a claim-less provider → login sync is a no-op; login still succeeds --------------
async def test_claimless_provider_sync_is_noop():
    ws = await seed_workspace(idp_binding=BINDING)
    await sync.run_login_sync("heidi", ClaimlessIdP())          # no groups_for capability → whole no-op
    async with tenant_session(ws) as s:
        assert await membership.role_for(s, "heidi") is None
    # and a direct sync with groups=None is likewise a no-op (never revoke)
    async with tenant_session(ws) as s:
        await sync.sync_member_from_groups(s, ws, "heidi", None)
    async with tenant_session(ws) as s:
        assert await membership.role_for(s, "heidi") is None


# ---- T014 / D4(revoke) + D3(None never revokes): reconcile on an authoritative shrink ------------
async def test_group_shrink_revokes_but_none_is_noop():
    ws = await seed_workspace(idp_binding=BINDING)
    async with tenant_session(ws) as s:
        await sync.sync_member_from_groups(s, ws, "judy", [R_PUB, T_SOC])    # publisher + SOC
    async with tenant_session(ws) as s:
        assert await membership.role_for(s, "judy") == "publisher"
        assert await membership.teams_for(s, "judy") == ["SOC"]

    # authoritative shrink → builder, no team: revoke publisher→builder and clear SOC (D4)
    async with tenant_session(ws) as s:
        await sync.sync_member_from_groups(s, ws, "judy", [R_BUILDER])
    async with tenant_session(ws) as s:
        assert await membership.role_for(s, "judy") == "builder"
        assert await membership.teams_for(s, "judy") == []

    # authoritative no-match but the row EXISTS → clear to the fail-closed default, keep the row (D4)
    async with tenant_session(ws) as s:
        await sync.sync_member_from_groups(s, ws, "judy", ["unrelated"])
    async with tenant_session(ws) as s:
        assert await membership.role_for(s, "judy") == "viewer"             # cleared, row retained

    # restore to builder, then a None (can't-enumerate) result must NOT revoke (D3)
    async with tenant_session(ws) as s:
        await sync.sync_member_from_groups(s, ws, "judy", [R_BUILDER])
    async with tenant_session(ws) as s:
        await sync.sync_member_from_groups(s, ws, "judy", None)
    async with tenant_session(ws) as s:
        assert await membership.role_for(s, "judy") == "builder"            # unchanged by None


# ---- T015 / P5: the sync writes only within the tenant (RLS) session -----------------------------
async def test_sync_is_workspace_scoped():
    ws_a = await seed_workspace(idp_binding=BINDING)
    ws_b = await seed_workspace(idp_binding=BINDING)
    async with tenant_session(ws_a) as s:
        await sync.sync_member_from_groups(s, ws_a, "kyle", [R_BUILDER])
    async with tenant_session(ws_a) as s:
        assert await membership.role_for(s, "kyle") == "builder"
    async with tenant_session(ws_b) as s:
        assert await membership.role_for(s, "kyle") is None                 # not leaked across tenants (RLS)


# ---- code-review #2a: a teams-only binding must NOT downgrade an existing member's role -----------
async def test_teams_only_binding_preserves_existing_role():
    """A binding that maps only teams (no "roles" key) says nothing about console roles. Reconciling it must leave
    an existing member's role intact — not silently demote a publisher to viewer on login (#2a). Teams still sync."""
    teams_only = {"teams": {"SOC": T_SOC, "Detection": T_DET}}
    ws = await seed_workspace(idp_binding=teams_only)
    await seed_member(ws, "pat", role="publisher", teams=[])
    async with tenant_session(ws) as s:
        await sync.sync_member_from_groups(s, ws, "pat", [T_SOC])        # holds a mapped team; binding has no roles
    async with tenant_session(ws) as s:
        assert await membership.role_for(s, "pat") == "publisher"        # role preserved, NOT downgraded to viewer
        assert await membership.teams_for(s, "pat") == ["SOC"]           # team still reconciled


# ---- T019 wiring: run_login_sync drives the members sync + login projection on an authoritative IdP
async def test_run_login_sync_wires_members_and_projection():
    ws = await seed_workspace(idp_binding=BINDING)
    await sync.run_login_sync("ivan", FakeIdP(groups={"ivan": [R_PUB, T_SOC]}))
    async with tenant_session(ws) as s:
        assert await membership.role_for(s, "ivan") == "publisher"
        rows = (await s.execute(select(GroupMember).where(GroupMember.subject == "ivan"))).scalars().all()
        assert {r.group_ref for r in rows} == {R_PUB, T_SOC}
        assert all(r.source == "login_seen" for r in rows)
