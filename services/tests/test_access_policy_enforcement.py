"""Feature 013 — enforcement-spine helpers (`ensure_use`/`ensure_manage` raise-mapping; `teams_for` fail-closed).

The pure decision logic is proven in `test_access_policy_eval.py`; this pins how the API layer maps a DENY to an
HTTP error — the no-leak contract (P5): a caller who can't USE gets `NotFound` (404) everywhere, so visibility is
never leaked; a caller who can USE but not MANAGE gets `AccessDenied` (403). `teams_for` returns `[]` for a caller
with no member row (fail-closed, never raises) so default-`workspace` resources keep working for dev principals.
"""
from __future__ import annotations

import pytest

from api.access_policy import PolicyPrincipal
from api.deps import ensure_manage, ensure_use
from api.errors import AccessDenied, NotFound

_OUTSIDER = PolicyPrincipal(role="builder", teams=("X",), subject="u-out")
_ADMIN = PolicyPrincipal(role="admin", teams=(), subject="u-admin")


def test_ensure_use_denies_with_notfound_no_leak():
    with pytest.raises(NotFound):
        ensure_use({"visibility": "private", "owner": "someone-else"}, _OUTSIDER)


def test_ensure_use_allows_workspace_default():
    ensure_use({"visibility": "workspace"}, _OUTSIDER)   # no raise
    ensure_use(None, _OUTSIDER)                            # None → workspace default, no raise


def test_ensure_manage_notfound_when_not_usable():
    # can't even USE → 404 (never reveal the resource exists)
    with pytest.raises(NotFound):
        ensure_manage({"visibility": "private", "owner": "someone-else"}, _OUTSIDER)


def test_ensure_manage_accessdenied_when_usable_but_not_manageable():
    # usable (use role matches) but not manageable (manage role does not) → 403
    policy = {"visibility": "restricted", "useRoles": ["builder"], "manageRoles": ["publisher"]}
    with pytest.raises(AccessDenied):
        ensure_manage(policy, _OUTSIDER)


def test_ensure_manage_admin_always_allowed():
    ensure_manage({"visibility": "private", "owner": "someone-else"}, _ADMIN)   # no raise


@pytest.mark.integration
async def test_teams_for_returns_empty_for_memberless_caller():
    from api import membership
    from api.db import tenant_session
    from tests._api_helpers import seed_workspace

    ws = await seed_workspace()
    async with tenant_session(ws) as s:
        teams = await membership.teams_for(s, "nobody-here")
    assert teams == []


@pytest.mark.integration
async def test_teams_for_returns_member_teams():
    from api import membership
    from api.db import tenant_session
    from api.models import Member
    import uuid
    from tests._api_helpers import seed_workspace

    ws = await seed_workspace()
    async with tenant_session(ws) as s:
        s.add(Member(workspace_id=uuid.UUID(ws), subject="alice", display_name="a",
                     email="a@x.com", console_role="builder", teams=["SOC", "IR"]))
        await s.flush()
    async with tenant_session(ws) as s:
        teams = await membership.teams_for(s, "alice")
    assert sorted(teams) == ["IR", "SOC"]
