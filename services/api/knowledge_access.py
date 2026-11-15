"""DB-backed KnowledgeAccess port (feature 024, tasks.md T047; data-model §3; spec FR-014..FR-016, SC-003).

Implements `runtime.retrieval.port.KnowledgeAccess` over pgvector on the CALLER'S session (RLS-scoped), so it never
opens a second tenant session inside the SSE generator. Per search:
  1. read the KB live: absent in this tenant (RLS) or archived → "unavailable"; the caller's CURRENT access policy
     denies USE → "unavailable" too (indistinguishable — no leak), unless `bypass_access` (eval, after the H1 gate);
  2. kNN over live generations only, filtered by the FROZEN embedding model, explicit workspace predicate (defence in
     depth beside RLS), cosine score ≥ threshold, at most top_k, score-descending.
`denied` collects KB ids refused by policy so the API can mask them in client-visible status (analyze H2).
"""
from __future__ import annotations

import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.access_policy import PolicyPrincipal, can_use
from api.models import KnowledgeBase
from runtime.retrieval.port import Passage, SearchOutcome

_KNN = text("""
    select c.id::text as chunk_id, c.text, c.token_count, c.meta, d.id::text as doc_id, d.name as doc_name,
           1 - (c.embedding <=> CAST(:qvec AS vector)) as score
    from kb_chunks c join kb_documents d on d.id = c.document_id
    where c.workspace_id = :ws and c.kb_id = :kb
      and c.embedding_model = :model
      and d.active_generation > 0 and c.generation = d.active_generation
      and 1 - (c.embedding <=> CAST(:qvec AS vector)) >= :threshold
    order by c.embedding <=> CAST(:qvec AS vector)
    limit :k
""")


class DbKnowledgeAccess:
    def __init__(self, session: AsyncSession, workspace_id: str, principal: PolicyPrincipal | None, *,
                 bypass_access: bool = False) -> None:
        self.session = session
        self.workspace_id = str(workspace_id)
        self.principal = principal
        self.bypass_access = bypass_access
        self.denied: set[str] = set()

    async def search(self, ref, qvec, *, top_k: int, threshold: float) -> SearchOutcome:
        try:
            kb_id = uuid.UUID(ref.kb_id)
        except (ValueError, TypeError):
            return SearchOutcome("unavailable")
        kb = (await self.session.execute(
            select(KnowledgeBase.archived_at, KnowledgeBase.access_policy).where(KnowledgeBase.id == kb_id))).first()
        if kb is None or kb.archived_at is not None:
            return SearchOutcome("unavailable")
        if not self.bypass_access and (self.principal is None or not can_use(kb.access_policy, self.principal)):
            self.denied.add(str(kb_id))
            return SearchOutcome("unavailable")
        rows = (await self.session.execute(_KNN, {
            "qvec": "[" + ",".join(repr(float(x)) for x in qvec) + "]", "ws": self.workspace_id, "kb": str(kb_id),
            "model": ref.embedding_model, "threshold": float(threshold), "k": int(top_k),
        })).all()
        return SearchOutcome("ok", tuple(
            Passage(chunk_id=r.chunk_id, kb_id=str(kb_id), document_id=r.doc_id, document_name=r.doc_name,
                    text=r.text, token_count=int(r.token_count), score=float(r.score), meta=dict(r.meta or {}))
            for r in rows))
