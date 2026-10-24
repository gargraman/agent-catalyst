# 04 (LLD) — Knowledge / RAG & Ingestion

> **Parent:** [`../04-rag.md`](../04-rag.md) (the KB shape, ingestion pipeline sketch, chunking, embedding,
> retrieval, injection modes, citations, the live-KB-content decision).
> The parent has the pipeline skeleton and a near-complete `search()`. It leaves `chunk()` as `...`, the
> dedup key / ingest atomicity as prose, `select_binding()` (introduced in the C9 plural-binding fix)
> undefined, and the **per-user doc-ACL** as a one-line P2 bullet. This doc fills those — including the
> concrete per-user "see your own data" design your **B8** decision calls for.

## What this adds over parent `04`

| Gap in `04` | Filled here |
|---|---|
| `chunk(doc, cfg)` body is `...` | §1 — the structure-aware chunking algorithm |
| dedup "on re-sync" + "ingest atomicity/rollback" are prose (scope.md rev3) | §1 — the dedup key `(content_hash, embedding_model, chunk_config)` + the atomic write |
| `select_binding()` (from the C9 plural fix) + `search()` helpers undefined | §2 — `select_binding` + the helper contracts |
| per-user doc-ACL is a single P2 bullet (`§10`) — but **B8** makes it the "see your own data" layer | §3 — the doc-ACL model + retrieval-time filter (the employee-group archetype) |

Python 3.12. Snippets compile (§4). `ChunkConfig`/`RetrievalConfig`/`ingest()`/`search()`/the KNN SQL/the
injection table: **see `04` — not restated.** `VersionSnapshot.retrieval_bindings`:
[`01-data-model.lld.md §1`](01-data-model.lld.md). `JobOut`/202 async: [`08-api-surface.lld.md §5`](08-api-surface.lld.md).
`can_use`/`AccessPolicy`: frontend `access-policy.ts` mirrored in `09`.

---

## 1. Ingestion details (fills `04 §2`/`§4`)

### 1.1 `chunk()` — structure-aware (fills the `...`)

```python
from __future__ import annotations

def chunk(doc, cfg) -> list["Chunk"]:
    """Structure-aware splitter (04 §4). Split on structure FIRST (headings/sections/table rows/code
    blocks), then size each section to cfg.size tokens with cfg.overlap, so a chunk rarely straddles a
    semantic boundary. Each chunk keeps citation meta {source, docId, page, heading} (04 §8)."""
    sections = _split_structure(doc) if cfg.structure_aware else [doc.text]
    out: list[Chunk] = []
    for section in sections:
        toks = tokenize(section.text)
        step = max(1, cfg.size - cfg.overlap)                 # overlap < size (guarded §1.3)
        for start in range(0, len(toks), step):
            window = toks[start:start + cfg.size]
            if not window:
                break
            out.append(Chunk(
                text=detokenize(window), token_count=len(window),
                meta={"source": doc.source, "docId": doc.id, "page": section.page, "heading": section.heading},
            ))
    return out
```

### 1.2 Dedup key + atomic write (fills `04 §2` "dedupe on re-sync" + scope.md rev3 "atomicity/rollback")

The parent dedups on `documents.content_hash` alone — but scope.md rev3 fixed this: a re-ingest with a
**changed embedding model or chunk config** must actually re-embed, so dedup keys on all three.

```python
def ingest_dedup_key(content_hash: str, embedding_model: str, chunk_config: dict) -> str:
    # scope.md rev3: keying on content_hash ALONE would skip re-embedding when the embedding model or chunk
    # config changed (same text, different vectors) — a silent staleness bug. Include all three.
    return sha256_hex({"content": content_hash, "embed_model": embedding_model, "chunk_config": chunk_config})

# Storage + the parent's skip check: persist this key on the document (documents.dedup_key) at ingest. The
# parent §2 loop `if unchanged(raw.content_hash): continue` becomes
# `if documents.dedup_key == ingest_dedup_key(raw.content_hash, kb.embedding_model, kb.chunk_config): continue`
# — so a content-identical doc is skipped ONLY when the embedding model and chunk config also match (else re-embed).

# Atomicity: a re-ingest of a document writes the new chunks in ONE transaction and swaps them for the old
# atomically, so a reader never sees a half-reindexed document (partial chunk set = wrong retrieval):
async def write_chunks_atomic(doc, chunks, vectors, db) -> None:
    async with db.begin():                                    # one transaction
        await db.execute(delete(Chunk).where(Chunk.document_id == doc.id))   # drop the old generation
        db.add_all(_rows(doc, chunks, vectors))                              # insert the new
    # on any exception the transaction rolls back → the old chunks remain; the job is marked failed (04 §2),
    # never a document left with zero or partial chunks.
```

### 1.3 Validation + upload safety

- `chunk_config`: `0 ≤ overlap < size` (else `step ≤ 0` loops forever) — validated when a KB is created
  (`08` `KnowledgeBaseCreate`, `01 LLD §3 ChunkConfig`).
- Uploads: MinIO presigned PUT (`04 §3`, `08`), then **size/type limits + virus scan before parsing**
  (`backend-integration-plan` upload safety) — a malicious upload must never reach a loader.
- `embedding_dim` is **server-derived** from `embedding_model` via the model catalog (`02`), never client-sent
  (`08 §2`); it fixes the `vector(N)` width (`01 §4.4` dimension constraint).

---

## 2. `select_binding()` + retrieval helpers (fills the C9 plural-binding fix)

`search()` (`04 §6`) now iterates a **collection** of bindings. `select_binding` picks the binding for a
given call; the always-on path iterates all.

```python
def select_binding(snapshot, kb_ref: str | None) -> "RetrievalBindingSnap | None":
    """Pick the binding for THIS retrieval call (04 §6, plural bindings). `kb_ref` is the STRUCTURED
    retrieve(kb=...) tool arg (the tool layer resolves a KB name/slug → kb_id), NOT parsed from the query text.
    Returns None when there are no bindings, or >1 are bound and none is named, or the named KB isn't bound —
    the caller surfaces that as an error result to the model (like 03 LLD §4 resolve_binding), never a silent
    wrong-KB retrieval. always_on does NOT call this — it iterates snapshot.retrieval_bindings (03 §4)."""
    bindings = snapshot.retrieval_bindings
    if not bindings:
        return None                                          # guard: empty → no IndexError
    if kb_ref is None:
        return bindings[0] if len(bindings) == 1 else None   # >1 bound and unnamed → ambiguous → caller errors
    return next((b for b in bindings if b.kb_id == kb_ref), None)   # unknown KB → None (not bindings[0])
```

Helper contracts used by `search()` (`04 §6`), named there, defined per module:
- `embed_one(query, embedding_model) -> vector` — one embedding via the gateway embedding path (`04 §5`, cost-logged).
- `pgvector_knn(workspace_id, kb_id, qvec, k, threshold, filters) -> rows` — the `04 §6` KNN SQL, namespace-pruned.
- `trim_to_token_budget(rows, max_ctx_tokens) -> rows` — drop lowest-scored rows until `Σ token_count ≤ budget`.
- `fuse(rows, bm25(...))` / `rerank(name, query, rows)` — **P2** (hybrid + bge reranker, `04 §6`).

---

## 3. Per-user doc-ACL — the "see your own data" layer (B8)

Your **B8** decision: "open to all" = a workspace bound to an **all-employees IdP group** (every employee is a
member via the group claim), and "see your own data" is **per-user data scoping at the retrieval layer**.
Workspace access and *document* visibility are two different gates:

```
Workspace membership   → the all-employees group claim (idp_binding, 01 §4.1 / 18) — "authorized employees only"
KB access_policy        → workspace (everyone in the space may USE the KB) — 09 §5
Per-document ACL (THIS) → scopes WHICH chunks a given caller retrieves — "see your own data"
```

So in an employee-open workspace, everyone can invoke the agent, but each caller only retrieves the documents
they're entitled to. The design (P2+, now concrete):

```python
from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from access_policy import Principal                   # {role, teams, subject?} — access-policy.ts mirror (09)


class DocAcl(BaseModel):
    """Stored on documents.meta.acl and DENORMALIZED onto each chunk's meta for SQL-filterable retrieval.
    Set at ingestion: a per-user upload → owner-scoped; a connector preserves the source's ACL (Confluence
    space perms, Drive sharing); a shared corpus → workspace-visible."""
    model_config = ConfigDict(extra="forbid")        # reject unexpected connector-supplied keys (don't silently drop an ACL field)
    visibility: Literal["workspace", "restricted", "private"] = "workspace"
    owner_subject: str | None = None                 # the owning employee; MUST be set for private/restricted at rest
    teams: list[str] = Field(default_factory=list)   # restricted → these teams (functional-group teams)


def acl_filter(principal: Principal, run_source: str) -> dict:
    """Retrieval-time doc-ACL predicate for THIS caller (merged into a COPY of metadata_filters, 04 §6).
    Visibility is AUTHORITATIVE: workspace → everyone; restricted → owner OR a shared team; private → owner only."""
    # Eval bypasses the KB-grain check (the binding was manage-authorized) but must NOT blanket-bypass per-doc
    # entitlements — else an eval case could exfiltrate another user's owner/private docs (review finding). Eval
    # retrieves ONLY workspace-visible docs (the reproducible, non-personal corpus).
    if run_source == "eval":
        return {"visibility": "workspace"}
    clauses: list[dict] = [{"visibility": "workspace"}]
    if principal.subject:                            # owner clause ONLY for an identified caller — no None==None leak
        clauses.append({"visibility_in": ["restricted", "private"], "owner_subject": principal.subject})
    if principal.teams:                              # team clause matches RESTRICTED docs only (never private)
        clauses.append({"visibility": "restricted", "teams_intersect": principal.teams})
    return {"acl_any": clauses}                      # pgvector_knn translates acl_any/visibility_in/teams_intersect → SQL over chunks.meta->'acl'
```

- `search()` (`04 §6`) merges `acl_filter(principal, ctx.run_source)` into a **COPY** of
  `binding.config.metadata_filters` (the snapshot binding is frozen — never mutate it) before the KNN call;
  `pgvector_knn` translates `acl_any`/`visibility_in`/`teams_intersect` to a SQL `WHERE` over the denormalized
  `chunks.meta->'acl'` (a GIN index on `chunks.meta` keeps it fast within the `(workspace_id, kb_id)` namespace).
- **Denormalize the ACL onto chunks** (not a doc join per query): retrieval is hot; a join per KNN is not.
  Re-ingest rewrites chunk ACLs with the document's (atomic, §1.2).
- **Resolved caller identity.** `acl_filter` needs a resolved `Principal` (`subject` + `teams`); an `executer`
  caller (channel/token) carries both from its federated identity (`09`, `12`), so "see your own data" works
  for a channel user too — but the canonical `RunContext` doesn't carry it yet (amendment below).
- This is the single layer that makes the HR/ops archetype (B8) real: no new visibility *tier*, just
  per-document scoping under an employee-wide workspace.

> ⚠️ **Amendments (2026-10-24 review), folded into `01`/`04`:** (1) `RunContext` (00 §4 / 01 LLD §2) must carry
> a **resolved `principal: Principal`** (subject+teams), not just `principal_id`, so `search()`/`can_use` can
> evaluate per-caller access. (2) **KB access policy is live, not frozen** (04 §9): `RetrievalBindingSnap`
> (01 LLD §1) carries no `kb_access_policy`; `search()` must resolve the KB's *current* policy at invoke
> (the parent `04 §6` `can_use(binding.kb_access_policy, …)` reads that live-resolved policy, not a snap field).
> (3) `select_binding` now takes the structured `kb_ref` and returns `None` on miss — the parent `04 §6`
> `search()` passes the retrieve()-tool's kb arg and surfaces `None` as a model-facing error.

> **Scope:** per-user doc-ACL is **P2+** (`04 §10`, `scope.md` 2026-10-23/-24). P1 ships KB-level access
> (`09 §5`) only; a P1 KB is workspace-visible or `restricted` at the KB grain, not per document. This section
> is the **design** the B8 archetype needs, not P1 scope.

---

## 4. Verification performed

1. **Snippet compile** — every `python` block via `compile()` (same harness as the other LLDs).
2. **Cross-ref** — against `04` read in full: pipeline (`§2`), `ChunkConfig`/`chunk` (`§4`), embedding + dim
   constraint (`§5`), `search`/KNN/`select_binding` (`§6`, incl. the C9 plural fix), injection (`§7`),
   citations (`§8`), live-content decision + parked eval signal (`§9`), P2 doc-ACL (`§10`); `01 LLD §3`
   (`ChunkConfig`/`RetrievalConfig`); `08 LLD §5` (async job); `access-policy.ts`/`09` (`can_use`, `AccessPolicy`);
   `scope.md` B8 (employee-group model) + rev3 (dedup key / atomicity).
3. **B8 realized** — §3 implements "open-to-all = employee-group membership + per-user data scoping," with no
   new public visibility tier (the decision's explicit shape).
4. **No new parent amendment** — the one `04` correction (singular→plural `retrieval_binding`) was already
   folded in C9; this doc builds on it.

---

> **LLD backlog complete (9/9).** `00`,`01`,`02`,`03`,`04`,`05`,`06`,`07`,`08` all have a `.lld.md`. All
> flagged amendments are reconciled into `docs/backend/` (README banner + scope.md 2026-10-24).
