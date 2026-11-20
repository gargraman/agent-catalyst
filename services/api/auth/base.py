"""Pluggable IdP authentication seam (feature 012).

The vendor-agnostic contract every call site uses. A concrete provider verifies an inbound credential and
returns a resolved `Principal`, or raises `AuthError` (→ 401). Adding/switching an IdP vendor is a new
provider implementation + config — no call-site, RBAC, or tenancy change (FR-005). API-layer only (P6).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from starlette.requests import Request

from api.errors import AuthRequired

if TYPE_CHECKING:
    from api.deps import Principal


class AuthError(AuthRequired):
    """Authentication failure → 401 `auth.required`. Carries only a non-sensitive reason — never the raw
    token or any secret (FR-011)."""


@runtime_checkable
class IdPProvider(Protocol):
    """A pluggable identity provider. `verify` MUST raise `AuthError` on any failure (never a partial or
    default Principal, FR-010) and MUST NOT log/echo the raw token (FR-011)."""
    id: str

    async def verify(self, request: Request) -> "Principal": ...


@runtime_checkable
class GroupEnumerating(Protocol):
    """OPTIONAL provider capability (feature 020, D2) for the login-time group→members-store sync.

    `groups_for` returns the subject's NATIVE IdP group refs at login; `members_of` enumerates a group's members
    for the scheduled projection refresh. Both are **capability-detected** (`hasattr`/`isinstance`) — the base
    providers (`dev`/`trellix-iam`/`jwt`) do NOT implement this and keep only `verify`, so the seam grows without
    touching them. Returning **`None`** means "this IdP can't tell us" — distinct from `[]` ("no groups"); the
    sync treats `None` as a no-op that NEVER revokes (D3), while an empty/partial authoritative list may revoke
    a prior synced role (D4). Implementations MUST NOT log/echo tokens or secrets (FR-011)."""

    async def groups_for(self, subject: str) -> list[str] | None: ...

    async def members_of(self, group_ref: str) -> list[str] | None: ...
