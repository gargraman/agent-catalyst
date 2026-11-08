"""Feature 012 (M5) US1 — membership-sourced authorization (contract §5 items 14–16; FR-013/FR-014).

A verified principal carries no AC role (empty workspace_claims); resolve_workspace must derive the role from
the `members` table for the asserted workspace, under RLS. Asserts: role resolved → require_permission enforces
it; no membership → 403 (no existence leak); cross-tenant membership does not grant access.
"""
from __future__ import annotations

import contextlib

import pytest

from api.deps import Principal, session_principal
from api.main import app
from tests._api_helpers import client, seed_member, seed_workspace

pytestmark = pytest.mark.integration


@contextlib.contextmanager
def _as_verified(subject: str):
    """Stand in for a real provider: a verified identity with NO role claim (forces the membership path)."""
    async def _override():
        return Principal(subject=subject, workspace_claims={}, scopes=("xdr.alr.rw",),
                         issuer="https://test.idp.local/iam")
    app.dependency_overrides[session_principal] = _override
    try:
        yield
    finally:
        app.dependency_overrides.pop(session_principal, None)


async def test_role_resolved_from_membership_allows_view():
    ws = await seed_workspace()
    await seed_member(ws, subject="svc-1", role="builder")
    with _as_verified("svc-1"):
        async with client() as c:
            r = await c.get("/api/v1/agents", headers={"X-Workspace-Id": ws})
    assert r.status_code == 200                           # builder has `view`, role came from the members table


async def test_membership_role_is_enforced_for_higher_permission():
    ws = await seed_workspace()
    await seed_member(ws, subject="svc-1", role="viewer")  # viewer lacks agent.create
    with _as_verified("svc-1"):
        async with client() as c:
            r = await c.post("/api/v1/agents", headers={"X-Workspace-Id": ws},
                             json={"name": "x", "provider": "openai", "model": "gpt-5-mini",
                                   "system_prompt": "hi"})
    assert r.status_code == 403                           # role from membership enforced by require_permission
    assert r.json()["error"]["code"] == "access.denied"


async def test_no_membership_denied_fail_closed():
    ws = await seed_workspace()                            # no member seeded
    with _as_verified("svc-1"):
        async with client() as c:
            r = await c.get("/api/v1/agents", headers={"X-Workspace-Id": ws})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "access.denied"


async def test_cross_tenant_membership_does_not_grant_access():
    ws_a = await seed_workspace()
    ws_b = await seed_workspace()
    await seed_member(ws_a, subject="svc-1", role="builder")  # member of A only
    with _as_verified("svc-1"):
        async with client() as c:
            r = await c.get("/api/v1/agents", headers={"X-Workspace-Id": ws_b})
    assert r.status_code == 403                            # no membership in B → denied (RLS-scoped lookup)
