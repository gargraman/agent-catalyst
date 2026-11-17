"""Feature 026 — second /code-review, finding #4: saving a version from a draft must not silently drop config the
draft can't express.

The Draft shape has no `api_version` and edits registry tools only (http/builtin). `version_from_draft` rebuilt
`model_config` as {provider, model, params} and `draft_bindings` built registry tools only — so the first version
saved after ANY draft edit lost the base's `api_version` (hashed; selects the provider profile) and every MCP /
subagent tool binding. The NULL-draft clone path was already fixed for this (review #1); the draft path wasn't.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from api.db import tenant_session
from api.models import Agent, AgentVersion, McpTool, ToolBinding
from tests._api_helpers import PROFILE_SNAPSHOT, client, dev_headers, seed_agent, seed_mcp_server, seed_workspace

pytestmark = pytest.mark.integration

IO = {"input_schema": {"type": "object", "properties": {"message": {"type": "string"}}}, "output_format": "text"}


async def _seed_base_with_mcp_and_api_version():
    ws = await seed_workspace()
    aid = await seed_agent(ws)
    server = await seed_mcp_server(ws, with_tool=True)
    wsid = uuid.UUID(ws)
    async with tenant_session(ws) as s:
        mcp_tool = (await s.execute(select(McpTool.id).where(McpTool.server_id == uuid.UUID(server)))).scalar_one()
        v = AgentVersion(workspace_id=wsid, agent_id=uuid.UUID(aid), label="v1",
                         system_prompt="base prompt", prompt_hash="ph", snapshot_hash="sh",
                         model_config_={"provider": "openai", "model": "gpt-5-mini", "api_version": "2026-01",
                                        "params": {"temperature": 0.2}},
                         profile_snapshot=PROFILE_SNAPSHOT, io_contract=IO)
        s.add(v)
        await s.flush()
        s.add(ToolBinding(workspace_id=wsid, version_id=v.id, kind="mcp", mcp_tool_id=mcp_tool,
                          schema_snapshot={"name": "t1", "input_schema": {"type": "object"}}, config_snapshot={}))
        await s.flush()
        (await s.get(Agent, uuid.UUID(aid))).active_version_id = v.id
    return ws, aid, mcp_tool


async def test_version_from_draft_keeps_api_version_and_mcp_bindings():
    ws, aid, mcp_tool = await _seed_base_with_mcp_and_api_version()
    h = dev_headers(workspace=ws)
    async with client() as c:
        got = await c.get(f"/api/v1/agents/{aid}", headers=h)
        draft = {**got.json()["draft"], "system_prompt": "edited prompt"}
        draft.pop("restricted_bindings", None)
        p = await c.patch(f"/api/v1/agents/{aid}", json={"draft": draft},
                          headers=dev_headers(workspace=ws, if_match=got.headers["ETag"]))
        assert p.status_code == 200, p.text
        v2 = await c.post(f"/api/v1/agents/{aid}/versions", headers=h)
        assert v2.status_code == 201, v2.text
    async with tenant_session(ws) as s:
        row = (await s.execute(select(AgentVersion).options(selectinload(AgentVersion.tool_bindings))
                               .where(AgentVersion.id == uuid.UUID(v2.json()["id"])))).scalar_one()
    assert row.system_prompt == "edited prompt"
    assert row.model_config_.get("api_version") == "2026-01"
    assert row.profile_snapshot == PROFILE_SNAPSHOT                    # same model → the frozen profile is inherited
    assert [(b.kind, b.mcp_tool_id) for b in row.tool_bindings] == [("mcp", mcp_tool)]


async def test_switching_model_in_the_draft_does_not_carry_the_old_api_version():
    ws, aid, _ = await _seed_base_with_mcp_and_api_version()
    h = dev_headers(workspace=ws)
    async with client() as c:
        got = await c.get(f"/api/v1/agents/{aid}", headers=h)
        draft = {**got.json()["draft"], "provider": "anthropic", "model": "claude-haiku-5-5"}
        draft.pop("restricted_bindings", None)
        p = await c.patch(f"/api/v1/agents/{aid}", json={"draft": draft},
                          headers=dev_headers(workspace=ws, if_match=got.headers["ETag"]))
        assert p.status_code == 200, p.text
        v2 = await c.post(f"/api/v1/agents/{aid}/versions", headers=h)
        assert v2.status_code == 201, v2.text
    async with tenant_session(ws) as s:
        row = await s.get(AgentVersion, uuid.UUID(v2.json()["id"]))
    assert row.model_config_["model"] == "claude-haiku-5-5" and "api_version" not in row.model_config_
    assert row.profile_snapshot != PROFILE_SNAPSHOT                    # never the old model's frozen profile …
    assert row.profile_snapshot.get("model") == "claude-haiku-5-5"     # … but the new model's


# --- review-2 #8: the console must know each version's input contract ---------------------------------------------
async def test_versions_expose_their_io_contract_so_the_console_sends_the_right_field():
    """A version's input field can differ from the draft's (renamed in the draft, or an older version). Without the
    version's own io_contract the playground sent `target=version` turns under the draft's field name → refused."""
    from tests._api_helpers import seed_version
    ws = await seed_workspace()
    aid = await seed_agent(ws)
    old_io = {"input_schema": {"type": "object", "required": ["question"],
                               "properties": {"question": {"type": "string"}}}, "output_format": "text"}
    vid = await seed_version(ws, aid, io_contract=old_io)
    async with client() as c:
        got = await c.get(f"/api/v1/agents/{aid}", headers=dev_headers(workspace=ws))
    assert got.status_code == 200, got.text
    (v,) = [x for x in got.json()["versions"] if x["id"] == vid]
    assert v["io_contract"]["input_schema"]["required"] == ["question"]
