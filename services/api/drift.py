"""Deterministic drift flagging (feature 006, FR-002).

When a bound tool/MCP-tool/skill/KB's trigger surface changes, the agent versions that bind it are now out of
sync with their frozen snapshot — so the owning agents are flagged `needs_reeval=true` (a signal to
re-snapshot + re-evaluate). Published version snapshots are NEVER mutated (P6); this only sets the draft flag.

The caller invokes this ONLY for a trigger-surface edit (tool auth-mode/scopes; skill instructions/resources/
version/content-hash; KB content/retrieval config) — a cosmetic edit (name/description/display) must not call it.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import Agent, AgentVersion, RetrievalBinding, SkillBinding, ToolBinding

# drift kind → the binding column that references the edited resource
_BINDING = {
    "tool": (ToolBinding.version_id, ToolBinding.tool_id),
    "mcp_tool": (ToolBinding.version_id, ToolBinding.mcp_tool_id),
    "skill": (SkillBinding.version_id, SkillBinding.skill_id),
    "kb": (RetrievalBinding.version_id, RetrievalBinding.kb_id),
}


async def flag_bound_agents(session: AsyncSession, kind: str, resource_id: str | uuid.UUID) -> int:
    """Set `needs_reeval=true` on every agent whose version binds `resource_id`; return the count flagged.

    Scoped to the caller's workspace by RLS (the session is a tenant_session). `kind` selects the binding
    table/column; an unknown kind is a programming error (fail-closed)."""
    if kind not in _BINDING:
        raise ValueError(f"unknown drift kind: {kind}")
    version_col, resource_col = _BINDING[kind]
    try:
        rid = resource_id if isinstance(resource_id, uuid.UUID) else uuid.UUID(str(resource_id))
    except (ValueError, TypeError):
        return 0
    bound_versions = select(version_col).where(resource_col == rid)
    bound_agents = select(AgentVersion.agent_id).where(AgentVersion.id.in_(bound_versions))
    res = await session.execute(
        update(Agent).where(Agent.id.in_(bound_agents)).values(needs_reeval=True)
    )
    return res.rowcount or 0
