"""Server-side RBAC permission model (feature 008, M5).

The role→permission map is the single source of truth mirrored from `frontend/src/lib/permissions.ts`
via `scripts/gen_rbac.py` → `rbac_permissions.json` (regenerate when permissions.ts changes; a drift test
asserts parity). `has_permission` is fail-closed: an unknown/empty/None role has NO permissions (P3).
"""
from __future__ import annotations

import json
import pathlib

_DATA = pathlib.Path(__file__).with_name("rbac_permissions.json")

# {role: frozenset(permissions)} — loaded once at import from the generated artifact.
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    role: frozenset(perms) for role, perms in json.loads(_DATA.read_text()).items()
}


def has_permission(role: str | None, permission: str) -> bool:
    """True iff `role` grants `permission`. Fail-closed: unknown/empty/None role → False (P3)."""
    if not role:
        return False
    return permission in ROLE_PERMISSIONS.get(role, frozenset())
