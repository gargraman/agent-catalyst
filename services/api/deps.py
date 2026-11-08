"""Shared FastAPI dependencies for the thin API (M4, feature 005).

The request machinery every route needs: a (dev-stub) session principal, workspace selection + authorization
against the caller's claim (P5/FR-007), a tenant-scoped DB session (ac_app + GUC + RLS via `db.tenant_session`),
a rate-limit seam (F5), and an in-process idempotency in-flight guard (D8). Real OIDC/Vault/Redis are deferred.
Tests override `session_principal` to inject a caller (no real auth in tests).
"""
from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from fastapi import Depends, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from api import membership, spend
from api.db import tenant_session
from api.errors import AccessDenied, AppError, BudgetExceeded, IdempotencyInProgress, InputInvalid
from api.permissions import has_permission

_log = logging.getLogger("api.spend")

# Provider credential for the runtime RunContext. Env-sourced, dev default; invoke is mocked in this slice so
# no real key is used. Vault custody is deferred (P1/M5). Never logged or surfaced (P5).
PROVIDER_API_KEY = os.environ.get("AC_PROVIDER_API_KEY", "sk-dev-unused")


@dataclass
class Principal:
    """Resolved caller. `workspace_claims` maps workspace_id → role (e.g. 'builder'/'publisher'). For a real
    (JWT) provider the role is NOT in the token — it is filled per-request by `resolve_workspace` from the
    membership store (feature 012). `scopes`/`issuer` carry the verified token's claims (audit/future use)."""
    subject: str
    workspace_claims: dict[str, str] = field(default_factory=dict)
    scopes: tuple[str, ...] = ()
    issuer: str | None = None


async def session_principal(request: Request) -> Principal:
    """Authenticate the caller via the ACTIVE IdP provider (feature 012). The provider is selected by config
    (`AC_AUTH_PROVIDER`): `trellix-iam` verifies a real bearer JWT; `dev` trusts the `X-Dev-Principal` header
    (test/dev only, disabled under `AC_ENV=prod`). Any auth failure → 401 (fail-closed, FR-010). The provider
    import is lazy to avoid an import cycle (deps ← auth providers)."""
    from api.auth.registry import get_active_provider
    from api.settings import get_settings

    provider = get_active_provider(get_settings())
    return await provider.verify(request)


async def _membership_role(ws: str, subject: str) -> str | None:
    """Resolve the caller's AC role for `ws` from the membership store, RLS-scoped to `ws` (feature 012)."""
    async with tenant_session(ws) as s:
        return await membership.role_for(s, subject)


async def resolve_workspace(
    principal: Principal = Depends(session_principal),
    x_workspace_id: str | None = Header(default=None, alias="X-Workspace-Id"),
    workspace: str | None = Query(default=None),
) -> str:
    """Select the target workspace (`X-Workspace-Id` or `?workspace=`) and AUTHORIZE the caller for it (P5).
    If the principal already carries a role claim for the workspace (dev provider), use it; otherwise (real
    provider — the token has no AC claim) resolve the role from the membership store for that workspace
    (feature 012, FR-013/FR-014). No membership → 403 access.denied (fail-closed, no existence leak); no
    selection → 400 input.invalid."""
    ws = x_workspace_id or workspace
    if not ws:
        raise InputInvalid("workspace selection required", detail={"header": "X-Workspace-Id"})
    if ws not in principal.workspace_claims:
        role = await _membership_role(ws, principal.subject)
        if role is None:
            raise AccessDenied("no membership for the selected workspace", detail={"workspace_id": ws})
        principal.workspace_claims[ws] = role
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


async def enforce_spend_cap(
    ws: str = Depends(resolve_workspace),
    session: AsyncSession = Depends(tenant_db),
) -> None:
    """Durable monthly spend cap (feature 009, M6 / F7, P3). Refuse a paid invocation when the workspace's
    month-to-date spend (summed from the durable `runs.cost_usd` ledger — eviction-proof) is at/over its
    monthly cap, BEFORE the provider is called / the stream opens. Attached AFTER `require_permission(...)`
    so authz (403) resolves first (FR-006); shares the request's RLS session with the handler (no second
    connection). A refusal writes no Run row — it is surfaced via the 402 + this log line (FR-011). A
    spend-computation error is NOT swallowed: it propagates, failing the request closed before any paid
    call (FR-009)."""
    try:
        spent = await spend.month_to_date_spend(session, ws)
        cap = await spend.active_cap_usd(session, ws)
    except AppError:
        raise                                               # a real downstream AppError surfaces as-is
    except Exception as e:                                   # noqa: BLE001 — cannot verify spend → fail closed
        _log.warning("spend cap indeterminate: workspace=%s err=%s", ws, e)
        # FR-009: refuse (fail-closed) with a controlled 500. We deliberately do NOT claim budget.exceeded —
        # the budget may be nowhere near spent; this is an internal verification failure.
        raise AppError("could not verify spend budget; request refused (fail-closed)",
                       detail={"reason": "spend_indeterminate"})
    if spent >= cap:
        _log.warning("spend cap reached: workspace=%s month_to_date_usd=%s cap_usd=%s", ws, spent, cap)
        raise BudgetExceeded("monthly spend cap reached",
                             detail={"cap_usd": float(cap), "month_to_date_usd": float(spent)})
    return None


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
