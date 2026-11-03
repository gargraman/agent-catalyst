"""SQLAlchemy 2.0 async ORM + Alembic target (docs/backend/01-data-model.md).

Base + WorkspaceScoped mixin, the core models (Workspace/Agent/AgentVersion), and the
snapshot-complete bindings + KnowledgeBase. `AgentVersion.to_snapshot()` returns the pure
`runtime.snapshot.VersionSnapshot` — the runtime imports only that dataclass, never this module.
Column names/attrs match runtime/hashing.py:compute_snapshot_hash exactly (e.g. model_config_).
"""
from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, MetaData, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from runtime.hashing import compute_snapshot_hash
from runtime.snapshot import RetrievalRef, SkillRef, ToolRef, VersionSnapshot

_NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=_NAMING)


# PG-native enums already created by migrations (0001/0004) — bind to them, never re-create.
_AGENT_STATUS = Enum("draft", "published", "archived", "eval_failing", name="agent_status", create_type=False)
_TOOL_KIND = Enum("http", "builtin", "subagent", "mcp", name="tool_kind", create_type=False)
_TOOL_RISK_TIER = Enum("read", "write", "destructive", name="tool_risk_tier", create_type=False)


def _opt_str(x) -> str | None:
    return str(x) if x is not None else None


class WorkspaceScoped:
    """Every tenant-owned table carries workspace_id (RLS keys on it — 0002/0004, 09 §6)."""

    workspace_id: Mapped[uuid.UUID] = mapped_column(index=True)


# --------------------------------------------------------------------------------------------------
# Core (existing schema 0001)
# --------------------------------------------------------------------------------------------------
class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    name: Mapped[str] = mapped_column(String(80))
    group_prefix: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())


class Agent(Base, WorkspaceScoped):
    __tablename__ = "agents"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(_AGENT_STATUS, default="draft")
    active_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())
    versions: Mapped[list["AgentVersion"]] = relationship(
        back_populates="agent", foreign_keys="AgentVersion.agent_id"
    )


class AgentVersion(Base, WorkspaceScoped):
    __tablename__ = "agent_versions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    label: Mapped[str] = mapped_column(String(40))
    system_prompt: Mapped[str] = mapped_column(Text)
    prompt_hash: Mapped[str] = mapped_column(Text)
    snapshot_hash: Mapped[str] = mapped_column(Text)
    # `model_config` collides with Pydantic/SQLAlchemy internals → attr model_config_ (01:895, hashing.py).
    model_config_: Mapped[dict] = mapped_column("model_config", JSONB)
    guardrail_config: Mapped[dict] = mapped_column(JSONB, default=dict)
    io_contract: Mapped[dict] = mapped_column(JSONB, default=dict)
    memory_config: Mapped[dict] = mapped_column(JSONB, default=dict)
    context_config: Mapped[dict] = mapped_column(JSONB, default=dict)
    profile_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    publishable: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())

    agent: Mapped["Agent"] = relationship(back_populates="versions", foreign_keys=[agent_id])
    retrieval_bindings: Mapped[list["RetrievalBinding"]] = relationship()
    tool_bindings: Mapped[list["ToolBinding"]] = relationship(
        order_by="(ToolBinding.kind, ToolBinding.tool_id, ToolBinding.mcp_tool_id, ToolBinding.subagent_config_id)"
    )
    skill_bindings: Mapped[list["SkillBinding"]] = relationship()

    def to_snapshot(self) -> VersionSnapshot:
        """Materialize the frozen, runtime-ready snapshot (00 §4). Pure read; no live agent row.

        Content identity reuses runtime/hashing.py:compute_snapshot_hash (clone-stable;
        kb_index_version excluded). Bindings must be loaded before calling (no lazy I/O escapes);
        sorted to mirror the hash's stable ordering so the assembled snapshot is reproducible.
        """
        return VersionSnapshot(
            version_id=str(self.id),
            agent_id=str(self.agent_id),
            workspace_id=str(self.workspace_id),
            label=self.label,
            snapshot_hash=compute_snapshot_hash(self),
            model_config=self.model_config_,
            profile_snapshot=self.profile_snapshot,
            system_prompt=self.system_prompt,
            prompt_hash=self.prompt_hash,
            io_contract=self.io_contract,
            memory_config=self.memory_config,
            context_config=self.context_config,
            guardrail_config=self.guardrail_config,
            retrieval=tuple(
                RetrievalRef(
                    kb_id=str(b.kb_id),
                    embedding_model=b.kb.embedding_model,
                    retrieval_config=b.retrieval_config,
                    injection=b.injection,
                    kb_index_version=b.kb_index_version,
                )
                for b in sorted(self.retrieval_bindings, key=lambda b: str(b.kb_id))
            ),
            tools=tuple(
                ToolRef(
                    kind=b.kind,
                    schema_snapshot=b.schema_snapshot,
                    config_snapshot=b.config_snapshot,
                    risk_tier=b.risk_tier,
                    requires_approval=b.requires_approval,
                    subagent_config_id=_opt_str(b.subagent_config_id),
                    auth_mode=b.auth_mode,
                    mcp_scopes=tuple(b.mcp_scopes or ()),
                    tool_id=_opt_str(b.tool_id),
                    mcp_tool_id=_opt_str(b.mcp_tool_id),
                )
                for b in sorted(
                    self.tool_bindings,
                    key=lambda b: (b.kind, str(b.tool_id or b.mcp_tool_id or b.subagent_config_id)),
                )
            ),
            skills=tuple(
                SkillRef(skill_id=str(b.skill_id), skill_content_hash=b.skill_content_hash)
                for b in sorted(self.skill_bindings, key=lambda b: str(b.skill_id))
            ),
        )


# --------------------------------------------------------------------------------------------------
# Snapshot-complete additions (migration 0004)
# --------------------------------------------------------------------------------------------------
class KnowledgeBase(Base, WorkspaceScoped):
    __tablename__ = "knowledge_bases"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    slug: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    embedding_model: Mapped[str] = mapped_column(Text)
    embedding_dim: Mapped[int] = mapped_column(Integer)
    chunk_config: Mapped[dict] = mapped_column(JSONB, default=dict)
    index_version: Mapped[int] = mapped_column(Integer, default=1)
    access_policy: Mapped[dict] = mapped_column(JSONB, default=lambda: {"visibility": "workspace"})
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())


class RetrievalBinding(Base, WorkspaceScoped):
    __tablename__ = "retrieval_bindings"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_versions.id"), index=True)
    kb_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_bases.id"))
    kb_index_version: Mapped[int] = mapped_column(Integer)
    retrieval_config: Mapped[dict] = mapped_column(JSONB)
    injection: Mapped[str] = mapped_column(Text, default="tool")
    kb: Mapped["KnowledgeBase"] = relationship()


class SkillBinding(Base, WorkspaceScoped):
    __tablename__ = "skill_bindings"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_versions.id"), index=True)
    skill_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # registry deferred: no FK yet
    skill_content_hash: Mapped[str] = mapped_column(Text)


class ToolBinding(Base, WorkspaceScoped):
    __tablename__ = "tool_bindings"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), index=True)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_versions.id"), index=True)
    kind: Mapped[str] = mapped_column(_TOOL_KIND)
    tool_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    mcp_tool_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    subagent_config_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    schema_snapshot: Mapped[dict] = mapped_column(JSONB)
    config_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    risk_tier: Mapped[str] = mapped_column(_TOOL_RISK_TIER, default="read")
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    auth_mode: Mapped[str | None] = mapped_column(Text)
    mcp_scopes: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
