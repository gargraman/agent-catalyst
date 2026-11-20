# HTTP Contract — 024 Minimal RAG (M7)

All routes are under the console plane (`/api/v1`, session/bearer auth, `X-Workspace-Id`), use the standard error
envelope (`api/errors.py`), RLS-scoped sessions, and the existing deny conventions: **403** missing RBAC permission
(before any resource load); **404** for a KB/document the caller cannot USE (byte-identical to absent, P5); **403**
when usable but not manageable (013 `ensure_manage`). OpenAPI + `frontend/src/lib/api/schema.ts` regenerated (FR-033).

## Documents & ingestion

| Method + path | Permission · rate class · gates | Success | Errors |
|---|---|---|---|
| `POST /knowledge-bases/{kb_id}/documents` (multipart `file`) | `knowledge.manage` · `ingest` · spend cap · KB MANAGE · KB not archived | **202** `{document: DocumentOut, job: IngestionJobOut}` | 400 `input.invalid` (`unsupported_type` / `content_mismatch` / empty file / bad name), 413 `upload.too_large`, 402 `budget.exceeded`, 409 `kb.archived`, 429 |
| `GET /knowledge-bases/{kb_id}/documents?limit&cursor&status` | `view` · KB USE | 200 `Page[DocumentOut]` | 404 |
| `GET /knowledge-bases/{kb_id}/documents/{doc_id}` | `view` · KB USE | 200 `DocumentOut` | 404 |
| `DELETE /knowledge-bases/{kb_id}/documents/{doc_id}` | `knowledge.manage` · `default` · KB MANAGE | **204** (chunks gone in the same txn; object deleted after commit; KB `index_version` +1) | 404, 409 `document.processing` (delete while a job is processing → retry later) |
| `POST /knowledge-bases/{kb_id}/documents/{doc_id}/retry` | `knowledge.manage` · `ingest` · spend cap · KB MANAGE | 202 `IngestionJobOut` | 409 `job.not_retryable` (latest job not failed+retryable) |
| `POST /knowledge-bases/{kb_id}/reingest` | `knowledge.manage` · `ingest` · spend cap · KB MANAGE | 202 `{jobs: [IngestionJobOut]}` (one per document) | 409 `kb.archived` |
| `GET /ingestion-jobs?kb_id&status&limit&cursor` | `view` | 200 `Page[IngestionJobOut]` (**real**, replaces the deferred stub; filtered to KBs the caller can USE) | |
| `GET /ingestion-jobs/{job_id}` | `view` · KB USE | 200 `IngestionJobOut` | 404 |

`Idempotency-Key` honoured on `POST …/documents` (same in-flight guard as other creates). Re-uploading an existing
`name` replaces that document (new generation, D4); unchanged bytes + same embedding model + chunk config ⇒ job ends
`ready` with `skipped_unchanged: true`, `cost_usd: "0.000000"`.

### `DocumentOut`

```json
{ "id": "uuid", "kb_id": "uuid", "name": "handbook.pdf", "media_type": "application/pdf",
  "size_bytes": 482113, "status": "ready", "chunk_count": 42, "live": true,
  "injection_flags": {"instruction_override": 1}, "error": null,
  "created_by": "user:…", "created_at": "…", "updated_at": "…" }
```

`live` = `active_generation > 0`. `storage_key` / `content_hash` are never returned.

### `IngestionJobOut` (extends the existing stub shape: `id, source, kb, status, docs, chunks` kept for FE compat)

```json
{ "id": "uuid", "kb": "uuid", "document_id": "uuid", "source": "handbook.pdf", "kind": "upload",
  "status": "processing", "docs": 1, "chunks": 42, "progress": {"done": 20, "total": 42},
  "skipped_unchanged": false, "cost_usd": "0.000412", "cost_is_estimate": true,
  "error": null, "created_at": "…", "started_at": "…", "finished_at": null }
```

`error` = `{code, message, retryable}`; `message` is non-secret (no provider payload, no key, no storage path).

## Knowledge bases (existing routes; contract changes)

- `KnowledgeBaseOut` gains `document_count`, `chunk_count`, `last_ingested_at`, `embedding_provider` (disclosure,
  FR-032). `ingestion_status` becomes the derived value `empty|ingesting|ready|partial|stale` (data-model §1) instead
  of `"deferred"`.
- `POST /knowledge-bases` / `PATCH`: `embedding_model` must be a supported embedding profile (1024-dim), otherwise
  **400** `input.invalid` with `detail.supported_models`. `embedding_dim`, if sent, must match. `chunk_config`
  validated (data-model §1). PATCH of `embedding_model`/`chunk_config` additionally marks documents `stale`
  (existing drift flagging unchanged).

## Agent versions — retrieval binding authoring (research D12)

`POST /api/v1/agents/{agent_id}/versions` body gains optional:

```json
{ "label": "v3",
  "retrieval_bindings": [
    { "kb_id": "uuid", "injection": "always_on",
      "retrieval_config": {"top_k": 6, "similarity_threshold": 0.2, "max_ctx_tokens": 2000} } ] }
```

- Omitted ⇒ copies the latest version's retrieval bindings (G2 fix). `[]` ⇒ no bindings.
- Per binding: KB must exist, not archived, caller can MANAGE it (else 404 / 403 per conventions); config bounds per
  data-model §5 (400 otherwise); max 5 bindings per version; duplicate `kb_id` → 400.
- `VersionOut` gains `retrieval_bindings: [{kb_id, kb_name, injection, retrieval_config, embedding_model,
  kb_index_version, restricted}]`; `GET /agents/{id}` detail projects the active version's bindings.
- **Masking (FR-016a / H2)**: a binding whose KB the viewer cannot USE renders as
  `{kb_id: null, kb_name: null, embedding_model: null, kb_index_version: null, restricted: true, injection,
  retrieval_config}`; otherwise `restricted: false`.
- **Refresh disclosure (FR-031 / C2)**: `VersionOut` and agent detail gain
  `knowledge_refreshed_since_eval: bool | null` and `refreshed_kb_ids: [str]`. Computed from the version's latest
  eval run (`runs.source='eval'`, newest `created_at`): for each `usage.retrieval.bindings[]` entry, refreshed iff the
  KB's current `index_version` ≠ the recorded `kb_index_version`. `null` when the version has no bindings, no eval
  run, or the eval run predates retrieval facts. `refreshed_kb_ids` is masked like bindings (restricted KBs are
  counted in the boolean but not listed). Drift flag (`needs_reeval`) is NOT set by this (signal parked).

### Concurrency (H3)

At most one active job (`queued|processing`) per document, enforced by the partial unique index
`ingestion_jobs_one_active_per_doc` (data-model §4). Re-upload of an existing name, `retry`, and `DELETE` while a job
is active → `409 document.processing`. `POST …/reingest` enqueues only documents without an active job and returns
`{enqueued: n, skipped_active: m}`. A unique violation raised by a race is mapped to the same 409.

## Invoke (both planes) — response additions

SSE gains one frame, emitted once after the final `text_delta`s and before `usage`, only when the version has bindings:

```
event: citations
data: {"knowledge_used": true,
       "citations": [{"id": "c1", "kb_id": "…", "document_id": "…", "document_name": "handbook.pdf",
                      "chunk_id": "…", "score": 0.83, "page": 4, "heading": "Leave policy"}],
       "knowledge_status": [{"kb_id": "…", "status": "used"}]}
```

`knowledge_status[].status ∈ used|no_match|unavailable|degraded`. **Published plane**: `knowledge_status` is omitted
(caller sees only citations of what was used). A denied KB is `unavailable` on the console plane, same as archived,
and its entry is masked `{kb_id: null, status: "unavailable", restricted: true}` (FR-016a / H2). New terminal status via the
existing `done` frame: `bounded_exit` (tool-mode search budget exhausted).

## Evaluations

`POST /api/v1/agents/{id}/eval-runs` **access gate (FR-016a / H1)**: before any case runs, the caller must be able to
USE every KB bound to the version under evaluation; otherwise `403 eval.knowledge_forbidden` (no run, no cost). The
KB ids are not echoed. Retrieval inside the run then reads the frozen snapshot with `bypass_access=True` (the gate
already proved access; a mid-run policy change does not fork results).

Case schema: `assertions[].kind` accepts `"retrieved_source"` with `detail` =
document id or exact name. The result adds `retrieval_hit_rate` (null when no case asserts retrieval) and per failing
case `retrieval_hit`.

## Workspace spend

`GET /api/v1/workspace/spend` month-to-date now includes ingestion embedding cost
(`Σ runs.cost_usd + Σ ingestion_jobs.cost_usd`); response gains a `breakdown: {runs, ingestion}` (strings, 6 dp).

## Metrics (`GET /metrics`, aggregate-only)

`ac_ingestion_jobs_total{status}` · `ac_ingestion_seconds` (histogram) · `ac_retrieval_seconds` (histogram) ·
`ac_retrieval_passages_withheld_total{category}` · `ac_injection_detections_total{checkpoint,category}`
(`checkpoint ∈ ingest|retrieve`) · `ac_ingestion_reaped_total`. No tenant/content labels.
