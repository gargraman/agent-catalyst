"""US2 — AgentVersion.to_snapshot() produces the frozen, reproducible VersionSnapshot
(feature 001, spec FR-006..009, SC-003/004).

RED until to_snapshot assembles bindings + reuses compute_snapshot_hash. Integration (real DB via
ac_app tenant_session); skips when DB unreachable. Uses fixed registry ids so clones hash equal.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from api.db import tenant_session
from api.models import (
    Agent,
    AgentVersion,
    KnowledgeBase,
    RetrievalBinding,
    SkillBinding,
    ToolBinding,
    Workspace,
)
from runtime.hashing import compute_snapshot_hash

pytestmark = pytest.mark.integration

# Fixed registry identities so two "clone" versions share behavior-defining content → equal hash.
_SKILL_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
_TOOL_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")

_MODEL_CONFIG = {
    "model": "claude-haiku-4-5",
    "provider": "anthropic",
    "api_version": "2026-01-01",
    "params": {"temperature": 0.2, "max_tokens": 1024},
}
_PROFILE = {"features": {"structured_output": True}, "fallback": {}}


async def _seed_version(session, ws_id, kb_id, *, prompt="You are a helpful assistant.", label="v1"):
    agent = Agent(workspace_id=ws_id, name=f"agent-{uuid.uuid4().hex[:6]}", status="draft")
    session.add(agent)
    await session.flush()
    av = AgentVersion(
        workspace_id=ws_id,
        agent_id=agent.id,
        label=label,
        system_prompt=prompt,
        prompt_hash="ph-fixed",
        snapshot_hash="seed",  # set at INSERT; never updated (0002 trigger freezes it)
        model_config_=_MODEL_CONFIG,
        guardrail_config={"input": [], "output": []},
        io_contract={"output_format": "text"},
        memory_config={"session": True, "window": 10},
        context_config={"limit_tokens": 8000},
        profile_snapshot=_PROFILE,
    )
    session.add(av)
    await session.flush()
    session.add_all(
        [
            ToolBinding(
                workspace_id=ws_id, version_id=av.id, kind="http", tool_id=_TOOL_ID,
                schema_snapshot={"type": "object"}, config_snapshot={"url": "https://x.example"},
                risk_tier="read", requires_approval=False,
            ),
            SkillBinding(
                workspace_id=ws_id, version_id=av.id, skill_id=_SKILL_ID, skill_content_hash="sch-aaaa",
            ),
            RetrievalBinding(
                workspace_id=ws_id, version_id=av.id, kb_id=kb_id, kb_index_version=3,
                retrieval_config={"top_k": 5, "similarity_threshold": 0.2}, injection="tool",
            ),
        ]
    )
    await session.flush()
    return av.id


async def _load(session, av_id):
    res = await session.execute(
        select(AgentVersion)
        .options(
            selectinload(AgentVersion.retrieval_bindings).selectinload(RetrievalBinding.kb),
            selectinload(AgentVersion.tool_bindings),
            selectinload(AgentVersion.skill_bindings),
        )
        .where(AgentVersion.id == av_id)
    )
    return res.scalar_one()


async def _ws_and_kb():
    async with tenant_session(uuid.uuid4()) as s:
        ws = Workspace(name=f"ws-{uuid.uuid4().hex[:8]}")
        s.add(ws)
        await s.flush()
        ws_id = ws.id
    async with tenant_session(ws_id) as s:
        kb = KnowledgeBase(
            workspace_id=ws_id, slug=f"kb-{uuid.uuid4().hex[:6]}", name="KB",
            embedding_model="text-embedding-3-small", embedding_dim=1536,
        )
        s.add(kb)
        await s.flush()
        kb_id = kb.id
    return ws_id, kb_id


async def test_snapshot_hash_wires_compute_and_covers_fields():
    ws_id, kb_id = await _ws_and_kb()
    async with tenant_session(ws_id) as s:
        av_id = await _seed_version(s, ws_id, kb_id)
    async with tenant_session(ws_id) as s:
        av = await _load(s, av_id)
        expected = compute_snapshot_hash(av)
        snap = av.to_snapshot()
    assert snap.snapshot_hash == expected, "to_snapshot must reuse compute_snapshot_hash"
    assert snap.system_prompt == "You are a helpful assistant."
    assert snap.model_config == _MODEL_CONFIG
    assert len(snap.tools) == 1 and len(snap.skills) == 1 and len(snap.retrieval) == 1
    assert snap.retrieval[0].embedding_model == "text-embedding-3-small"


async def test_snapshot_hash_is_clone_stable():
    ws_id, kb_id = await _ws_and_kb()
    async with tenant_session(ws_id) as s:
        a_id = await _seed_version(s, ws_id, kb_id, label="v1")
        b_id = await _seed_version(s, ws_id, kb_id, label="v1")  # identical content, different rows
    async with tenant_session(ws_id) as s:
        a = await _load(s, a_id)
        b = await _load(s, b_id)
        assert a.to_snapshot().snapshot_hash == b.to_snapshot().snapshot_hash


async def test_kb_index_version_excluded_from_hash():
    ws_id, kb_id = await _ws_and_kb()
    async with tenant_session(ws_id) as s:
        av_id = await _seed_version(s, ws_id, kb_id)
    async with tenant_session(ws_id) as s:
        av = await _load(s, av_id)
        h1 = av.to_snapshot().snapshot_hash
    async with tenant_session(ws_id) as s:
        rb = (await s.execute(select(RetrievalBinding).where(RetrievalBinding.version_id == av_id))).scalar_one()
        rb.kb_index_version = 999  # bindings aren't frozen; only agent_versions are
    async with tenant_session(ws_id) as s:
        av = await _load(s, av_id)
        h2 = av.to_snapshot().snapshot_hash
    assert h1 == h2, "kb_index_version must not affect snapshot_hash (04 §9)"
