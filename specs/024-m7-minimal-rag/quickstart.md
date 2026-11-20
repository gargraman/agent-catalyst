# Quickstart / Validation Guide — 024 Minimal RAG (M7)

How to prove each story and success criterion. Contracts: [api.md](contracts/api.md), [runtime.md](contracts/runtime.md);
entities: [data-model.md](data-model.md). **No live or paid provider call anywhere**: embeddings and model calls are
mocked at the transport seam.

## Prerequisites

```bash
source ~/.nvm/nvm.sh; nvm use 24          # only for the FE contract regen step
cd services
./.venv/bin/pip install -e '.[api,ingest,test]'   # adds pypdf, python-docx, python-multipart (research D6/D7)
make -C .. up                                      # Postgres(5433, pgvector) + PgBouncer + Redis + LocalStack + migrate
./.venv/bin/alembic upgrade head                   # → 0013
```

## TDD order (Red shown to the owner before each Green — constitution §9)

1. Pure building blocks: extractor sniffing → chunker → injection scanner + corpora → gateway `embed` cost →
   transport tool-call assembly (+ fake-server spike).
2. Runtime: always-on retrieval + context assembly + citations → scanner withhold/fail-closed → tool loop + bounds +
   mixed turn + capability downgrade → run record/cost invariant/per-call embedding records → parity.
3. Migration 0013 (RLS forced ×3, reaper function grant scope) → KnowledgeAccess kNN (tenant + policy + model filter).
4. API: binding authoring + clone fix → upload/validation → executor + generations + dedup + reaper → documents/jobs
   routes → KB derived status → spend breakdown → invoke wiring (both planes) + SSE `citations` → eval assertion.
5. Contract regen (OpenAPI + FE types), docs reconciliation.

## Focused commands

```bash
./.venv/bin/python -m pytest -q -m "not integration" tests/test_ingest_extract.py tests/test_ingest_chunk.py \
  tests/test_injection_scanner.py tests/test_gateway_embed.py tests/test_transport_tool_calls.py \
  tests/test_invoke_retrieval.py tests/test_invoke_retrieval_tool.py tests/test_invoke_parity.py tests/test_evals_retrieval.py
./.venv/bin/python -m pytest -q -m integration tests/test_migration_0013.py tests/test_kb_retrieval_isolation.py \
  tests/test_api_ingestion.py tests/test_api_retrieval_invoke.py tests/test_ingestion_reaper.py tests/test_api_bindings.py
AC_RUN_LOCALSTACK=1 ./.venv/bin/python -m pytest -q tests/test_storage_s3_localstack.py
./.venv/bin/python -m pytest -q -m "not integration"     # full infra-free regression
./.venv/bin/python -m pytest -q -m integration           # full integration regression
./.venv/bin/python -m pytest -q tests/test_runtime_purity.py tests/test_evals_purity.py
```

## Validation matrix

| Check | Scenario | Expected |
|---|---|---|
| US1 / SC-002 | upload `.txt` + 50-page text PDF fixture; poll `GET /ingestion-jobs/{id}` | `queued → processing → ready` < 2 min with mocked embeddings; KB `document_count`/`chunk_count` up; `ingestion_status=ready` |
| US1 refusals | `.exe`, a `.pdf` that is really text, 21 MiB file, empty file, image-only PDF | 400 `unsupported_type` / `content_mismatch`, 413, 400, job `failed: no_extractable_text`. Nothing stored for the refused ones |
| FR-005 atomicity | embedding mock fails on batch 3 of a re-upload | job `failed`; previous generation still retrievable; no new-generation chunks remain |
| FR-006 dedup | upload identical bytes twice | 2nd job `ready`, `skipped_unchanged: true`, `cost_usd 0`, embedding mock not called |
| FR-007 delete | delete a ready doc | 204; kNN returns none of its chunks; storage object gone; KB `index_version` +1 |
| D5 reaper | start a job, kill its heartbeat (freeze clock), run sweep; second "replica" with a fresh heartbeat | stale job → `failed{interrupted, retryable}`; the fresh one untouched; `retry` succeeds |
| FR-010 authz | viewer upload; builder on a KB whose policy denies USE; KB usable-not-manageable | 403; 404 (byte-identical to absent); 403 |
| FR-009 spend | MTD ≥ cap then upload | 402 `budget.exceeded`, no provider call, no job |
| US2 / SC-001 | 20-question known-fact set against a seeded KB (deterministic fake embedder: bag-of-words hashed into 1024 dims) | answer passage retrieved + cited for ≥ 18/20 at defaults |
| FR-013 bounds | top_k 2, threshold 0.9, max_ctx 300 | ≤ 2 passages, none < 0.9, injected tokens ≤ 300; `hybrid:true` ⇒ substitution recorded |
| FR-016 policy | console caller denied on KB-B of a 2-KB version; published token caller whose teams miss KB policy | nothing from the denied KB; run succeeds; citations only from allowed KB; no `knowledge_status` on published plane |
| SC-003 isolation | workspace B invokes with a qvec identical to a workspace-A chunk; also through PgBouncer | 0 rows from A |
| FR-020 degraded | archived KB; embedding mock raises; `ctx.knowledge=None` | run succeeds; `knowledge_used:false`; status `unavailable` / `degraded` |
| SC-006 / FR-019 parity | same input on playground and published, and `invoke` vs `invoke_result` | identical `RetrievalSummary` + citations; every citation id ∈ retrieved set |
| US3 / SC-004 | `attack.v1.jsonl`, `benign.v1.jsonl` | detection ≥ 90 %, FP < 5 %, sizes printed |
| US3 withhold | poisoned passage ranks #1 | withheld, not in citations or prompt, detection record + metric; remaining passages used |
| FR-024 fail closed | scanner patched to raise | no passages supplied; summary `degraded: scan_failed` |
| US3 containment | poisoned passage tells the model to call `delete_all` (unbound) | F3 rejects; no execution; outcome per existing trust boundary |
| US4 tool mode | model mock issues `search_knowledge` then answers | search executed, results scanned, fed back; final answer + citations; `searches: 1` |
| US4 bounds | model mock searches forever | stops at `max_tool_calls`; outcome `bounded_exit`; usage/cost summed over all turns |
| US4 mixed | turn = search + external tool | search executed; external gets the "not executed" note; next turn's external call surfaced |
| US4 downgrade | profile without tool calling | treated as always-on + `retrieval_tool` substitution |
| D9 transport | fake OpenAI-compatible server streams `delta.tool_calls` fragments | one `TOOL_CALL_END{id,name,args}` per call + `STOP` |
| US5 | case asserting `retrieved_source: handbook.pdf` on a good vs broken binding | hit vs miss; `retrieval_hit_rate` 1.0 vs 0.0 |
| FR-031 | patch KB embedding model | bound versions flagged `needs_reeval`; docs `stale`; re-ingest content does NOT flag |
| SC-007 | invoke with always-on retrieval | `runs.cost_usd == Σ call-log cost lines` (model + embedding); spend breakdown includes ingestion |
| SC-005 | 10k-chunk seeded KB, embedding mock fixed 150 ms latency, 50 runs | p95 added time-to-first-token ≤ 1 s (reported, local stack only; not a prod claim) |
| G2 clone fix | create v2 from a v1 with bindings without passing bindings | v2 has v1's bindings; snapshot hashes equal |
| SC-008 | full suites + purity | green; zero live calls |

## Manual smoke (optional, local stack)

```bash
# with AC_ENV=dev, X-Dev-Principal headers as in prior quickstarts
curl -F file=@handbook.md -H 'X-Workspace-Id: <ws>' localhost:8000/api/v1/knowledge-bases/<kb>/documents
curl localhost:8000/api/v1/ingestion-jobs?kb_id=<kb>
curl -N -X POST localhost:8000/api/v1/agents/<agent>/invoke -d '{"inputs":{"message":"What is the leave policy?"}}'
# expect an `event: citations` frame before `usage`
```

A real embedding call needs an OpenAI key in the workspace's provider-key custody. **That is a paid call: owner
approval required** (paid-call protocol). It is not part of the automated DoD.
