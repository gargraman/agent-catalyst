"""Provider-key custody service (feature 014).

The application-layer logic that ties the `provider_keys` table (masked value + opaque reference + metadata)
to the pluggable secrets backend (`api/secrets/`). Raw values flow ONLY to the backend at capture/rotate and
to the provider at call time; they are never stored in the app DB, returned to a client, or logged (FR-017).

`resolve_provider_key` is the invoke/eval seam that replaces the single `PROVIDER_API_KEY` env var.
"""
from __future__ import annotations

import datetime
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api import deps
from api.errors import ConfigError, ConflictError, InputInvalid, NotFound
from api.models import ProviderKey
from api.schemas.provider_keys import ProviderKeyCreate, ProviderKeyOut, ProviderKeyRotate
from api.secrets import SecretsError, build_secrets_provider
from api.settings import get_settings

# Behaviour added across phases: mask_secret + projection (foundational), capture/list (US1), resolve (US2),
# rotate (US3), revoke (US4).


def mask_secret(secret: str) -> str:
    """Server-computed display mask — mirrors frontend/src/lib/secrets.ts maskSecret (6 head + 8 bullets +
    4 tail; full bullets for short values). The client never derives this; the server is the source of truth."""
    s = secret.strip()
    if not s:
        return ""
    if len(s) <= 10:
        return "•" * len(s)
    return s[:6] + "••••••••" + s[-4:]


def _provider_key_out(row: ProviderKey) -> ProviderKeyOut:
    """Project a stored row into the masked-only API/FE shape. NEVER reads/returns the raw value or vault_path."""
    return ProviderKeyOut(
        id=str(row.id),
        provider=row.provider,
        label=row.label,
        masked=row.masked,
        region=row.region,
        added=row.created_at,
        rotatedAt=row.rotated_at,
        status=row.status,
    )


# ---- US1: capture + list -------------------------------------------------------------------------
async def capture_provider_key(session: AsyncSession, ws: str, body: ProviderKeyCreate) -> ProviderKeyOut:
    """Write-once capture (FR-003/FR-004). The raw value is stored ONLY in the secrets backend; the row keeps a
    masked display value + the opaque `vault_path`. Atomic: a duplicate label is rejected (409) BEFORE the
    backend is touched; a backend write failure rolls the transaction back (no orphan row) and surfaces a
    fail-closed `config_error`."""
    provider = (body.provider or "").strip()
    label = (body.label or "").strip()
    value = (body.value or "").strip()
    if not provider or not label or not value:
        raise InputInvalid("provider, label, and value are all required",
                           detail={"fields": ["provider", "label", "value"]})

    # Insert the row first (empty vault_path placeholder) so a duplicate (workspace,label) is caught as a
    # typed 409 WITHOUT ever writing a secret to the backend.
    row = ProviderKey(workspace_id=uuid.UUID(ws), provider=provider, label=label,
                      masked=mask_secret(value), vault_path="", region=body.region, status="active")
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ConflictError("a provider key with this label already exists in this workspace",
                            detail={"field": "label"}) from exc

    # Store the raw value in the configured secrets backend and record the opaque reference.
    backend = build_secrets_provider()
    try:
        vault_path = await backend.put(ws, label, value)
    except SecretsError as exc:   # backend unreachable/failed → fail closed; transaction rolls back (no row)
        raise ConfigError("secrets backend unavailable; provider key not stored") from exc
    row.vault_path = vault_path
    await session.flush()
    return _provider_key_out(row)


async def list_provider_keys(session: AsyncSession, ws: str) -> list[ProviderKeyOut]:
    """Masked-only roster for the workspace (RLS-scoped). Includes revoked tombstones (FE shows their status)."""
    rows = (await session.execute(
        select(ProviderKey).order_by(ProviderKey.created_at.desc(), ProviderKey.id.desc())
    )).scalars().all()
    return [_provider_key_out(r) for r in rows]


# ---- US2: resolve-at-invoke / eval (the seam that retires the shared env var) ---------------------
async def resolve_provider_key(session: AsyncSession, ws: str, provider: str) -> str:
    """Resolve the plaintext provider credential for `(ws, provider)` just-in-time (FR-011/FR-016). Selects the
    most-recently-added `active` key (revoked/expiring never resolve), then fetches its plaintext from the
    secrets backend. Fail-closed (FR-014): a configured key that won't resolve, or no key at all in prod, raises
    `ConfigError` (503 `config_error`) BEFORE any provider call. In non-prod ONLY, a *missing* key falls back to
    `PROVIDER_API_KEY` for dev continuity — but a configured-yet-unresolvable key ALWAYS fails closed (we never
    silently substitute the env key for one an admin deliberately set). The returned string is passed to
    `RunContext.api_key`; the runtime (P6) never sees this module.
    """
    row = (await session.execute(
        select(ProviderKey)
        .where(ProviderKey.provider == provider, ProviderKey.status == "active")
        .order_by(ProviderKey.created_at.desc(), ProviderKey.id.desc())
        .limit(1)
    )).scalars().first()
    if row is not None:
        backend = build_secrets_provider()
        try:
            return await backend.get(row.vault_path)
        except SecretsError as exc:        # configured key, backend can't produce it → fail closed (any env)
            raise ConfigError("provider key could not be resolved from the secrets backend",
                              detail={"provider": provider}) from exc
    # No active key for this provider.
    if get_settings().env != "prod" and deps.PROVIDER_API_KEY:
        return deps.PROVIDER_API_KEY       # dev continuity only (disabled in prod — FR-014)
    raise ConfigError("no active provider key configured for this provider",
                      detail={"provider": provider})


# ---- US3/US4: rotate + revoke --------------------------------------------------------------------
async def _load_key(session: AsyncSession, key_id: str) -> ProviderKey | None:
    """Load a key by id within the request's RLS session — a key in another workspace is invisible (→ None →
    404), so cross-tenant rotate/revoke is impossible (P5)."""
    try:
        kid = uuid.UUID(key_id)
    except (ValueError, TypeError):
        return None
    return (await session.execute(select(ProviderKey).where(ProviderKey.id == kid))).scalar_one_or_none()


async def rotate_provider_key(session: AsyncSession, ws: str, key_id: str,
                              body: ProviderKeyRotate) -> ProviderKeyOut:
    """Replace the vaulted secret for an existing key (FR-007): update the backend value, refresh `masked` +
    `rotated_at`, keep id/label/status. The superseded value no longer resolves. 404 on unknown/cross-tenant."""
    value = (body.value or "").strip()
    if not value:
        raise InputInvalid("value is required", detail={"fields": ["value"]})
    row = await _load_key(session, key_id)
    if row is None:
        raise NotFound("provider key not found", detail={"id": key_id})
    backend = build_secrets_provider()
    try:
        row.vault_path = await backend.rotate(row.vault_path, value)
    except SecretsError as exc:
        raise ConfigError("secrets backend unavailable; provider key not rotated") from exc
    row.masked = mask_secret(value)
    row.rotated_at = datetime.datetime.now(datetime.timezone.utc)
    await session.flush()
    return _provider_key_out(row)


async def revoke_provider_key(session: AsyncSession, ws: str, key_id: str) -> ProviderKeyOut:
    """Revoke a key (FR-008): flip to a `revoked` tombstone (row RETAINED, never deleted) and disable the backend
    secret so resolution fails closed. Idempotent on an already-revoked key. 404 on unknown/cross-tenant."""
    row = await _load_key(session, key_id)
    if row is None:
        raise NotFound("provider key not found", detail={"id": key_id})
    if row.status != "revoked":
        backend = build_secrets_provider()
        try:
            await backend.disable(row.vault_path)
        except SecretsError as exc:
            raise ConfigError("secrets backend unavailable; provider key not revoked") from exc
        row.status = "revoked"
        await session.flush()
    return _provider_key_out(row)
