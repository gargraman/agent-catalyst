"""Agent / version API contracts (M4, feature 005) — mirror frontend `Agent` + version shapes.

Field constraints are quoted from the ORM (`api/models.py`): `name` ≤ 80 chars (String(80)); `status` is the
`agent_status` enum. `workspace_id` is NEVER accepted in a create/patch body — it comes from the request scope
(FR-007), so a client cannot forge a cross-tenant write.
"""
from __future__ import annotations

import datetime
from typing import Literal

from pydantic import BaseModel, Field

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
    """Partial edit. Requires an `If-Match` header (the prior ETag); status ∈ the agent_status enum."""
    name: str | None = Field(default=None, min_length=1, max_length=80)
    status: AgentStatus | None = None
    active_version_id: str | None = None
    access_policy: dict | None = None                       # feature 013


class VersionOut(BaseModel):
    id: str
    agent_id: str
    label: str
    snapshot_hash: str
    prompt_hash: str
    publishable: bool
    created_at: datetime.datetime


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
    versions: list[VersionOut] = Field(default_factory=list)
