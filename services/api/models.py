"""SQLAlchemy 2.0 async ORM + Alembic target (docs/backend/01-data-model.md).

Base + WorkspaceScoped mixin, the core models (Workspace/Agent/AgentVersion), and the
snapshot-complete bindings + KnowledgeBase. `AgentVersion.to_snapshot()` returns the pure
`runtime.snapshot.VersionSnapshot` — the runtime imports only that dataclass, never this module.
Column names/attrs match runtime/hashing.py:compute_snapshot_hash exactly (e.g. model_config_).
"""
from __future__ import annotations

import datetime
import uuid

from sqlalchemy import false as sa_false
from sqlalchemy import (Boolean, CheckConstraint, DateTime, Enum, ForeignKey, ForeignKeyConstraint, Integer, MetaData,
                        Numeric, String, Text, UniqueConstraint, Uuid, func)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column, relationship
from sqlalchemy.types import UserDefinedType

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
_PROVIDER_KEY_STATUS = Enum("active", "expiring", "revoked", name="provider_key_status", create_type=False)  # 0007


def _embedding_provider(model: str) -> str:
    """Provider of a KB's embedding model, from its capability PROFILE (feature 024, analyze M3 — never settings).
    "" for a legacy model with no profile; the runtime then degrades that binding instead of guessing a provider."""
    from runtime.providers.profiles import resolve_embedding_profile_by_model
    try:
        return resolve_embedding_profile_by_model(model).provider
    except FileNotFoundError:
        return ""


def _opt_str(x) -> str | None:
    return str(x) if x is not None else None


class WorkspaceScoped:
    """Every tenant-owned table carries workspace_id (RLS keys on it — 0002/0004, 09 §6).

    The FK + index is provided here (via declared_attr) so subclasses don't redeclare it; the mixin
    also marks a table as tenant-scoped for future RLS-policy automation (0.c). ``Workspace`` is the
    tenant ROOT and deliberately does NOT use this mixin.
    """

    @declared_attr
    def workspace_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(ForeignKey("workspaces.id"), index=True)


# --------------------------------------------------------------------------------------------------
# Core (existing schema 0001)
# --------------------------------------------------------------------------------------------------
class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    name: Mapped[str] = mapped_column(String(80))
    group_prefix: Mapped[str | None] = mapped_column(String(64))
    # TODO(backend): group_prefix is NOT unique, yet the console joins discovered workspaces onto its registry by it
    #   (frontend store.ts mergeDiscovered) — two workspaces sharing a prefix collide there (last wins). Add a partial
    #   UNIQUE index (WHERE group_prefix IS NOT NULL) once the platform-admin workspace-create plane lands.
    # feature 020 (D1): AC role/team → NATIVE IdP group ref (Okta name / Entra GUID / SAML attr); no naming
    # convention assumed (FR-021). Shape: {"roles": {role: ref}, "teams": {team: ref}}. Default {} = no binding.
    idp_binding: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())


class Agent(Base, WorkspaceScoped):
    __tablename__ = "agents"
    # 0015 (feature 025 FR-020): the active version must belong to THIS agent; deferred (circular create in one txn).
    __table_args__ = (
        ForeignKeyConstraint(["active_version_id", "id"], ["agent_versions.id", "agent_versions.agent_id"],
                             name="fk_agents_active_version", deferrable=True, initially="DEFERRED", use_alter=True),
        UniqueConstraint("workspace_id", "id", name="uq_agents_ws_id"),                          # 0016 FK target
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    name: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(_AGENT_STATUS, default="draft")
    active_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    needs_reeval: Mapped[bool] = mapped_column(Boolean, default=False)  # drift flag (feature 006, FR-002)
    access_policy: Mapped[dict] = mapped_column(JSONB, default=lambda: {"visibility": "workspace"})  # feature 013
    # 0016 (feature 026): the editable draft (D1, NULL ⇒ resolved from the active version on read) + archive memory (D4)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    draft: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status_before_archive: Mapped[str | None] = mapped_column(_AGENT_STATUS, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())
    versions: Mapped[list["AgentVersion"]] = relationship(
        back_populates="agent", foreign_keys="AgentVersion.agent_id"
    )


class AgentVersion(Base, WorkspaceScoped):
    __tablename__ = "agent_versions"
    __table_args__ = (UniqueConstraint("id", "agent_id", name="uq_agent_versions_id_agent"),)   # 0015 FK target
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), index=True)
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
    created_by: Mapped[str | None] = mapped_column(Text, nullable=True)   # 0016: principal subject at INSERT (D2)
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
                    # feature 024: derived, NOT hashed (compute_snapshot_hash builds its payload from columns only)
                    embedding_provider=_embedding_provider(b.kb.embedding_model),
                    kb_name=b.kb.name,
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
    __table_args__ = (UniqueConstraint("workspace_id", "id", name="uq_kb_ws_id"),)               # 0015 FK target
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    slug: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    embedding_model: Mapped[str] = mapped_column(Text)
    embedding_dim: Mapped[int] = mapped_column(Integer)
    chunk_config: Mapped[dict] = mapped_column(JSONB, default=dict)
    index_version: Mapped[int] = mapped_column(Integer, default=1)
    access_policy: Mapped[dict] = mapped_column(JSONB, default=lambda: {"visibility": "workspace"})
    archived_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))  # soft-archive (feature 006, FR-003)
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())
    last_ingested_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))  # 0013 (feature 024)
    documents: Mapped[list["KbDocument"]] = relationship(back_populates="kb", passive_deletes=True)


class RetrievalBinding(Base, WorkspaceScoped):
    __tablename__ = "retrieval_bindings"
    __table_args__ = (   # 0015: same-workspace KB (the single-column kb_id FK is kept alongside)
        ForeignKeyConstraint(["workspace_id", "kb_id"], ["knowledge_bases.workspace_id", "knowledge_bases.id"],
                             name="fk_retrieval_bindings_kb_ws", ondelete="RESTRICT"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_versions.id"), index=True)
    kb_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_bases.id"))
    kb_index_version: Mapped[int] = mapped_column(Integer)
    retrieval_config: Mapped[dict] = mapped_column(JSONB)
    injection: Mapped[str] = mapped_column(Text, default="tool")
    kb: Mapped["KnowledgeBase"] = relationship(primaryjoin="RetrievalBinding.kb_id == KnowledgeBase.id",
                                               foreign_keys=[kb_id])   # two FK paths since 0015 — pin the join


# --------------------------------------------------------------------------------------------------
# RAG storage (migration 0013, feature 024 / M7) — data-model.md §2–§4
# --------------------------------------------------------------------------------------------------
class Vector1024(UserDefinedType):
    """pgvector `vector(1024)` without the `pgvector` Python package: bind renders the text literal '[f1,f2,…]' and
    results parse it back. Length is enforced by the column type (and by gateway.embed before it gets here)."""

    cache_ok = True

    def get_col_spec(self, **kw) -> str:
        return "vector(1024)"

    def bind_processor(self, dialect):
        def process(value):
            if value is None:
                return None
            return "[" + ",".join(repr(float(x)) for x in value) + "]"
        return process

    def result_processor(self, dialect, coltype):
        def process(value):
            if value is None or isinstance(value, (list, tuple)):
                return value
            return [float(x) for x in str(value).strip("[]").split(",") if x]
        return process


class KbDocument(Base, WorkspaceScoped):
    __tablename__ = "kb_documents"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    kb_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_bases.id"))
    name: Mapped[str] = mapped_column(Text)
    media_type: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(Text)        # never returned to clients
    storage_key: Mapped[str] = mapped_column(Text)         # never returned to clients
    source_kind: Mapped[str] = mapped_column(Text, default="upload")
    status: Mapped[str] = mapped_column(Text)              # queued | processing | ready | failed | stale
    active_generation: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    dedup_key: Mapped[str | None] = mapped_column(Text)
    injection_flags: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[dict | None] = mapped_column(JSONB)
    created_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    kb: Mapped["KnowledgeBase"] = relationship(back_populates="documents")
    chunks: Mapped[list["KbChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True)
    jobs: Mapped[list["IngestionJob"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True)


class KbChunk(Base, WorkspaceScoped):
    __tablename__ = "kb_chunks"
    __table_args__ = (   # 0015 orphan guard (G9); the dim check is the kb_chunks_dim_check trigger (DB-only)
        ForeignKeyConstraint(["workspace_id", "kb_id"], ["knowledge_bases.workspace_id", "knowledge_bases.id"],
                             name="fk_kb_chunks_kb", ondelete="RESTRICT"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    kb_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("kb_documents.id", ondelete="CASCADE"))
    generation: Mapped[int] = mapped_column(Integer)
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)                # customer-confidential
    token_count: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list[float]] = mapped_column(Vector1024())
    embedding_model: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    injection_categories: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    document: Mapped["KbDocument"] = relationship(back_populates="chunks")


class IngestionJob(Base, WorkspaceScoped):
    __tablename__ = "ingestion_jobs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    kb_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_bases.id"))
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("kb_documents.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text)                # upload | retry | reingest
    status: Mapped[str] = mapped_column(Text)              # queued | processing | ready | failed
    progress_done: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    skipped_unchanged: Mapped[bool] = mapped_column(Boolean, default=False)
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), default=0)
    cost_is_estimate: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[dict | None] = mapped_column(JSONB)
    owner: Mapped[str | None] = mapped_column(Text)
    heartbeat_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    document: Mapped["KbDocument"] = relationship(back_populates="jobs")


class SkillBinding(Base, WorkspaceScoped):
    __tablename__ = "skill_bindings"
    __table_args__ = (   # 0015; immutability = bindings_freeze / bindings_insert_window triggers (DB-only)
        ForeignKeyConstraint(["workspace_id", "skill_id"], ["skills.workspace_id", "skills.id"],
                             name="fk_skill_bindings_skill", ondelete="RESTRICT"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_versions.id"), index=True)
    skill_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # FK: fk_skill_bindings_skill (0015)
    skill_content_hash: Mapped[str] = mapped_column(Text)


class ToolBinding(Base, WorkspaceScoped):
    __tablename__ = "tool_bindings"
    __table_args__ = (   # 0015 (FR-016/017/019); subagent_config_id stays FK-less (no catalog)
        ForeignKeyConstraint(["workspace_id", "tool_id"], ["tools.workspace_id", "tools.id"],
                             name="fk_tool_bindings_tool", ondelete="RESTRICT"),
        ForeignKeyConstraint(["workspace_id", "mcp_tool_id"], ["mcp_tools.workspace_id", "mcp_tools.id"],
                             name="fk_tool_bindings_mcp_tool", ondelete="RESTRICT"),
        CheckConstraint(
            "(kind IN ('http','builtin') AND tool_id IS NOT NULL AND mcp_tool_id IS NULL AND subagent_config_id IS NULL)"
            " OR (kind = 'mcp' AND mcp_tool_id IS NOT NULL AND tool_id IS NULL AND subagent_config_id IS NULL)"
            " OR (kind = 'subagent' AND subagent_config_id IS NOT NULL AND tool_id IS NULL AND mcp_tool_id IS NULL)",
            name="ck_tool_bindings_one_target"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
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


# --------------------------------------------------------------------------------------------------
# M4-remainder registries + admin + runs (migration 0005, feature 006)
# --------------------------------------------------------------------------------------------------
class Tool(Base, WorkspaceScoped):
    """Reusable HTTP tool registry (data.ts RepoTool). http_config is NON-SECRET only (FR-021)."""
    __tablename__ = "tools"
    __table_args__ = (UniqueConstraint("workspace_id", "id", name="uq_tools_ws_id"),)   # 0015 FK target
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(_TOOL_KIND, default="http")
    http_config: Mapped[dict] = mapped_column(JSONB, default=dict)
    risk_tier: Mapped[str] = mapped_column(_TOOL_RISK_TIER, default="read")
    access_policy: Mapped[dict] = mapped_column(JSONB, default=lambda: {"visibility": "workspace"})  # feature 013
    archived_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())


class McpServer(Base, WorkspaceScoped):
    __tablename__ = "mcp_servers"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    name: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text, default="")
    transport: Mapped[str] = mapped_column(Text, default="http")
    status: Mapped[str] = mapped_column(Text, default="disabled")
    access_policy: Mapped[dict] = mapped_column(JSONB, default=lambda: {"visibility": "workspace"})  # feature 013
    archived_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())
    tools: Mapped[list["McpTool"]] = relationship(order_by="McpTool.name")


class McpTool(Base, WorkspaceScoped):
    __tablename__ = "mcp_tools"
    __table_args__ = (UniqueConstraint("workspace_id", "id", name="uq_mcp_tools_ws_id"),)   # 0015 FK target
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    server_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mcp_servers.id"), index=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    risk_tier: Mapped[str] = mapped_column(_TOOL_RISK_TIER, default="read")
    access_policy: Mapped[dict] = mapped_column(JSONB, default=lambda: {"visibility": "workspace"})  # feature 013


class Skill(Base, WorkspaceScoped):
    """Reusable skill package (data.ts Skill). provenance = unsigned stub (FR-004); no signature."""
    __tablename__ = "skills"
    __table_args__ = (UniqueConstraint("workspace_id", "id", name="uq_skills_ws_id"),)   # 0015 FK target
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    slug: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    instructions: Mapped[str] = mapped_column(Text, default="")
    resources: Mapped[list] = mapped_column(JSONB, default=list)
    content_hash: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(Text, default="draft")
    provenance: Mapped[dict | None] = mapped_column(JSONB)
    access_policy: Mapped[dict] = mapped_column(JSONB, default=lambda: {"visibility": "workspace"})  # feature 013
    archived_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())


class WorkspaceSettings(Base, WorkspaceScoped):
    """1:1 with a workspace: operating defaults + limits + eval policy (data.ts WorkspaceSettings/Limits/EvalPolicy)."""
    __tablename__ = "workspace_settings"
    # workspace_id is the PK here (1:1); re-declare to mark primary (the mixin provides the FK+index).
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    default_provider: Mapped[str] = mapped_column(Text, default="")
    default_model: Mapped[str] = mapped_column(Text, default="")
    limits: Mapped[dict] = mapped_column(JSONB, default=dict)
    eval_policy: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Feature 023 (0012): per-workspace opt-in for redacted text in call-log lines; admin-only, audited.
    content_logging: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa_false())


class Member(Base, WorkspaceScoped):
    """Workspace roster entry. console_role is STORED, not enforced in this feature (FR-012)."""
    __tablename__ = "members"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    subject: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text, default="")
    email: Mapped[str] = mapped_column(Text, default="")
    console_role: Mapped[str] = mapped_column(Text, default="viewer")
    teams: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)  # feature 013: server-owned team membership (authZ)
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())


class GroupMember(Base, WorkspaceScoped):
    """Notification fan-out projection (feature 020, D5). CACHE-ONLY — MUST NEVER be read to grant access
    (FR-023); authZ reads the `members` store (console_role/teams). Upserted `source='login_seen'` on login and
    `source='members_of'` on a scheduled enumeration. WorkspaceScoped under RLS; composite pk (re-declare
    workspace_id as primary, like WorkspaceSettings, since the mixin declares it non-primary)."""
    __tablename__ = "group_members"
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    group_ref: Mapped[str] = mapped_column(Text, primary_key=True)
    subject: Mapped[str] = mapped_column(Text, primary_key=True)
    source: Mapped[str] = mapped_column(Text)                                     # 'login_seen' | 'members_of'
    seen_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())


class ProviderKey(Base, WorkspaceScoped):
    """Workspace-scoped provider credential custody record (feature 014; data.ts ProviderKey).

    Holds NO raw secret — only a server-computed `masked` display value, an OPAQUE `vault_path`
    backend reference (an AWS Secrets Manager ARN for `aws`; a `local://…` handle for `local`), and
    metadata. The plaintext lives only in the pluggable secrets backend (api/secrets/). Resolved
    just-in-time at invoke/eval and passed as `RunContext.api_key` (P6: runtime never sees this row).
    """
    __tablename__ = "provider_keys"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    provider: Mapped[str] = mapped_column(Text)            # matches the agent's provider for resolution
    label: Mapped[str] = mapped_column(Text)               # UNIQUE(workspace_id, label)
    masked: Mapped[str] = mapped_column(Text)              # server-computed head/tail; never the raw secret
    vault_path: Mapped[str] = mapped_column(Text)          # OPAQUE backend ref; never returned to a client
    region: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(_PROVIDER_KEY_STATUS, default="active")
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())
    rotated_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))


class ApiToken(Base, WorkspaceScoped):
    """Caller token for the published-invoke plane (feature 018; data-model.md §api_tokens).

    Holds NO raw secret — only a non-reversible `token_hash` (sha256 of the one-time secret) and a short
    display `prefix`. Workspace+agent bound (invoke is pinned to this workspace/endpoint). `scopes.teams` is
    the caller's team source for the per-resource access policy. Revoke is an UPDATE to `revoked_at` (durable,
    checked every call); optional `expires_at` gives a natural lifetime. Immediate revocation (killing an
    active session mid-flight) is the transient Redis deny-set — this row is the authoritative record.
    """
    __tablename__ = "api_tokens"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"))   # one token → one published endpoint
    name: Mapped[str] = mapped_column(Text)
    token_hash: Mapped[str] = mapped_column(Text, unique=True)             # sha256(secret); NEVER the raw secret
    prefix: Mapped[str] = mapped_column(Text)                             # short display prefix, safe to show
    scopes: Mapped[dict] = mapped_column(JSONB, default=dict)             # {"invoke": true, "teams": [...]}
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(Text)   # minting principal's subject (0009) — offboarding sweep


class Run(Base, WorkspaceScoped):
    """Durable run record — identity + metrics ONLY, no raw content (data.ts Run; FR-013/SC-009)."""
    __tablename__ = "runs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    agent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    model: Mapped[str] = mapped_column(Text, default="")
    profile_hash: Mapped[str] = mapped_column(Text, default="")
    prompt_hash: Mapped[str] = mapped_column(Text, default="")
    usage: Mapped[dict] = mapped_column(JSONB, default=dict)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), default=0)
    outcome: Mapped[str] = mapped_column(Text, default="ok")
    source: Mapped[str] = mapped_column(Text, default="playground")
    correlation_id: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())
    # feature 025 (migration 0014): provider retries spent by this run — disclosed, never priced (FR-015)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


# --------------------------------------------------------------------------------------------------
# Feature 026 (migration 0016): persisted evals with a server-held baseline + append-only audit
# Composite (workspace_id, …) FKs mirror the migration — RI bypasses RLS, so the workspace is pinned by the key (025 G8).
# --------------------------------------------------------------------------------------------------
class EvalSuite(Base, WorkspaceScoped):
    __tablename__ = "eval_suites"
    __table_args__ = (
        UniqueConstraint("agent_id", "name", name="uq_eval_suites_agent_name"),
        UniqueConstraint("workspace_id", "id", name="uq_eval_suites_ws_id"),
        ForeignKeyConstraint(["workspace_id", "agent_id"], ["agents.workspace_id", "agents.id"],
                             name="fk_eval_suites_agent", ondelete="RESTRICT"),
        CheckConstraint("char_length(name) BETWEEN 1 AND 80", name="eval_suites_name_check"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    agent_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    cases: Mapped[list["EvalCase"]] = relationship(order_by="EvalCase.created_at", viewonly=True,
                                                   primaryjoin="EvalSuite.id == foreign(EvalCase.suite_id)")


class EvalCase(Base, WorkspaceScoped):
    __tablename__ = "eval_cases"
    __table_args__ = (
        ForeignKeyConstraint(["workspace_id", "suite_id"], ["eval_suites.workspace_id", "eval_suites.id"],
                             name="fk_eval_cases_suite", ondelete="CASCADE"),
        CheckConstraint("jsonb_typeof(rubric) = 'array' AND jsonb_array_length(rubric) BETWEEN 1 AND 8",
                        name="ck_eval_cases_rubric_dims"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    suite_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    input: Mapped[dict] = mapped_column(JSONB)
    rubric: Mapped[list] = mapped_column(JSONB)
    required_fields: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    expected: Mapped[object | None] = mapped_column(JSONB, nullable=True)
    content_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvalRun(Base, WorkspaceScoped):
    """One persisted eval run (never updated after insert). `verdict` is NULL iff `status='failed'`."""
    __tablename__ = "eval_runs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_eval_runs_ws_id"),
        ForeignKeyConstraint(["workspace_id", "agent_id"], ["agents.workspace_id", "agents.id"],
                             name="fk_eval_runs_agent", ondelete="RESTRICT"),
        ForeignKeyConstraint(["workspace_id", "suite_id"], ["eval_suites.workspace_id", "eval_suites.id"],
                             name="fk_eval_runs_suite", ondelete="RESTRICT"),
        ForeignKeyConstraint(["version_id", "agent_id"], ["agent_versions.id", "agent_versions.agent_id"],
                             name="fk_eval_runs_version", ondelete="RESTRICT"),
        ForeignKeyConstraint(["workspace_id", "baseline_run_id"], ["eval_runs.workspace_id", "eval_runs.id"],
                             name="fk_eval_runs_baseline", ondelete="RESTRICT"),
        CheckConstraint("status IN ('completed','failed')", name="eval_runs_status_check"),
        CheckConstraint("verdict IN ('pass','fail','low_confidence')", name="eval_runs_verdict_check"),
        CheckConstraint("baseline_origin IN ('automatic','frozen')", name="eval_runs_baseline_origin_check"),
        CheckConstraint("(status = 'failed') = (verdict IS NULL)", name="ck_eval_runs_verdict_iff_completed"),
        CheckConstraint("(baseline_run_id IS NULL) = (baseline_origin IS NULL)", name="ck_eval_runs_baseline_origin"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    agent_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    suite_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    version_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(Text)
    verdict: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason_code: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    baseline_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    baseline_origin: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    quality_delta: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    cost_delta_pct: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    latency_delta_ms: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    dimensions: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    compared_cases: Mapped[int] = mapped_column(Integer)
    new_cases: Mapped[int] = mapped_column(Integer)
    dropped_cases: Mapped[int] = mapped_column(Integer)
    total_cases: Mapped[int] = mapped_column(Integer)
    pairwise_rate: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    policy: Mapped[dict] = mapped_column(JSONB)
    judge_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvalResult(Base, WorkspaceScoped):
    """Per-case evidence of an eval run. `case_id` is deliberately NOT an FK — evidence outlives a case edit/delete."""
    __tablename__ = "eval_results"
    __table_args__ = (
        UniqueConstraint("eval_run_id", "case_id", name="uq_eval_results_run_case"),
        ForeignKeyConstraint(["workspace_id", "eval_run_id"], ["eval_runs.workspace_id", "eval_runs.id"],
                             name="fk_eval_results_run", ondelete="CASCADE"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    eval_run_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    case_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    case_hash: Mapped[str] = mapped_column(Text)
    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    passed_deterministic: Mapped[bool] = mapped_column(Boolean)
    quality: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    rubric_scores: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    pairwise_winner: Mapped[str | None] = mapped_column(Text, nullable=True)
    judge_low_confidence: Mapped[bool] = mapped_column(Boolean)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    hard_failure: Mapped[bool] = mapped_column(Boolean)
    candidate_output: Mapped[str] = mapped_column(Text)
    cost_usd: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    cost_is_estimate: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa_false())
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    in_comparison: Mapped[bool] = mapped_column(Boolean)


class EvalBaseline(Base, WorkspaceScoped):
    """The FROZEN baseline pointer for a suite (one per suite). Absence ⇒ the automatic rule applies (D7)."""
    __tablename__ = "eval_baselines"
    __table_args__ = (
        ForeignKeyConstraint(["workspace_id", "suite_id"], ["eval_suites.workspace_id", "eval_suites.id"],
                             name="fk_eval_baselines_suite", ondelete="CASCADE"),
        ForeignKeyConstraint(["workspace_id", "eval_run_id"], ["eval_runs.workspace_id", "eval_runs.id"],
                             name="fk_eval_baselines_run", ondelete="RESTRICT"),
    )
    suite_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    eval_run_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    frozen_by: Mapped[str] = mapped_column(Text)
    frozen_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base, WorkspaceScoped):
    """Append-only (ac_app holds SELECT, INSERT only). `meta` holds ids only — write through `api.audit.record`."""
    __tablename__ = "audit_logs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    actor: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    target_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
