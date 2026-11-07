"""US-foundational — deterministic drift flagging (feature 006, FR-002/SC-005).

A bound tool/MCP-tool/skill/KB edit flags ONLY the agents that bind it; a cosmetic edit or an unbound
resource flags none. Integration (real tenant_session + RLS). RED until api/drift.py implements the joins.
"""
from __future__ import annotations

import uuid as _uuid

import pytest
from sqlalchemy import select

from api.db import tenant_session
from api.drift import flag_bound_agents
from api.models import Agent
from tests._api_helpers import (
    seed_agent, seed_skill, seed_skill_binding, seed_tool, seed_tool_binding, seed_version, seed_workspace,
)

pytestmark = pytest.mark.integration


async def _needs_reeval(ws: str, agent_id: str) -> bool:
    async with tenant_session(ws) as s:
        a = (await s.execute(select(Agent).where(Agent.id == _uuid.UUID(agent_id)))).scalar_one()
        return a.needs_reeval


async def test_bound_tool_edit_flags_only_bound_agent():
    ws = await seed_workspace()
    bound = await seed_agent(ws, "Bound")
    bound_v = await seed_version(ws, bound)
    unbound = await seed_agent(ws, "Unbound")
    tool = await seed_tool(ws)
    await seed_tool_binding(ws, bound_v, tool)

    async with tenant_session(ws) as s:
        n = await flag_bound_agents(s, "tool", tool)
    assert n == 1
    assert await _needs_reeval(ws, bound) is True
    assert await _needs_reeval(ws, unbound) is False


async def test_bound_skill_edit_flags_bound_agent():
    ws = await seed_workspace()
    a = await seed_agent(ws)
    v = await seed_version(ws, a)
    skill = await seed_skill(ws)
    await seed_skill_binding(ws, v, skill)

    async with tenant_session(ws) as s:
        n = await flag_bound_agents(s, "skill", skill)
    assert n == 1
    assert await _needs_reeval(ws, a) is True


async def test_unbound_resource_flags_nothing():
    ws = await seed_workspace()
    a = await seed_agent(ws)
    await seed_version(ws, a)
    tool = await seed_tool(ws)                 # created but never bound
    async with tenant_session(ws) as s:
        n = await flag_bound_agents(s, "tool", tool)
    assert n == 0
    assert await _needs_reeval(ws, a) is False


async def test_flag_bound_agents_raises_on_malformed_resource_id():
    """Code-review hardening: a malformed/unknown resource_id or kind is a programming error → fail-closed
    ValueError (not a silent 0). The raise precedes any session use, so a dummy session is fine."""
    with pytest.raises(ValueError):
        await flag_bound_agents(None, "tool", "not-a-uuid")      # bad id → raises at the uuid parse
    with pytest.raises(ValueError):
        await flag_bound_agents(None, "nonsense-kind", "x")      # unknown kind → raises (already)
