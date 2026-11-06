"""Pydantic contracts for the member roster (feature 006, US4) — mirror data.ts Member.

console_role is STORED, not enforced across endpoints in this feature (FR-012). No invite/email flow, no IdP sync.
"""
from __future__ import annotations

from pydantic import BaseModel


class MemberCreate(BaseModel):
    subject: str
    display_name: str = ""
    email: str = ""
    console_role: str = "viewer"


class MemberPatch(BaseModel):
    display_name: str | None = None
    email: str | None = None
    console_role: str | None = None


class MemberOut(BaseModel):
    id: str
    subject: str
    display_name: str
    email: str
    console_role: str
