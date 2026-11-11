"""Provider-key custody router (feature 014).

Workspace-admin config: capture (write-once), list (masked-only), rotate, revoke — all gated on `key.manage`
(admin) and scoped to the caller's workspace via the RLS session (P5). Provider keys carry NO per-resource
access policy (they are workspace-admin config, not a shareable resource), so there is no `access_principal`
gate here — only RBAC + RLS. The raw secret is never accepted back out: responses are masked-only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import require_permission, idempotency_guard, rate_limit, resolve_workspace, tenant_db
from api.provider_keys import (capture_provider_key, list_provider_keys, revoke_provider_key,
                               rotate_provider_key)
from api.schemas.provider_keys import ProviderKeyCreate, ProviderKeyOut, ProviderKeyRotate

router = APIRouter(prefix="/api/v1/provider-keys", tags=["provider-keys"])


@router.post("", status_code=201, dependencies=[Depends(require_permission("key.manage"))])
async def capture_key(
    body: ProviderKeyCreate,
    ws: str = Depends(resolve_workspace),
    session: AsyncSession = Depends(tenant_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    _rl: None = Depends(rate_limit),
) -> ProviderKeyOut:
    async with idempotency_guard(ws, idempotency_key):
        return await capture_provider_key(session, ws, body)


@router.get("", dependencies=[Depends(require_permission("key.manage"))])
async def list_keys(
    ws: str = Depends(resolve_workspace),
    session: AsyncSession = Depends(tenant_db),
) -> list[ProviderKeyOut]:
    return await list_provider_keys(session, ws)


@router.post("/{key_id}/rotate", dependencies=[Depends(require_permission("key.manage"))])
async def rotate_key(
    key_id: str,
    body: ProviderKeyRotate,
    ws: str = Depends(resolve_workspace),
    session: AsyncSession = Depends(tenant_db),
    _rl: None = Depends(rate_limit),
) -> ProviderKeyOut:
    return await rotate_provider_key(session, ws, key_id, body)


@router.post("/{key_id}/revoke", dependencies=[Depends(require_permission("key.manage"))])
async def revoke_key(
    key_id: str,
    ws: str = Depends(resolve_workspace),
    session: AsyncSession = Depends(tenant_db),
    _rl: None = Depends(rate_limit),
) -> ProviderKeyOut:
    return await revoke_provider_key(session, ws, key_id)
