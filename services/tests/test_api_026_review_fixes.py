"""Feature 026 — regressions for the post-MVP /code-review findings (2026-11-16).

#1/#2  A NULL draft clones the active version EXACTLY: model_config incl. api_version, frozen profile_snapshot,
       guardrail/memory/context — and a base whose capability profile has since been retired still clones (no 500).
#3     A draft-bound registry tool snapshots a PROVIDER-VALID def (an `input_schema` object) — Anthropic rejects a
       tool without one, so the old {name, description} snapshot broke every invoke of that version.
#6     An archived agent is read-only: draft / metadata / re-point / version-create → 409 `agent.archived`;
       only the restore transition is accepted.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from api.db import tenant_session
from api.models import Agent, AgentVersion
from tests._api_helpers import client, dev_headers, seed_agent, seed_tool, seed_workspace
from tests._invoke_helpers import PROFILE_SNAPSHOT

pytestmark = pytest.mark.integration

IO = {"input_schema": {"type": "object", "properties": {"message": {"type": "string"}}}, "output_format": "text"}


async def _version(ws, vid) -> AgentVersion:
    async with tenant_session(ws) as s:
        return (await s.execute(select(AgentVersion).where(AgentVersion.id == uuid.UUID(vid)).options(
            selectinload(AgentVersion.tool_bindings), selectinload(AgentVersion.skill_bindings),
            selectinload(AgentVersion.retrieval_bindings)))).scalar_one()


async def _seed_base(ws, *, model_config: dict, profile: dict | None = None) -> tuple[str, str]:
    """A base version with non-default config, written in ONE transaction (0015 freezes a committed version)."""
    aid = await seed_agent(ws)
    async with tenant_session(ws) as s:
        v = AgentVersion(
            workspace_id=uuid.UUID(ws), agent_id=uuid.UUID(aid), label="v1", system_prompt="Base prompt.",
            prompt_hash="ph-base", snapshot_hash="sh-base", model_config_=model_config,
            profile_snapshot=profile or {**PROFILE_SNAPSHOT, "frozen_marker": True}, io_contract=IO,
            guardrail_config={"g": 1}, memory_config={"m": 2}, context_config={"c": 3})
        s.add(v)
        await s.flush()
        (await s.get(Agent, uuid.UUID(aid))).active_version_id = v.id
        await s.flush()
        return aid, str(v.id)


async def test_null_draft_clone_keeps_full_model_config_and_frozen_profile():
    ws = await seed_workspace()
    mc = {"provider": "openai", "model": "gpt-5-mini", "api_version": "2025-01-01", "params": {"temperature": 0.2}}
    aid, base_id = await _seed_base(ws, model_config=mc)
    async with client() as c:
        r1 = await c.post(f"/api/v1/agents/{aid}/versions", headers=dev_headers(workspace=ws, subject="carol"))
        r2 = await c.post(f"/api/v1/agents/{aid}/versions", headers=dev_headers(workspace=ws))
    assert r1.status_code == 201, r1.text
    base, v = await _version(ws, base_id), await _version(ws, r1.json()["id"])
    assert v.model_config_ == mc                                     # api_version kept (was dropped by project())
    assert v.profile_snapshot == base.profile_snapshot and v.prompt_hash == base.prompt_hash
    assert v.system_prompt == base.system_prompt and v.io_contract == base.io_contract
    assert (v.guardrail_config, v.memory_config, v.context_config) == ({"g": 1}, {"m": 2}, {"c": 3})
    assert v.created_by == "carol"
    assert r1.json()["snapshot_hash"] == r2.json()["snapshot_hash"]   # identical content ⇒ identical hash (F4)


async def test_null_draft_clone_of_a_retired_profile_is_not_a_500():
    ws = await seed_workspace()
    mc = {"provider": "openai", "model": "gpt-retired-0", "params": {}}
    aid, base_id = await _seed_base(ws, model_config=mc, profile={"provider": "openai", "model": "gpt-retired-0"})
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/versions", headers=dev_headers(workspace=ws))
    assert r.status_code == 201, r.text
    v = await _version(ws, r.json()["id"])
    assert v.model_config_ == mc and v.profile_snapshot == {"provider": "openai", "model": "gpt-retired-0"}


async def _agent(c, ws) -> tuple[dict, str]:
    r = await c.post("/api/v1/agents", json={"name": "A", "provider": "openai", "model": "gpt-5-mini",
                                             "system_prompt": "You are helpful.", "io_contract": IO},
                     headers=dev_headers(workspace=ws))
    assert r.status_code == 201, r.text
    return r.json(), r.headers["ETag"]


def _draft(**over) -> dict:
    d = {"provider": "openai", "model": "gpt-5-mini", "model_params": {}, "system_prompt": "You are helpful.",
         "io_contract": IO, "kb_ids": [], "tool_ids": [], "skill_ids": []}
    d.update(over)
    return d


@pytest.mark.parametrize("http_config, expected", [
    ({"endpoint": "https://x.example"}, {"type": "object", "properties": {}}),
    ({"endpoint": "https://x.example", "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}}},
     {"type": "object", "properties": {"q": {"type": "string"}}}),
])
async def test_draft_bound_tool_snapshot_is_a_provider_valid_def(http_config, expected):
    ws = await seed_workspace()
    t = await seed_tool(ws, name="lookup", http_config=http_config)
    async with client() as c:
        a, etag = await _agent(c, ws)
        p = await c.patch(f"/api/v1/agents/{a['id']}", json={"draft": _draft(tool_ids=[t])},
                          headers=dev_headers(workspace=ws, if_match=etag))
        assert p.status_code == 200, p.text
        r = await c.post(f"/api/v1/agents/{a['id']}/versions", headers=dev_headers(workspace=ws))
    assert r.status_code == 201, r.text
    (b,) = (await _version(ws, r.json()["id"])).tool_bindings
    assert b.schema_snapshot == {"name": "lookup", "description": "seed", "input_schema": expected}


async def _archived(c, ws) -> tuple[dict, str]:
    a, etag = await _agent(c, ws)
    r = await c.patch(f"/api/v1/agents/{a['id']}", json={"status": "archived"},
                      headers=dev_headers(workspace=ws, claims={ws: "admin"}, if_match=etag))   # archive = admin (026 #9)
    assert r.status_code == 200, r.text
    return r.json(), r.headers["ETag"]


@pytest.mark.parametrize("body", [
    {"draft": _draft(system_prompt="changed")},
    {"name": "renamed"},
    {"description": "d"},
    {"access_policy": {"visibility": "workspace"}},   # a VALID policy (Red review: "public" was malformed → 400)
    {"active_version_id": "__v1__"},
    {"status": "archived", "name": "renamed"},
])
async def test_archived_agent_refuses_edits(body):
    ws = await seed_workspace()
    async with client() as c:
        a, etag = await _archived(c, ws)
        if body.get("active_version_id") == "__v1__":
            body = {"active_version_id": a["versions"][0]["id"]}
        r = await c.patch(f"/api/v1/agents/{a['id']}", json=body, headers=dev_headers(workspace=ws, if_match=etag))
        after = await c.get(f"/api/v1/agents/{a['id']}", headers=dev_headers(workspace=ws))
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "agent.archived"
    assert after.headers["ETag"] == etag                       # nothing was written


async def test_archived_agent_refuses_version_create():
    ws = await seed_workspace()
    async with client() as c:
        a, _ = await _archived(c, ws)
        r = await c.post(f"/api/v1/agents/{a['id']}/versions", headers=dev_headers(workspace=ws))
    assert r.status_code == 409 and r.json()["error"]["code"] == "agent.archived"


async def test_archived_agent_can_still_be_restored_then_edited():
    ws = await seed_workspace()
    async with client() as c:
        a, etag = await _archived(c, ws)
        r = await c.patch(f"/api/v1/agents/{a['id']}", json={"status": a["restore_status"]},
                          headers=dev_headers(workspace=ws, claims={ws: "admin"}, if_match=etag))   # restore = admin
        assert r.status_code == 200, r.text
        e = await c.patch(f"/api/v1/agents/{a['id']}", json={"name": "renamed"},
                          headers=dev_headers(workspace=ws, if_match=r.headers["ETag"]))
    assert e.status_code == 200 and e.json()["name"] == "renamed"
