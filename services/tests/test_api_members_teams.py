"""Feature 013 — member `teams` round-trips through the roster API, RLS/workspace-scoped (FR-016).

Teams are server-owned authZ data (the real IdP token carries no groups), stored on the member row under RLS exactly
as `console_role` is since feature 012. The access-policy evaluator reads them to resolve `restricted` team gating.
"""
from __future__ import annotations

import pytest

from tests._api_helpers import client, dev_headers, seed_workspace

pytestmark = pytest.mark.integration


async def test_member_teams_round_trip_on_create_and_patch():
    ws = await seed_workspace()
    async with client() as c:
        h = dev_headers(workspace=ws, claims={ws: "admin"})
        # create with teams
        created = await c.post("/api/v1/members", headers=h,
                               json={"subject": "alice", "teams": ["SOC", "IR"]})
        assert created.status_code == 201, created.text
        body = created.json()
        mid = body["id"]
        assert sorted(body["teams"]) == ["IR", "SOC"]
        # list returns teams
        lst = (await c.get("/api/v1/members", headers=h)).json()
        row = next(m for m in lst["items"] if m["subject"] == "alice")
        assert sorted(row["teams"]) == ["IR", "SOC"]
        # patch replaces teams
        patched = await c.patch(f"/api/v1/members/{mid}", headers=h, json={"teams": ["SOC"]})
        assert patched.status_code == 200 and patched.json()["teams"] == ["SOC"]


async def test_member_teams_default_empty_when_omitted():
    ws = await seed_workspace()
    async with client() as c:
        h = dev_headers(workspace=ws, claims={ws: "admin"})
        created = await c.post("/api/v1/members", headers=h, json={"subject": "bob"})
    assert created.status_code == 201 and created.json()["teams"] == []


async def test_member_teams_are_workspace_scoped():
    ws_a = await seed_workspace()
    ws_b = await seed_workspace()
    async with client() as c:
        await c.post("/api/v1/members", headers=dev_headers(workspace=ws_a, claims={ws_a: "admin"}),
                     json={"subject": "carol", "teams": ["SOC"]})
        # ws_b roster must not see ws_a's member (RLS)
        lst_b = (await c.get("/api/v1/members",
                             headers=dev_headers(workspace=ws_b, claims={ws_b: "admin"}))).json()
    assert all(m["subject"] != "carol" for m in lst_b["items"])
