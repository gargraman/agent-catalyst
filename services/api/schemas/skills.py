"""Pydantic contracts for the skills catalog (feature 006, US1) — mirror data.ts Skill/SkillResource.

Publication records an **unsigned provenance stub** (who/when); no cryptographic signature (FR-004).
"""
from __future__ import annotations

import datetime

from pydantic import BaseModel


class SkillCreate(BaseModel):
    slug: str
    name: str
    description: str = ""
    instructions: str = ""
    resources: list[dict] = []


class SkillPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    instructions: str | None = None
    resources: list[dict] | None = None


class SkillOut(BaseModel):
    id: str
    slug: str
    name: str
    description: str
    instructions: str
    resources: list[dict]
    content_hash: str
    status: str
    provenance: dict | None
    archived: bool
    created_at: datetime.datetime
