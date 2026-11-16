"""Append-only audit writer (feature 026, FR-023).

`record()` inserts ONE `audit_logs` row in the CALLER's session/transaction — so the audit row commits or rolls back
atomically with the state change it describes (a rolled-back publish leaves no audit trail claiming it happened).
`ac_app` holds only SELECT, INSERT on `audit_logs` (migration 0016), so the log is append-only at the DB layer.

Guardrails (fail loud, never silently drop):
- `action` must be in the closed allow-list below — a typo'd action is a bug, not a new event type.
- `meta` holds identifiers only (UUIDs, short id-shaped strings, or None). No prose, numbers, nested objects — the
  audit log must never become a side channel for prompt text, secrets, or PII.
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Mapping

from sqlalchemy import insert

from api.models import AuditLog

ACTIONS: frozenset[str] = frozenset({
    "baseline.freeze",
    "baseline.clear",
    "agent.publish",
    "agent.repoint",
    "agent.archive",
    "agent.restore",
})

_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def _clean_meta(meta: Mapping[str, Any] | None) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for k, v in (meta or {}).items():
        if not isinstance(k, str) or not _ID_RE.match(k):
            raise ValueError(f"audit meta key {k!r} is not id-shaped")
        if v is None:
            out[k] = None
        elif isinstance(v, uuid.UUID):
            out[k] = str(v)
        elif isinstance(v, str) and _ID_RE.match(v):  # bool/int are not str → rejected below
            out[k] = v
        else:
            raise ValueError(f"audit meta[{k!r}] must be a UUID, an id string, or None — got {type(v).__name__}")
    return out


async def record(session, principal, action: str, target_type: str | None,
                 target_id: uuid.UUID | None, meta: Mapping[str, Any] | None = None) -> None:
    if action not in ACTIONS:
        raise ValueError(f"unknown audit action {action!r}; allowed: {sorted(ACTIONS)}")
    clean = _clean_meta(meta)
    ws = await session.scalar(_current_ws())
    if not ws:
        raise ValueError("audit.record needs a tenant session (app.workspace_id unset)")
    await session.execute(insert(AuditLog).values(
        workspace_id=uuid.UUID(ws), actor=principal.subject, action=action,
        target_type=target_type, target_id=target_id, meta=clean,
    ))


def _current_ws():
    from sqlalchemy import text
    return text("select nullif(current_setting('app.workspace_id', true), '')")
