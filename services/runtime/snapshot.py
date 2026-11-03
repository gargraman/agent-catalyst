"""VersionSnapshot — the frozen, runtime-ready version config (00 §4, 01 §4.x).

Pure value type: `services/runtime/*` imports this and nothing DB/ORM/HTTP-related, so the runtime
stays a library the eval worker and API can share. Built by `AgentVersion.to_snapshot()`
(services/api/models.py). Content identity (`snapshot_hash`) comes from runtime/hashing.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolRef:
    kind: str
    schema_snapshot: dict
    config_snapshot: dict
    risk_tier: str
    requires_approval: bool
    subagent_config_id: str | None
    auth_mode: str | None
    mcp_scopes: tuple[str, ...]
    tool_id: str | None
    mcp_tool_id: str | None


@dataclass(frozen=True)
class RetrievalRef:
    kb_id: str
    embedding_model: str
    retrieval_config: dict
    injection: str
    kb_index_version: int  # carried for display/staleness; EXCLUDED from snapshot_hash (04 §9)


@dataclass(frozen=True)
class SkillRef:
    skill_id: str
    skill_content_hash: str


@dataclass(frozen=True)
class VersionSnapshot:
    """Immutable, fully-resolved config the runtime executes. Assembled from a frozen AgentVersion
    row + its bindings; never from the live agent row."""

    version_id: str
    agent_id: str
    workspace_id: str
    label: str
    snapshot_hash: str
    # model / capability
    model_config: dict
    profile_snapshot: dict
    # behavior
    system_prompt: str
    prompt_hash: str
    io_contract: dict
    memory_config: dict
    context_config: dict
    guardrail_config: dict
    # bindings (immutable tuples)
    retrieval: tuple[RetrievalRef, ...] = field(default_factory=tuple)
    tools: tuple[ToolRef, ...] = field(default_factory=tuple)
    skills: tuple[SkillRef, ...] = field(default_factory=tuple)
