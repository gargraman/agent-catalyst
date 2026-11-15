"""Wire retrieval into a RunContext for every caller of runtime.invoke (feature 024, tasks.md T048; contracts/api.md).

One seam for the console plane, the published plane and evals, so the three cannot drift (P6):
  * `ctx.knowledge` = a `DbKnowledgeAccess` on the caller's RLS session (eval: policy bypassed AFTER the H1 gate);
  * `ctx.embedding_api_key` resolved for the bound embedding provider — a missing/unresolvable key is NOT an error
    here: it stays None and the binding degrades `embedding_key_missing` while the run proceeds (FR-020);
  * `ctx.embedding_region` for keyless (Bedrock) providers.
Also the H1 eval start gate and the client-facing citations payload (H2 masking; published plane omits status).
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api import metrics
from api.access_policy import PolicyPrincipal, can_use
from api.errors import ConfigError, EvalKnowledgeForbidden
from api.knowledge_access import DbKnowledgeAccess
from api.models import KnowledgeBase
from api.settings import get_settings

_KEYLESS = frozenset({"bedrock"})


async def attach_knowledge(session: AsyncSession, ws: str, snapshot, ctx, principal: PolicyPrincipal | None, *,
                           bypass_access: bool = False) -> DbKnowledgeAccess | None:
    """Mutates `ctx`; returns the access port (its `.denied` drives masking) or None when nothing is bound."""
    refs = tuple(getattr(snapshot, "retrieval", ()) or ())
    if not refs:
        return None
    ka = DbKnowledgeAccess(session, ws, principal, bypass_access=bypass_access)
    ctx.knowledge = ka
    keyed = [p for p in dict.fromkeys(r.embedding_provider for r in refs) if p and p not in _KEYLESS]
    if keyed:
        from api.provider_keys import resolve_provider_key
        try:
            ctx.embedding_api_key = await resolve_provider_key(session, ws, keyed[0])
        except ConfigError:
            ctx.embedding_api_key = None             # degrade the binding, never fail the run
    if any(r.embedding_provider in _KEYLESS for r in refs):
        ctx.embedding_region = get_settings().aws_region
    ctx.record_detection = make_detection_sink(ctx.correlation_id)
    return ka


_INJ_LOG = logging.getLogger("ac.injection")
_DETECTION_KEYS = ("run_id", "workspace_id", "kb_id", "document_id", "chunk_id", "categories", "checkpoint",
                   "scanner_version")


def make_detection_sink(correlation_id: str | None):
    """US3 (T059): one structured `ac.injection` line per withheld passage + detection/withheld counters. Allow-listed
    keys only, so a record can never smuggle passage text into the log. Never raises."""
    def _sink(rec: dict) -> None:
        try:
            line = {"event": "injection.detected", **{k: rec.get(k) for k in _DETECTION_KEYS},
                    "correlation_id": correlation_id}
            _INJ_LOG.info(json.dumps(line, default=str, sort_keys=True))
            for c in rec.get("categories") or ():
                metrics.record_injection_detection(rec.get("checkpoint") or "retrieve", c)
        except Exception:  # noqa: BLE001
            pass
    return _sink


async def ensure_eval_can_use_knowledge(session: AsyncSession, snapshot, principal: PolicyPrincipal) -> None:
    """analyze H1: the caller must be able to USE every KB bound to the evaluated version, checked BEFORE any case
    runs. A KB absent from the tenant counts as unusable. KB ids are never echoed."""
    import uuid
    ids = []
    for r in tuple(getattr(snapshot, "retrieval", ()) or ()):
        try:
            ids.append(uuid.UUID(r.kb_id))
        except (ValueError, TypeError):
            raise EvalKnowledgeForbidden("the version uses knowledge you cannot access") from None
    if not ids:
        return
    rows = dict((await session.execute(
        select(KnowledgeBase.id, KnowledgeBase.access_policy).where(KnowledgeBase.id.in_(ids)))).all())
    if any(i not in rows or not can_use(rows[i], principal) for i in ids):
        raise EvalKnowledgeForbidden("the version uses knowledge you cannot access")


def citations_payload(data: dict, *, denied: set[str], published: bool) -> dict:
    """Client-facing `citations` frame. Citations are references only (no passage text). Console plane: per-binding
    status with policy-denied KBs masked (H2). Published plane: status omitted entirely."""
    out = {"knowledge_used": bool(data.get("knowledge_used")),
           "citations": [c for c in data.get("citations", ()) if c.get("kb_id") not in denied]}
    if not published:
        out["knowledge_status"] = [
            {"kb_id": None, "status": "unavailable", "restricted": True} if s.get("kb_id") in denied
            else {"kb_id": s.get("kb_id"), "status": s.get("status")}
            for s in data.get("knowledge_status", ())]
    return out
