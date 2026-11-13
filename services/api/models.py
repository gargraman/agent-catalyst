"""SQLAlchemy 2.0 async ORM + Alembic target (docs/backend/01-data-model.md).

Base + WorkspaceScoped mixin, the core models (Workspace/Agent/AgentVersion), and the
snapshot-complete bindings + KnowledgeBase. `AgentVersion.to_snapshot()` returns the pure
`runtime.snapshot.VersionSnapshot` — the runtime imports only that dataclass, never this module.
Column names/attrs match runtime/hashing.py:compute_snapshot_hash exactly (e.g. model_config_).
"""
from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, MetaData, Numeric, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column, relationship

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
    # feature 020 (D1): AC role/team → NATIVE IdP group ref (Okta name / Entra GUID / SAML attr); no naming
    # convention assumed (FR-021). Shape: {"roles": {role: ref}, "teams": {team: ref}}. Default {} = no binding.
    idp_binding: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())


class Agent(Base, WorkspaceScoped):
    __tablename__ = "agents"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    name: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(_AGENT_STATUS, default="draft")
    active_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    needs_reeval: Mapped[bool] = mapped_column(Boolean, default=False)  # drift flag (feature 006, FR-002)
    access_policy: Mapped[dict] = mapped_column(JSONB, default=lambda: {"visibility": "workspace"})  # feature 013
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())
    versions: Mapped[list["AgentVersion"]] = relationship(
        back_populates="agent", foreign_keys="AgentVersion.agent_id"
    )


class AgentVersion(Base, WorkspaceScoped):
    __tablename__ = "agent_versions"
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
    slug: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    embedding_model: Mapped[str] = mapped_column(Text)
    embedding_dim: Mapped[int] = mapped_column(Integer)
    chunk_config: Mapped[dict] = mapped_column(JSONB, default=dict)
    index_version: Mapped[int] = mapped_column(Integer, default=1)
    access_policy: Mapped[dict] = mapped_column(JSONB, default=lambda: {"visibility": "workspace"})
    archived_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))  # soft-archive (feature 006, FR-003)
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())


class RetrievalBinding(Base, WorkspaceScoped):
    __tablename__ = "retrieval_bindings"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_versions.id"), index=True)
    kb_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_bases.id"))
    kb_index_version: Mapped[int] = mapped_column(Integer)
    retrieval_config: Mapped[dict] = mapped_column(JSONB)
    injection: Mapped[str] = mapped_column(Text, default="tool")
    kb: Mapped["KnowledgeBase"] = relationship()


class SkillBinding(Base, WorkspaceScoped):
    __tablename__ = "skill_bindings"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_versions.id"), index=True)
    skill_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # registry deferred: no FK yet
    skill_content_hash: Mapped[str] = mapped_column(Text)


class ToolBinding(Base, WorkspaceScoped):
    __tablename__ = "tool_bindings"
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
