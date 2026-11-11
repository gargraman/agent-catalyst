"""Pydantic contracts for provider-key custody (feature 014) — mirror `frontend/src/lib/data.ts` ProviderKey.

Write-once capture: the raw `value` comes IN on create/rotate and is NEVER echoed back. `ProviderKeyOut` is
the masked-only roster shape the FE expects (`added`←`created_at`, `rotatedAt`←`rotated_at`); it deliberately
carries NO `value` and NO `vault_path` (FR-017, SC-001).
"""
from __future__ import annotations

import datetime

from pydantic import BaseModel, ConfigDict


class ProviderKeyCreate(BaseModel):
    """Capture body. `value` is the raw secret — stored once in the secrets backend, never returned."""
    model_config = ConfigDict(extra="forbid")

    provider: str
    label: str
    value: str
    region: str | None = None


class ProviderKeyRotate(BaseModel):
    """Rotate body — the replacement raw secret (write-once, never returned)."""
    model_config = ConfigDict(extra="forbid")

    value: str


class ProviderKeyOut(BaseModel):
    """Masked roster entry — the drop-in `data.ts` ProviderKey shape. No raw value, no vault_path."""
    id: str
    provider: str
    label: str
    masked: str
    region: str | None = None
    added: datetime.datetime          # ← created_at
    rotatedAt: datetime.datetime | None = None   # ← rotated_at
    status: str
