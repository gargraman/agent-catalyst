"""Workspace discovery — GET /api/v1/workspaces (the console switcher's source of accessible workspaces).

This is a PRE-selection read: the caller is authenticated but no workspace is chosen yet, so it is inherently
cross-tenant. `members` is RLS-scoped on `app.workspace_id` (0005) and the app role must not bypass RLS broadly
(09 §7); the real IdP token carries no groups (0006), so the members store is the only source of a subject's
workspaces. Resolution goes through the `member_workspaces()` SECURITY DEFINER function (migration 0011) on the
unscoped `auth_lookup_session` — the same no-broad-bypass pattern as `api_token_resolve` (0008). A caller only
ever sees workspaces they hold a member row in.
"""
from __future__ import annotations

import json
import uuid

import pytest

from tests._api_helpers import client, seed_member, seed_workspace


def _auth(subject: str) -> dict:
    """An authenticated dev principal with NO workspace selected — discovery runs before selection."""
    return {"X-Dev-Principal": json.dumps({"subject": subject, "workspace_claims": {}})}


# ---- unit (no DB) --------------------------------------------------------------------------------
async def test_requires_authentication():
    async with client() as c:
        r = await c.get("/api/v1/workspaces")
    assert r.status_code == 401 and r.json()["error"]["code"] == "auth.required"


# ---- integration (compose DB) --------------------------------------------------------------------
@pytest.mark.integration
async def test_lists_only_workspaces_the_subject_is_a_member_of():
    subject = f"disco-{uuid.uuid4().hex[:8]}"
    ws_a = await seed_workspace(name="Alpha")
    ws_b = await seed_workspace(name="Beta")
    await seed_member(ws_a, subject=subject, role="publisher")
    async with client() as c:
        r = await c.get("/api/v1/workspaces", headers=_auth(subject))
    assert r.status_code == 200
    by_id = {w["id"]: w for w in r.json()}
    assert ws_a in by_id, "member workspace must be listed"
    assert ws_b not in by_id, "non-member workspace must NOT leak"
    assert by_id[ws_a]["role"] == "publisher"    # resolved console role rides along for the switcher
    assert by_id[ws_a]["name"] == "Alpha"


@pytest.mark.integration
async def test_empty_when_subject_has_no_memberships():
    subject = f"disco-none-{uuid.uuid4().hex[:8]}"
    await seed_workspace(name="Gamma")           # exists, but the subject is not a member
    async with client() as c:
        r = await c.get("/api/v1/workspaces", headers=_auth(subject))
    assert r.status_code == 200 and r.json() == []
