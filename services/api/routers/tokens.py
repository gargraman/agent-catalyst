"""Caller-token plane router (feature 018) — mint/list/revoke caller tokens + admin immediate revocation.

Mint/list/revoke are console-session routes gated on `agent.publish` (publisher+, D5) and scoped to the caller's
workspace via RLS. The admin force-logout (`/org/revoke-user`) is gated on `member.manage` (admin) and writes the
transient Redis deny-set so an offboarded user's active session AND live caller tokens are cut on their next
request. The raw secret is generated server-side and returned EXACTLY once at mint (FR-010); only its sha256 hash
+ a display prefix persist (FR-010/011).
"""
from __future__ import annotations

import datetime
import logging
import secrets as _secrets
import uuid

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import rate_limit, require_permission, resolve_workspace, tenant_db
from api.errors import InputInvalid, NotFound
from api.models import Agent, ApiToken
from api.publish.token_auth import hash_secret
from api.schemas.tokens import RevokeUserIn, TokenMintIn, TokenMintOut, TokenOut
from api.settings import get_settings

router = APIRouter(prefix="/api/v1", tags=["tokens"])
_audit = logging.getLogger("api.security")


def _new_secret() -> str:
    """A fresh opaque caller-token bearer. `ac_live_` prefix is a display convention; the entropy is the suffix."""
    return "ac_live_" + _secrets.token_urlsafe(32)


def _out(t: ApiToken) -> TokenOut:
    return TokenOut(id=str(t.id), prefix=t.prefix, name=t.name, agent_id=str(t.agent_id),
                    created_at=t.created_at, last_used_at=t.last_used_at,
                    expires_at=t.expires_at, revoked_at=t.revoked_at)


@router.post("/agents/{agent_id}/tokens", status_code=201,
             dependencies=[Depends(require_permission("agent.publish")), Depends(rate_limit("default"))])
async def mint_token(agent_id: str, body: TokenMintIn, ws: str = Depends(resolve_workspace),
                     session: AsyncSession = Depends(tenant_db)) -> TokenMintOut:
    try:
        aid = uuid.UUID(agent_id)
    except ValueError:
        raise NotFound("agent not found", detail={"agent_id": agent_id})
    agent = (await session.execute(select(Agent).where(Agent.id == aid))).scalar_one_or_none()
    if agent is None:                                      # RLS-scoped: absent OR cross-tenant → 404 (no leak)
        raise NotFound("agent not found", detail={"agent_id": agent_id})
    expires_at = body.expires_at
    if expires_at is not None:
        # A naive datetime from the client would raise TypeError on the aware comparison below (→ 500).
        # Normalize to UTC so validation + storage are always tz-aware (the column is timestamptz).
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=datetime.timezone.utc)
        if expires_at <= datetime.datetime.now(datetime.timezone.utc):
            raise InputInvalid("expires_at must be in the future", detail={"field": "expires_at"})
    secret = _new_secret()
    tok = ApiToken(workspace_id=uuid.UUID(ws), agent_id=aid, name=body.name,
                   token_hash=hash_secret(secret), prefix=secret[:12],
                   scopes={"invoke": True, "teams": body.teams or []}, expires_at=expires_at)
    session.add(tok)
    await session.flush()
    # The ONLY response that ever carries the raw secret (FR-010); it is not logged or stored.
    return TokenMintOut(id=str(tok.id), prefix=tok.prefix, secret=secret, agent_id=str(tok.agent_id),
                        name=tok.name, created_at=tok.created_at, expires_at=tok.expires_at)


@router.get("/agents/{agent_id}/tokens", dependencies=[Depends(require_permission("agent.publish"))])
async def list_tokens(agent_id: str, ws: str = Depends(resolve_workspace),
                      session: AsyncSession = Depends(tenant_db)) -> list[TokenOut]:
    try:
        aid = uuid.UUID(agent_id)
    except ValueError:
        return []
    rows = (await session.execute(
        select(ApiToken).where(ApiToken.agent_id == aid).order_by(ApiToken.created_at))).scalars().all()
    return [_out(t) for t in rows]


@router.delete("/tokens/{token_id}", status_code=204,
               dependencies=[Depends(require_permission("agent.publish")), Depends(rate_limit("default"))])
async def revoke_token(token_id: str, ws: str = Depends(resolve_workspace),
                       session: AsyncSession = Depends(tenant_db)) -> Response:
    try:
        tid = uuid.UUID(token_id)
    except ValueError:
        raise NotFound("token not found", detail={"token_id": token_id})
    tok = (await session.execute(select(ApiToken).where(ApiToken.id == tid))).scalar_one_or_none()
    if tok is None:                                        # RLS-scoped: absent OR cross-tenant → 404 (no leak)
        raise NotFound("token not found", detail={"token_id": token_id})
    if tok.revoked_at is None:                             # idempotent: re-revoke is a no-op 204
        tok.revoked_at = datetime.datetime.now(datetime.timezone.utc)
        await session.flush()
    return Response(status_code=204)


@router.post("/org/revoke-user", status_code=204,
             dependencies=[Depends(require_permission("member.manage")), Depends(rate_limit("default"))])
async def revoke_user(body: RevokeUserIn, ws: str = Depends(resolve_workspace)) -> Response:
    """Admin force-logout: write the subject and/or token-id to the deny-set so an active console session AND a
    live caller token are cut on their next request (FR-013). Fails if the deny-set write can't be confirmed."""
    from api.auth import denyset
    if not body.subject and not body.token_id:
        raise InputInvalid("subject or token_id required", detail={"field": "subject|token_id"})
    await denyset.deny(subject=body.subject, token_id=body.token_id, ttl_s=get_settings().denyset_ttl_s)
    # Durable append-only audit (`audit_logs`) is not built yet (deferred — see 018 research); structured log now.
    _audit.info("security.revoke_user workspace=%s subject=%s token_id=%s",
                ws, body.subject or "-", body.token_id or "-")
    return Response(status_code=204)
