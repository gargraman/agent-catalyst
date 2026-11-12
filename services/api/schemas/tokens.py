"""Caller-token API contracts (feature 018) — mint/list/revoke-user bodies.

The mint response is the ONLY place the raw secret is ever returned (shown exactly once, FR-010); every other
shape carries prefix-only. Mirrors `frontend/src/lib/data.ts` Token + the `08 §173-175, §220` surface.
"""
from __future__ import annotations

import datetime

from pydantic import BaseModel


class TokenMintIn(BaseModel):
    name: str
    expires_at: datetime.datetime | None = None          # optional lifetime (FR-015)
    teams: list[str] | None = None                       # caller teams → scopes.teams (access-policy source)


class TokenMintOut(BaseModel):
    """Mint result — carries the FULL secret exactly once (FR-010). Never returned again."""
    id: str
    prefix: str
    secret: str
    agent_id: str
    name: str
    created_at: datetime.datetime
    expires_at: datetime.datetime | None = None


class TokenOut(BaseModel):
    """Masked roster entry — prefix only, never the secret (FR-011)."""
    id: str
    prefix: str
    name: str
    agent_id: str
    created_at: datetime.datetime
    last_used_at: datetime.datetime | None = None
    expires_at: datetime.datetime | None = None
    revoked_at: datetime.datetime | None = None


class RevokeUserIn(BaseModel):
    """Admin immediate-revocation target: a console `subject` and/or a caller `token_id` (at least one)."""
    subject: str | None = None
    token_id: str | None = None
