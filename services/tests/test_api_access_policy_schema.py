"""Feature 013 — access_policy round-trips through create/patch and is surfaced on *Out (FR-018).

The API accepts and returns the camelCase policy shape so the console can SET a policy and read it back. Uses an
admin caller so the (later) USE enforcement never hides the row from its own creator. Malformed → 400 input.invalid.
"""
from __future__ import annotations

import pytest

from tests._api_helpers import client, dev_headers, seed_workspace

pytestmark = pytest.mark.integration

_RESTRICTED = {"visibility": "restricted", "useRoles": ["builder"], "useTeams": ["SOC"],
               "manageRoles": ["publisher"], "manageTeams": ["IR"]}


async def test_agent_create_and_get_round_trip_access_policy():
    ws = await seed_workspace()
    async with client() as c:
        h = dev_headers(workspace=ws, claims={ws: "admin"})
        created = await c.post("/api/v1/agents", headers=h, json={
            "name": "A1", "provider": "openai", "model": "gpt-5-mini",
            "io_contract": {"input_schema": {"type": "object"}}, "access_policy": _RESTRICTED})
        assert created.status_code == 201, created.text
        assert created.json()["access_policy"]["visibility"] == "restricted"
        aid = created.json()["id"]
        got = (await c.get(f"/api/v1/agents/{aid}", headers=h)).json()
    assert sorted(got["access_policy"]["useRoles"]) == ["builder"]
    assert got["access_policy"]["useTeams"] == ["SOC"]


async def test_kb_create_defaults_workspace_and_patch_sets_policy():
    ws = await seed_workspace()
    async with client() as c:
        h = dev_headers(workspace=ws, claims={ws: "admin"})
        created = await c.post("/api/v1/knowledge-bases", headers=h,
                               json={"slug": "kb1", "name": "KB1", "embedding_model": "text-embedding-3-small"})
        assert created.status_code == 201, created.text
        assert created.json()["access_policy"]["visibility"] == "workspace"   # default
        kid = created.json()["id"]
        etag = created.headers["ETag"]
        patched = await c.patch(f"/api/v1/knowledge-bases/{kid}",
                                headers=dev_headers(workspace=ws, claims={ws: "admin"}, if_match=etag),
                                json={"access_policy": _RESTRICTED})
    assert patched.status_code == 200, patched.text
    assert patched.json()["access_policy"]["visibility"] == "restricted"


async def test_malformed_access_policy_is_400():
    ws = await seed_workspace()
    async with client() as c:
        h = dev_headers(workspace=ws, claims={ws: "admin"})
        r = await c.post("/api/v1/agents", headers=h, json={
            "name": "A2", "provider": "openai", "model": "gpt-5-mini",
            "io_contract": {"input_schema": {"type": "object"}},
            "access_policy": {"visibility": "nonsense"}})
    assert r.status_code == 400 and r.json()["error"]["code"] == "input.invalid"
