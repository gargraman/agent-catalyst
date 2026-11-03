"""US3 — typed domain model: CRUD, relationship navigation, and loud immutability
(feature 001, spec FR-001/010, SC-006).

Integration (real DB via ac_app tenant_session); skips when DB unreachable.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from api.db import tenant_session
from api.models import Agent, AgentVersion, KnowledgeBase, RetrievalBinding, SkillBinding, ToolBinding, Workspace

pytestmark = pytest.mark.integration


async def _seed(session, ws_id):
    kb = KnowledgeBase(workspace_id=ws_id, slug=f"kb-{uuid.uuid4().hex[:6]}", name="KB",
                       embedding_model="text-embedding-3-small", embedding_dim=1536)
    agent = Agent(workspace_id=ws_id, name="orm-agent", status="draft")
    session.add_all([kb, agent])
    await session.flush()
    av = AgentVersion(
        workspace_id=ws_id, agent_id=agent.id, label="v1", system_prompt="orig",
        prompt_hash="ph", snapshot_hash="seed", model_config_={"model": "m", "provider": "p"},
    )
    session.add(av)
    await session.flush()
    session.add_all([
        ToolBinding(workspace_id=ws_id, version_id=av.id, kind="http", tool_id=uuid.uuid4(),
                    schema_snapshot={}, config_snapshot={}),
        SkillBinding(workspace_id=ws_id, version_id=av.id, skill_id=uuid.uuid4(), skill_content_hash="h"),
        RetrievalBinding(workspace_id=ws_id, version_id=av.id, kb_id=kb.id, kb_index_version=1,
                         retrieval_config={}, injection="tool"),
    ])
    await session.flush()
    return agent.id, av.id


async def _new_ws():
    async with tenant_session(uuid.uuid4()) as s:
        ws = Workspace(name=f"ws-{uuid.uuid4().hex[:8]}")
        s.add(ws)
        await s.flush()
        return ws.id


async def test_typed_crud_and_relationships():
    ws_id = await _new_ws()
    async with tenant_session(ws_id) as s:
        agent_id, av_id = await _seed(s, ws_id)
    async with tenant_session(ws_id) as s:
        agent = (await s.execute(
            select(Agent).options(selectinload(Agent.versions)).where(Agent.id == agent_id)
        )).scalar_one()
        assert any(v.id == av_id for v in agent.versions), "agent → versions navigable"
        av = (await s.execute(
            select(AgentVersion).options(
                selectinload(AgentVersion.tool_bindings),
                selectinload(AgentVersion.skill_bindings),
                selectinload(AgentVersion.retrieval_bindings),
            ).where(AgentVersion.id == av_id)
        )).scalar_one()
        assert len(av.tool_bindings) == 1 and len(av.skill_bindings) == 1 and len(av.retrieval_bindings) == 1


async def test_mutable_column_update_allowed():
    ws_id = await _new_ws()
    async with tenant_session(ws_id) as s:
        _, av_id = await _seed(s, ws_id)
    async with tenant_session(ws_id) as s:  # publishable is mutable post-freeze (0002)
        av = (await s.execute(select(AgentVersion).where(AgentVersion.id == av_id))).scalar_one()
        av.publishable = True
    async with tenant_session(ws_id) as s:
        av = (await s.execute(select(AgentVersion).where(AgentVersion.id == av_id))).scalar_one()
        assert av.publishable is True


async def test_frozen_column_update_raises_loudly():
    ws_id = await _new_ws()
    async with tenant_session(ws_id) as s:
        _, av_id = await _seed(s, ws_id)
    with pytest.raises(Exception) as ei:  # 0002 BEFORE UPDATE trigger must RAISE, not silently drop
        async with tenant_session(ws_id) as s:
            av = (await s.execute(select(AgentVersion).where(AgentVersion.id == av_id))).scalar_one()
            av.system_prompt = "tampered"
    assert "immutable" in str(ei.value).lower()
