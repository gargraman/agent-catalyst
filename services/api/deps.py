"""Shared FastAPI dependencies for the thin API (M4, feature 005).

The request machinery every route needs: a (dev-stub) session principal, workspace selection + authorization
against the caller's claim (P5/FR-007), a tenant-scoped DB session (ac_app + GUC + RLS via `db.tenant_session`),
a rate-limit seam (F5), and an in-process idempotency in-flight guard (D8). Real OIDC/Vault/Redis are deferred.
Tests override `session_principal` to inject a caller (no real auth in tests).
"""
from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from fastapi import Depends, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import tenant_session
from api.errors import AccessDenied, AuthRequired, IdempotencyInProgress, InputInvalid
from api.permissions import has_permission

# Provider credential for the runtime RunContext. Env-sourced, dev default; invoke is mocked in this slice so
# no real key is used. Vault custody is deferred (P1/M5). Never logged or surfaced (P5).
PROVIDER_API_KEY = os.environ.get("AC_PROVIDER_API_KEY", "sk-dev-unused")


@dataclass
class Principal:
    """Resolved caller. `workspace_claims` maps workspace_id → role (e.g. 'builder'/'publisher')."""
    subject: str
    workspace_claims: dict[str, str] = field(default_factory=dict)


async def session_principal(
    x_dev_principal: str | None = Header(default=None, alias="X-Dev-Principal"),
) -> Principal:
    """Dev-stub principal resolver (real OIDC cookie/CSRF adapter deferred — doc 18). The dev header is a JSON
    object `{"subject": "...", "workspace_claims": {"<ws>": "builder"}}`. Tests override this dependency.
    No principal → 401 auth.required."""
    if not x_dev_principal:
        raise AuthRequired("authentication required")
    try:
        d = json.loads(x_dev_principal)
        return Principal(subject=str(d["subject"]), workspace_claims=dict(d.get("workspace_claims", {})))
    except (ValueError, KeyError, TypeError) as exc:
        raise AuthRequired("invalid session principal") from exc


async def resolve_workspace(
    principal: Principal = Depends(session_principal),
    x_workspace_id: str | None = Header(default=None, alias="X-Workspace-Id"),
    workspace: str | None = Query(default=None),
) -> str:
    """Select the workspace (header or `?workspace=`) and AUTHORIZE it against the caller's claim (FR-007).
    Selecting an unheld workspace → 403 access.denied (never a leak); no selection → 400 input.invalid."""
    ws = x_workspace_id or workspace
    if not ws:
        raise InputInvalid("workspace selection required", detail={"header": "X-Workspace-Id"})
    if ws not in principal.workspace_claims:
        raise AccessDenied("no role claim for the selected workspace", detail={"workspace_id": ws})
    return ws


async def tenant_db(ws: str = Depends(resolve_workspace)) -> AsyncIterator[AsyncSession]:
    """Yield an RLS-scoped session (ac_app + app.workspace_id GUC) for exactly this workspace (P5)."""
    async with tenant_session(ws) as session:
        yield session


def require_permission(permission: str):
    """RBAC gate (feature 008, M5/P3). Returns a dependency that authorizes the caller's workspace ROLE
    against `permission` — deny fail-closed with 403 access.denied. Resolves principal + workspace (header-only,
    no DB), so denial raises BEFORE the handler loads any resource (403 before 404, no existence leak — FR-015).
    Composes with, never replaces, membership (resolve_workspace) + RLS (tenant_db). The returned callable
    carries `required_permission` for the deny-by-default coverage test (FR-014)."""
    async def _check(
        principal: Principal = Depends(session_principal),
        ws: str = Depends(resolve_workspace),
    ) -> None:
        role = principal.workspace_claims.get(ws, "")
        if not has_permission(role, permission):
            raise AccessDenied("insufficient role for this operation",
                               detail={"required_permission": permission, "role": role})
        return None

    _check.required_permission = permission  # type: ignore[attr-defined]  # introspected by the coverage test
    return _check


async def rate_limit() -> None:
    """Rate-limit seam (F5). No-op allow in this slice; thresholds (429 rate.limited) are deferred (M6)."""
    return None


# ---- idempotency in-flight guard (D8) ------------------------------------------------------------
_INFLIGHT: set[tuple[str, str]] = set()


@asynccontextmanager
async def idempotency_guard(workspace_id: str, key: str | None):
    """Reject a concurrent retry of an in-flight create (same workspace + Idempotency-Key) with
    409 idempotency.in_progress (D8). Durable replay of a COMPLETED key is deferred (Redis store)."""
    if not key:
        yield
        return
    k = (workspace_id, key)
    if k in _INFLIGHT:
        raise IdempotencyInProgress("a request with this Idempotency-Key is still in flight",
                                    detail={"idempotency_key": key})
    _INFLIGHT.add(k)
    try:
        yield
    finally:
        _INFLIGHT.discard(k)
