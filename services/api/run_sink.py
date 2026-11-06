"""Durable run-record sink (feature 006, US5, FR-013).

Maps a runtime `RunRecord` → a `runs` row **on the caller's existing request session**. It MUST NOT open a second
`tenant_session`: doing so inside the invoke StreamingResponse generator (while the request session is held open)
deadlocks. Persists **identity + metrics only** — no raw prompt/response content (SC-009). Called by the invoke and
eval routers for every invocation (success or failure, P8).
"""
from __future__ import annotations

import dataclasses
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from api.models import Run


def _uuid_or_none(val) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(val))
    except (ValueError, TypeError):
        return None


async def persist_run_record(session: AsyncSession, workspace_id: str, rec) -> None:
    """Add one run row from a RunRecord to the caller's session (same connection/transaction). Usage is stored as
    token/cost metrics only — no content (SC-009)."""
    usage: dict = {}
    if getattr(rec, "usage", None) is not None:
        try:
            usage = {k: (float(v) if isinstance(v, (int, float)) else v)
                     for k, v in dataclasses.asdict(rec.usage).items()}
        except (TypeError, ValueError):
            usage = {}
    outcome = rec.outcome.value if hasattr(rec.outcome, "value") else str(rec.outcome)
    session.add(Run(
        workspace_id=uuid.UUID(workspace_id),
        agent_id=_uuid_or_none(rec.agent_id),
        version_id=_uuid_or_none(rec.version_id),
        model=rec.model or "",
        profile_hash=rec.profile_hash or "",
        prompt_hash=rec.prompt_hash or "",
        usage=usage,
        latency_ms=rec.latency_ms or 0,
        cost_usd=rec.cost_usd if rec.cost_usd is not None else 0,
        outcome=outcome,
        source=rec.source or "playground",
        correlation_id=rec.correlation_id or "",
    ))
    await session.flush()
