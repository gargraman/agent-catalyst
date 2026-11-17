"""Agent / version API contracts (M4, feature 005) — mirror frontend `Agent` + version shapes.

Field constraints are quoted from the ORM (`api/models.py`): `name` ≤ 80 chars (String(80)); `status` is the
`agent_status` enum. `workspace_id` is NEVER accepted in a create/patch body — it comes from the request scope
(FR-007), so a client cannot forge a cross-tenant write.
"""
from __future__ import annotations

import datetime
from typing import Literal

from pydantic import BaseModel, Field

from api.drafts import Draft
from api.schemas.access_policy import AccessPolicy

AgentStatus = Literal["draft", "published", "archived", "eval_failing"]


class AgentCreate(BaseModel):
    """Create a draft agent + its first version fields (workspace comes from the request scope)."""
    name: str = Field(min_length=1, max_length=80)          # ORM agents.name String(80)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    system_prompt: str = ""
    io_contract: dict = Field(default_factory=dict)         # MUST contain an "input_schema" object (validated in the router)
    access_policy: dict | None = None                       # feature 013; validated in the router → input.invalid


class AgentPatch(BaseModel):
    """Partial edit. Requires an `If-Match` header (the prior ETag); status ∈ the agent_status enum.
    Feature 026: `description` + `draft` (FULL replacement, validated → 422) ride the same xmin ETag. Archive is
    `status:"archived"`; restore is `status:<restore_status>`; any other transition out of `archived` → 400."""
    name: str | None = Field(default=None, min_length=1, max_length=80)
    status: AgentStatus | None = None
    active_version_id: str | None = None
    access_policy: dict | None = None                       # feature 013
    description: str | None = Field(default=None, max_length=2000)   # feature 026
    draft: Draft | None = None                                        # feature 026 (D1)


class DraftOut(BaseModel):
    """The resolved draft as READ (feature 026). Deliberately unvalidated: a stored draft may reference a KB deleted
    since, or a profile retired since — reading must still work; validation happens at save / version create."""
    provider: str | None = None
    model: str | None = None
    model_params: dict = Field(default_factory=dict)
    system_prompt: str = ""
    io_contract: dict = Field(default_factory=dict)
    kb_ids: list[str] = Field(default_factory=list)
    tool_ids: list[str] = Field(default_factory=list)
    skill_ids: list[str] = Field(default_factory=list)
    # P5 / 024 H2: bound KB/tool/skill ids the viewer may not USE are omitted from the lists above and only counted
    # here. A draft save (PATCH) re-merges them server-side, so a caller can never strip a binding they cannot see.
    restricted_bindings: int = 0


class RetrievalBindingOut(BaseModel):
    """Feature 024. A binding the viewer may not USE renders masked: no KB id/name/model/version (analyze H2)."""
    kb_id: str | None
    kb_name: str | None
    injection: str
    retrieval_config: dict
    embedding_model: str | None
    kb_index_version: int | None
    restricted: bool


class VersionCreate(BaseModel):
    """Feature 024 (research D12). Optional body of POST /agents/{id}/versions. `retrieval_bindings` omitted (or
    null) ⇒ the latest version's bindings are COPIED (G2); `[]` ⇒ none. Items are validated in the router so a bad
    binding is a 400 input.invalid naming the field (not a framework 422)."""
    label: str | None = None
    retrieval_bindings: list[dict] | None = None


class VersionOut(BaseModel):
    id: str
    agent_id: str
    label: str
    snapshot_hash: str
    prompt_hash: str
    publishable: bool
    created_at: datetime.datetime
    retrieval_bindings: list[RetrievalBindingOut] = Field(default_factory=list)   # feature 024
    created_by: str | None = None          # feature 026 (D2): subject at create; NULL for pre-0016 versions
    is_active: bool = False                # feature 026: == agent.active_version_id
    # 026 review-2 #8: the version's OWN input contract — the console sends a version/active run under that
    # version's field name, not the draft's (which may have been renamed since).
    io_contract: dict = Field(default_factory=dict)


# TODO(backend/contract): owner, tags and 30-day metrics are still console placeholders (feature 011 F-A);
#   description/provider/model/draft landed in feature 026. Metrics belong to a separate runs rollup endpoint.
class AgentOut(BaseModel):
    """Mirrors FE `Agent`. The row ETag (Postgres xmin, D7) is surfaced via the `ETag` response header,
    not this body."""
    id: str
    name: str
    status: AgentStatus
    active_version_id: str | None = None
    workspace_id: str
    created_at: datetime.datetime
    access_policy: AccessPolicy
    versions: list[VersionOut] = Field(default_factory=list)      # newest first (feature 026)
    # feature 026 (D1/D4)
    description: str = ""
    provider: str | None = None            # from the resolved draft
    model: str | None = None
    draft: DraftOut | None = None          # stored draft, else the active version's config
    draft_dirty: bool = False              # normalized(draft) != normalized(active version)
    restore_status: AgentStatus | None = None   # set while archived: the status a restore puts back
