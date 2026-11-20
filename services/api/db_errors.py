"""Map data-layer integrity refusals to typed API errors (feature 025 US4; contract api §1, research D12).

Routes pre-check the common cases; this covers the RACE path where the pre-check passed but migration 0015's
constraints/triggers refused at write time. Only refusals WE added are mapped — anything else returns None and the
caller re-raises (an honest 500 beats a guessed 4xx).

Messages are fixed text: the raw DB message can name rows of another workspace (RI bypasses RLS), so it is never echoed.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.errors import AppError, ConflictError, InputInvalid, NotFound, VersionFrozen

# 23503 on these ⇒ the referenced catalog row does not exist in this workspace (deleted, foreign, or never existed).
_REFERENCE_FKS = frozenset({
    "fk_skill_bindings_skill", "fk_tool_bindings_tool", "fk_tool_bindings_mcp_tool",
    "fk_retrieval_bindings_kb_ws", "fk_kb_chunks_kb",
    # the pre-0015 single-column KB FK is kept alongside the composite one; RI trigger order decides which fires first
    "retrieval_bindings_kb_id_fkey",
})


def _parts(exc: BaseException) -> tuple[str | None, str | None, str]:
    """(sqlstate, constraint_name, message) from a SQLAlchemy IntegrityError over asyncpg (or psycopg)."""
    orig = getattr(exc, "orig", None)
    if orig is None:
        return None, None, ""
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    cause = getattr(orig, "__cause__", None)
    constraint = getattr(cause, "constraint_name", None) or getattr(getattr(orig, "diag", None), "constraint_name", None)
    message = getattr(cause, "message", None) or str(orig)
    return sqlstate, constraint, message or ""


def map_integrity_error(exc: BaseException) -> AppError | None:
    sqlstate, constraint, message = _parts(exc)
    if sqlstate == "23505" and constraint == "uq_eval_suites_agent_name":     # 026 US4 (review #7)
        return ConflictError("an eval suite with this name already exists for this agent", detail={"field": "name"})
    if sqlstate == "23503":
        if constraint in _REFERENCE_FKS:
            return NotFound("a referenced skill, tool, or knowledge base does not exist in this workspace")
        if constraint == "fk_agents_active_version":
            return InputInvalid("active version must be a version of this agent")
        return None
    if sqlstate == "23001" and "frozen_binding:" in message:
        return VersionFrozen("this version is committed; its bindings are immutable — create a new version")
    if sqlstate == "23514":
        if constraint == "ck_tool_bindings_one_target":
            return InputInvalid("a tool binding must name exactly one target for its kind")
        if "chunk_dim_mismatch:" in message:
            return InputInvalid("embedding dimension does not match the knowledge base's declared dimension")
        if "eval_baselines_run_check:" in message:          # 026 US4: race past the freeze route's pre-check
            return InputInvalid("the baseline must be a completed eval run of this suite")
    return None


@asynccontextmanager
async def integrity_mapped() -> AsyncIterator[None]:
    """Re-raise a mapped IntegrityError as its typed AppError; unmapped ones propagate unchanged (honest 500).
    The session's transaction is aborted either way — `tenant_session` rolls back on the exception."""
    try:
        yield
    except IntegrityError as exc:
        err = map_integrity_error(exc)
        if err is None:
            raise
        raise err from None


async def check_active_version_now(session: AsyncSession) -> None:
    """Fire the DEFERRED `fk_agents_active_version` check now (flushes first), so a refusal surfaces inside the route —
    where `integrity_mapped` turns it into 400 — rather than at COMMIT during dependency teardown (an unmapped 500)."""
    await session.flush()
    await session.execute(text("SET CONSTRAINTS fk_agents_active_version IMMEDIATE"))
