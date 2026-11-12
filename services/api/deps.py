"""Shared FastAPI dependencies for the thin API (M4, feature 005).

The request machinery every route needs: a (dev-stub) session principal, workspace selection + authorization
against the caller's claim (P5/FR-007), a tenant-scoped DB session (ac_app + GUC + RLS via `db.tenant_session`),
a rate-limit seam (F5), and an in-process idempotency in-flight guard (D8). Real OIDC/Vault/Redis are deferred.
Tests override `session_principal` to inject a caller (no real auth in tests).
"""
from __future__ import annotations

import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from fastapi import Depends, Header, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from api import membership, metrics, ratelimit, spend
from api.access_policy import PolicyPrincipal, can_manage, can_use, parse_policy
from api.db import tenant_session
from api.errors import (
    AccessDenied,
    AppError,
    BudgetExceeded,
    IdempotencyInProgress,
    InputInvalid,
    NotFound,
    RateLimited,
)
from api.permissions import has_permission
from api.settings import get_settings

_log = logging.getLogger("api.spend")
_deny_log = logging.getLogger("api.access_policy")
_rl_log = logging.getLogger("api.ratelimit")

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


# Single source of truth for the limiter's header names — deny headers = success trio + Retry-After.
def _rl_success_headers(limit: int, d: ratelimit.Decision) -> dict[str, str]:
    """The `X-RateLimit-*` trio sent on every gated response."""
    return {
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": str(d.remaining),
        "X-RateLimit-Reset": str(d.reset_epoch),
    }


def _rl_deny_headers(limit: int, d: ratelimit.Decision) -> dict[str, str]:
    """The success trio plus `Retry-After`, carried on the 429 envelope."""
    return {**_rl_success_headers(limit, d), "Retry-After": str(d.retry_after)}


def _rl_set(response: Response, limit: int, d: ratelimit.Decision) -> None:
    """Attach the success `X-RateLimit-*` headers to the response (no Retry-After on allow)."""
    for k, v in _rl_success_headers(limit, d).items():
        response.headers[k] = v


# Fail-open alert: the metric is the alert signal (per request); the WARNING log is throttled so a sustained
# Redis outage does not emit one line per request (code-review: log flood). Process-local, best-effort.
_rl_degraded_last_log = 0.0
_RL_DEGRADED_LOG_INTERVAL_S = 30.0


def rate_limit(route_class: str):
    """Per-identity sliding-window rate limit (feature 016 / F5). Returns a dependency for `route_class`
    (`invoke` | `playground` | `eval` | `ingest` | `default`); window/limit come from `settings.rate_limit_for`
    (fallback-safe — an unlisted class resolves to `default`, never a KeyError). Keyed per *principal* in this
    slice (`rl:prin:{subject}:{route_class}` — no token/secret in the key, P5); per-token invoke keying lands
    in 018 via the same engine. Wire AFTER `require_permission` (403 first) and BEFORE `enforce_spend_cap`/the
    handler (429 before any provider/ingestion work). On deny → 429 `rate.limited` with headers; on a Redis
    outage → **fail open + alert** (availability control, FR-024a); on allow → success `X-RateLimit-*` headers.
    NOTE: success headers are set on the injected `Response`, which FastAPI only merges for handlers that return a
    serializable value — endpoints that return a `Response`/`StreamingResponse` directly (invoke, 204 deletes) do
    NOT carry the success headers. The 429 deny path is unaffected (headers ride the `RateLimited` error envelope)."""
    async def _rl(
        response: Response,
        principal: Principal = Depends(session_principal),
    ) -> None:
        settings = get_settings()
        if not settings.rate_limit_enabled:
            return None
        window_s, limit = settings.rate_limit_for(route_class)
        key = f"rl:prin:{principal.subject}:{route_class}"
        decision = await ratelimit.check(key, limit, window_s)

        if decision.degraded:                                  # Redis down → fail open + alert (FR-024a)
            metrics.record_rate_limit_degraded()               # per-request alert signal
            global _rl_degraded_last_log                        # WARNING throttled (avoid per-request flood)
            now = time.monotonic()
            if now - _rl_degraded_last_log >= _RL_DEGRADED_LOG_INTERVAL_S:
                _rl_degraded_last_log = now
                _rl_log.warning("rate limiter degraded (fail-open): backend unavailable route_class=%s", route_class)
            _rl_set(response, limit, decision)
            return None
        if not decision.allowed:                               # over limit → 429 before any work
            metrics.record_rate_limited(route_class)
            raise RateLimited(f"rate limit exceeded; retry after {decision.retry_after}s",
                              headers=_rl_deny_headers(limit, decision))
        _rl_set(response, limit, decision)                     # under limit → success headers
        return None

    return _rl


# ---- access-policy enforcement spine (feature 013, P3 deterministic + P5 no-leak) ----------------
async def access_principal(
    principal: Principal = Depends(session_principal),
    ws: str = Depends(resolve_workspace),
    session: AsyncSession = Depends(tenant_db),
) -> PolicyPrincipal:
    """Build the access-policy view of the caller for the active workspace: console `role` (already resolved onto
    `workspace_claims[ws]` by `resolve_workspace`), authZ `teams` (one RLS-scoped read on the request's shared
    session), and `subject` (owner match). An execute-only/token caller has no console role — that plane is the
    NEXT feature; here a console caller always has a role."""
    role = principal.workspace_claims.get(ws, "")
    teams = await membership.teams_for(session, principal.subject)
    return PolicyPrincipal(role=role, teams=tuple(teams), subject=principal.subject)


def _log_deny(capability: str, policy, principal: PolicyPrincipal, not_found: NotFound | None) -> None:
    """FR-014: one structured, NON-SECRET deny line — capability, tier, subject, resource (from the caller's
    `not_found.detail`, which carries only the resource family+id, never a token/secret). Teams/role are low-card
    authZ attributes, safe to log; no policy body, no credential."""
    detail = getattr(not_found, "detail", None) or {}
    _deny_log.info("access_policy deny capability=%s tier=%s subject=%s teams=%s resource=%s",
                   capability, parse_policy(policy).get("visibility"), principal.subject,
                   ",".join(principal.teams), detail)


def ensure_use(policy, principal: PolicyPrincipal, *, not_found: NotFound | None = None) -> None:
    """Fail-closed USE gate (read/list/invoke/bind). On deny raise `NotFound` so visibility is NEVER leaked
    (P5): a denied resource is indistinguishable from an absent one. Routers pass `not_found=` so the denied
    body is byte-identical to the genuine absent-row 404."""
    if not can_use(policy, principal):
        _log_deny("use", policy, principal, not_found)
        raise not_found or NotFound("resource not found")


def ensure_manage(policy, principal: PolicyPrincipal, *, not_found: NotFound | None = None) -> None:
    """Fail-closed MANAGE gate (edit/publish/archive/restore). 404-then-403: if the caller can't even USE the
    resource, raise `NotFound` (no leak); if usable but not manageable, raise `AccessDenied` (403). Wire AFTER
    the RBAC `require_permission` gate so a role-level 403 still fires first."""
    if not can_use(policy, principal):
        _log_deny("manage", policy, principal, not_found)
        raise not_found or NotFound("resource not found")
    if not can_manage(policy, principal):
        _log_deny("manage", policy, principal, not_found)
        raise AccessDenied("insufficient access policy to manage this resource")


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
