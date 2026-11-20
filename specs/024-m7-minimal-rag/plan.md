# Implementation Plan: Minimal RAG — Ingestion, Retrieval, Citations + Indirect-Injection Scanning (M7)

**Branch**: `feat/backend-m0-foundation` (slice 024; no per-feature branch; setup script reported `024-m7-minimal-rag`) | **Date**: 2026-11-14 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/024-m7-minimal-rag/spec.md` (clarified 2026-11-14: Q1 A, Q2 A, Q3 A)

## Summary

Make knowledge real end-to-end for P1 and close finding F6, without new infrastructure:

1. **Ingestion (US1)**: multipart upload → validated (type sniff, 20 MiB streaming cap) → S3 (workspace-prefixed)
   → durable job → **in-process bounded background executor** with a heartbeat lease and a cross-replica-safe
   `SECURITY DEFINER` reaper (Q3 = A) → extract (pypdf / python-docx / stdlib) → structure-aware chunking → batched
   embeddings through a **new gateway `embed()`** (our pricing, provisional ⇒ estimate) → `kb_chunks` (pgvector
   HNSW, 1024-dim) written as a **new generation**, atomically flipped live. Dedup on content + model + chunk config.
2. **Retrieval + citations (US2)**: the **runtime** owns the retrieval step (query embed → injected `KnowledgeAccess`
   kNN, tenant + current-policy + model filtered → scan → trim → delimited context → citations), so playground,
   published and eval share one path (parity test extended). Embedding cost folds into the run cost.
3. **Injection scanning (US3)**: deterministic normalize-then-match scanner at ingest (flags) **and** at retrieval
   (withhold + record, Q1 = A; scanner error ⇒ withhold all, fail closed). Versioned attack/benign corpora measure
   SC-004. Containment stays F3 tool-call enforcement + "retrieved text is data" rendering.
4. **Retrieval-as-a-tool (US4, Q2 = A)**: a platform-owned `search_knowledge` tool, the **only** tool the runtime
   executes, bounded by `max_tool_calls` (now enforced for it) + deadline, `bounded_exit` on exhaustion, capability
   downgrade to always-on with a substitution. Requires the transport to emit real streamed tool calls (G3).
5. **Eval (US5)**: `retrieved_source` assertion + `retrieval_hit_rate`; eval retrieval bypasses caller ACLs per design.
6. **Gaps closed along the way**: binding authoring on `POST …/versions` (G1) and the clone-drops-bindings bug (G2).

## Technical Context

**Language/Version**: Python 3.12 (services); TypeScript only for regenerated OpenAPI types (no FE behaviour change).
**Primary Dependencies**: existing FastAPI, SQLAlchemy 2.0 async, asyncpg, litellm (transport, now also
`aembedding`), boto3 (now app-side for S3, lazily imported), prometheus-client. **New (3)** in a new `[ingest]`
extra: `pypdf`, `python-docx`, `python-multipart` (research D6/D7). No `pgvector` Python package (vector literals),
no LangChain/unstructured (decision-log delta, D7).
**Storage**: Postgres + `vector` extension. Migration **0013**: extension, `kb_documents`, `kb_chunks`
(`vector(1024)`, HNSW cosine), `ingestion_jobs`, `knowledge_bases.last_ingested_at`, `ingestion_reap_stale()`
SECURITY DEFINER. Objects in S3 `ac-uploads` (LocalStack locally; a `local` in-memory/disk backend for unit tests).
**Testing**: pytest + pytest-asyncio; httpx ASGITransport; transport seam mocked (chat + embeddings); a deterministic
fake embedder for retrieval-quality tests; fake OpenAI-compatible server spike for streamed tool calls; integration on
compose Postgres (5433) incl. the PgBouncer path; LocalStack S3 opt-in (`AC_RUN_LOCALSTACK=1`).
**Target Platform**: Linux server (`api` service), local compose stack.
**Project Type**: web service (`services/`) + pure libraries (`runtime/`, `evals/`, new `ingestion/`).
**Performance Goals**: SC-002 50-page doc → ready < 2 min (mocked embed latency); SC-005 retrieval adds ≤ 1 s p95 TTFT
at 10k chunks (local measurement, reported not claimed for prod).
**Constraints**: runtime + evals purity (P6); fail-closed security controls (P3); no content in logs/runs (P5,
023 rule); no live/paid calls (FR-035); single vector dimension (D3); upload ≤ 20 MiB; ingest concurrency ≤ 2 per
process; reaper must not kill other replicas' jobs (D5).
**Scale/Scope**: internal pilot: tens of KBs, ≤ 1,000 docs/KB, ≤ ~1M chunks per deployment (HNSW comfortably; 04 §6
ceiling recorded).

## Constitution Check

*GATE: passes before Phase 0; re-checked after Phase 1 design (below).*

| Principle | Gate | Status |
|---|---|---|
| **1 Verifiable outcome** | user-visible result + honest states | PASS: upload status lifecycle, actionable refusals, citations, explicit `knowledge_used/knowledge_status`; nothing mocked is presented as live (G3 closed rather than shipping a mock-only tool mode). |
| **2 Scope / phases** | P1-min per scope 2026-10-04; non-goals explicit | PASS: connectors/hybrid/rerank/doc-ACL/classifier/OCR/malware-scan/FE wiring out (spec Non-Goals). One **decision-log delta** to record at DoD: LangChain+unstructured → pypdf/python-docx (D7); presigned upload deferred (D6). |
| **3 Bounded authority, deterministic controls** | retrieved docs untrusted; bounded loops; fail closed | PASS: deterministic scanner; withhold-on-detect; scanner error ⇒ withhold all; only the platform search tool executes; search count + deadline + turn cap; F3 still gates every tool call; retrieved text rendered as data outside the system prompt. |
| **4 Evidence about behaviour** | retrieval evaluated, not just generation | PASS: `retrieved_source` assertion + hit rate; corpora-measured scanner rates with sample sizes; no judge change; publish gate stays advisory. |
| **5 Data / tenancy** | classification, purpose, access, retention, external processing | PASS: data-model §8 per item; RLS forced on 3 new tables + explicit `workspace_id` filters; cross-tenant reaper is one narrow SECURITY DEFINER fn (0008 pattern); KB policy evaluated live at invoke; embedding provider disclosed per KB; delete removes bytes + chunks; no passage text in logs/runs. |
| **6 Reproducible versions** | binding frozen; content live by decision | PASS: bindings (KB, model, config) in `snapshot_hash`; content live per 2026-10-22; `kb_index_version` recorded on runs for traceability; content-refresh eval signal stays **parked** (B7). |
| **7 Honest providers/cost** | own pricing; substitutions; estimates labelled | PASS: embedding cost from our profile, provisional ⇒ estimate; P2 levers + tool-mode downgrade surface as substitutions; run cost = Σ turns + Σ embeds (invariant test); ingestion cost in spend. |
| **8 Operate; failures visible** | degraded explicit; telemetry | PASS: per-binding `degraded/unavailable`; job errors with codes; reaper metric; retrieval/ingest histograms; detections counted. |
| **9 Spec / test-first / evidence** | Red→Green with owner-confirmed Red | PASS (planned): quickstart TDD order; every behaviour task gets a named Red test; no exception to test-first requested. |
| **Paid-call protocol** | no live spend | PASS: all provider calls mocked; a real embedding smoke is owner-gated and outside DoD. |

**No violations.** Complexity is real but each piece is required by a spec FR; see Complexity Tracking for the
choices that look heavier than the spec implies.

**Post-design re-check (after data-model + contracts):** still PASS. Design surfaced three items that *strengthen*
gates rather than violate them: G2 (clone dropped bindings, P6 reproducibility bug, now fixed), G3 (tool calls
mock-only, P1 honesty, now closed), and the reaper lease (P8 safe failure across replicas). One new cross-tenant
code path (`ingestion_reap_stale`) is confined to a single SECURITY DEFINER function with an integration test on
its grant scope (P5).

## Project Structure

### Documentation (this feature)

```text
specs/024-m7-minimal-rag/
├── spec.md
├── plan.md              # this file
├── research.md          # G1–G7 gaps + D1–D19 decisions
├── data-model.md        # tables, state machine, runtime/eval types, data classification
├── quickstart.md        # TDD order, focused commands, validation matrix
├── contracts/
│   ├── api.md           # HTTP: documents, jobs, bindings, citations frame, spend, metrics
│   └── runtime.md       # KnowledgeAccess port, retrieval step, tool loop, events, scanner, gateway.embed
├── checklists/
│   └── requirements.md
└── tasks.md             # /speckit-tasks (not created here)
```

### Source Code (repository root)

```text
services/
├── runtime/
│   ├── context.py                    # EDIT: knowledge (KnowledgeAccess|None), embedding_api_key, record_detection
│   ├── run.py                        # EDIT: RetrievalSummary/BindingSummary, Citation, RunRecord.retrieval, InvokeResult.citations
│   ├── invoke.py                     # EDIT: always-on step, tool loop (search_knowledge), CITATIONS event, embed accounting
│   ├── io.py                         # EDIT: context-message insertion; synthetic search tool in build_normalized_request
│   ├── retrieval/                    # NEW package
│   │   ├── port.py                   #   KnowledgeAccess Protocol, SearchOutcome, Passage
│   │   ├── step.py                   #   retrieve(): embed → search → scan → merge/trim → summary/citations
│   │   └── render.py                 #   delimited, escaped context rendering (D13)
│   ├── guardrails/injection.py       # NEW: deterministic scanner (D11)
│   └── providers/
│       ├── ir.py                     # EDIT: EventType.CITATIONS; EmbeddingResult
│       ├── gateway.py                # EDIT: embed()
│       ├── transport.py              # EDIT: embed() seam (litellm aembedding)
│       ├── _common.py                # EDIT: streamed tool-call assembly + STOP (D9)
│       ├── profiles.py               # EDIT: embedding profile fields (kind/dimension/max_batch)
│       └── profiles_data/openai__text-embedding-3-small.json   # NEW (provisional)
├── ingestion/                        # NEW pure package (no web/DB imports; purity test added)
│   ├── extract.py                    #   sniff + per-type extractors (D7)
│   └── chunk.py                      #   structure-aware splitter (D8)
├── evals/
│   ├── types.py                      # EDIT: retrieved_source, retrieval_hit(_rate)
│   └── runner.py / deterministic.py  # EDIT: evaluate retrieved_source against InvokeResult.citations
├── api/
│   ├── storage/                      # NEW: registry (local|s3, lazy boto3), put/get/delete
│   ├── ingest_executor.py            # NEW: bounded asyncio executor, heartbeat lease, reaper loop (lifespan)
│   ├── ingest_pipeline.py            # NEW: job steps: load → extract → chunk → scan → embed batches → write gen → flip
│   ├── knowledge_access.py           # NEW: KnowledgeAccess impl (policy, archived, kNN SQL)
│   ├── spend.py                      # EDIT: + Σ ingestion_jobs.cost_usd, breakdown
│   ├── metrics.py                    # EDIT: ingestion/retrieval/detection metrics
│   ├── settings.py                   # EDIT: AC_STORAGE_PROVIDER, AC_UPLOADS_BUCKET, AC_INGEST_MAX_FILE_BYTES,
│   │                                 #       AC_INGEST_CONCURRENCY, AC_INGEST_STALE_AFTER_S, AC_EMBEDDING_PROVIDER_KEY_SOURCE
│   ├── main.py                       # EDIT: lifespan starts executor + reaper loop; stops cleanly
│   ├── models.py                     # EDIT: KbDocument, KbChunk, IngestionJob; KnowledgeBase.last_ingested_at
│   ├── sse.py                        # EDIT: citations frame
│   ├── schemas/{knowledge,agents,invoke,evals,workspaces}.py   # EDIT: DTOs per contracts/api.md
│   └── routers/
│       ├── knowledge.py              # EDIT: documents/jobs/reingest routes; derived status; embedding-model validation
│       ├── agents.py                 # EDIT: retrieval_bindings on POST versions; clone copies bindings (G1/G2)
│       ├── invoke.py                 # EDIT: ctx.knowledge + embedding key on both planes
│       └── evals.py                  # EDIT: ctx.knowledge(bypass_access=True)
├── migrations/versions/0013_rag_documents_chunks_jobs.py       # NEW
├── pyproject.toml                    # EDIT: [ingest] extra; include ingestion* already listed
└── tests/
    ├── fixtures/injection/{attack,benign}.v1.jsonl             # NEW corpora
    ├── fixtures/docs/                                          # NEW small txt/md/html/pdf/docx + image-only pdf
    ├── test_ingest_extract.py · test_ingest_chunk.py · test_injection_scanner.py · test_gateway_embed.py
    ├── test_transport_tool_calls.py · test_invoke_retrieval.py · test_invoke_retrieval_tool.py · test_evals_retrieval.py
    ├── test_invoke_parity.py (EDIT) · test_call_record_emission.py (EDIT cost invariant) · test_ingestion_purity.py
    └── integration: test_migration_0013.py · test_kb_retrieval_isolation.py · test_api_ingestion.py ·
        test_api_retrieval_invoke.py · test_ingestion_reaper.py · test_api_bindings.py · test_storage_s3_localstack.py
frontend/openapi.json, frontend/src/lib/api/schema.ts          # REGEN only
docs/: backend/04 (as-built), backend/03 §8 (tool loop as-built), scope.md decision log (D6/D7 deltas),
       implementation-plan.md M7 row, backend-integration-status.md (KB/documents/ingestion-jobs BUILT, FE MOCK)
```

**Structure Decision**: Pure logic in pure packages (`runtime/retrieval`, `runtime/guardrails/injection.py`,
`ingestion/`, evals), all DB/S3/HTTP concerns in `api/`, joined by injected ports (`KnowledgeAccess`, sinks), the
same seam style as `record_run`/`record_call`. `ingestion/` was already reserved in `pyproject.toml`'s package list.

## Key design decisions (detail in research.md)

1. Runtime orchestrates retrieval; API supplies only data access (D1). Parity and cost stay in one place.
2. Gateway `embed()` with our own embedding pricing; pilot model `text-embedding-3-small`, single 1024 dimension (D2/D3).
3. Generations for atomic per-file replace; no `data_sources` table in P1 (D4).
4. In-process executor **with a heartbeat lease**; reaper via a narrow SECURITY DEFINER function (D5).
5. Multipart upload with a streaming size cap; light extractors instead of LangChain/unstructured (D6/D7).
6. Real streamed tool calls in the transport before tool mode ships (D9).
7. `search_knowledge` is the only executed tool; bounded; mixed-turn rule; capability downgrade (D10).
8. Scanner at ingest + retrieval, withhold-on-detect, fail closed, corpus-measured (D11).
9. Binding authoring + clone fix (D12); retrieved text rendered as escaped data (D13); citations as a runtime event (D14).
10. Ingestion cost is its own ledger, summed into spend (D16).

## Requirements traceability

| FR | Design | Verified by (quickstart row / test) |
|---|---|---|
| 001–002 | D6 multipart + streaming cap, D7 sniffing; contracts/api.md upload | US1 refusals · `test_ingest_extract`, `test_api_ingestion` |
| 003, 011 | `ingestion_jobs` + derived KB status (data-model §1/§4); jobs routes | US1 / SC-002 · `test_api_ingestion` |
| 004 | D7 extract → D8 chunk → D2 embed → `kb_chunks` | US1 · `test_ingest_chunk`, `test_api_ingestion` |
| 005 | D4 generations | FR-005 atomicity row |
| 006 | dedup on hash + model + chunk config (D4/contracts) | FR-006 row |
| 007 | delete/retry routes; `index_version` +1 (D18) | FR-007 row |
| 008 | D5 executor + lease + reaper | D5 reaper row · `test_ingestion_reaper` |
| 009 | D2 cost, D16 spend ledger | FR-009 row, SC-007 row |
| 010 | existing RBAC/013/016 deps on new routes | FR-010 row |
| 012, 020 | contracts/runtime.md §2–3 always-on + degraded | US2 / FR-020 rows · `test_invoke_retrieval` |
| 013–014 | D12 config bounds, P2 levers ⇒ substitution; model filter in kNN | FR-013 row |
| 015 | RLS forced + explicit filters (data-model §3) | SC-003 row · `test_kb_retrieval_isolation` |
| 016 | `KnowledgeAccess` live policy; eval bypass (D1/D17) | FR-016 row |
| 017 | D14 CITATIONS event + SSE frame | SC-006 row |
| 018 | D15 RetrievalSummary in `runs.usage`; embedding call records | SC-007 row · `test_call_record_emission` |
| 019 | runtime-owned retrieval (D1); parity test extended | SC-006 / FR-019 row · `test_invoke_parity` |
| 021–022 | D11 scanner at ingest + retrieve | US3 / SC-004 rows · `test_injection_scanner` |
| 023 | withhold + detection record + metric (Q1 = A) | US3 withhold row |
| 024 | scanner error ⇒ withhold all | FR-024 row |
| 025 | D13 rendering; F3 unchanged | US3 containment row |
| 026 | versioned corpora (D11) | SC-004 row |
| 027–029 | D10 tool loop; D9 transport | US4 rows · `test_invoke_retrieval_tool`, `test_transport_tool_calls` |
| 030 | D17 `retrieved_source` + hit rate | US5 row · `test_evals_retrieval` |
| 031 | existing drift flagging; D18 stale | FR-031 row |
| 032 | data-model §8 | review + `test_api_ingestion` (no storage key/content in responses/logs) |
| 033 | OpenAPI + FE type regen | Phase G; `npm run gen:api` + `npm run build` |
| 034 | pure packages + ports | `test_runtime_purity`, `test_evals_purity`, new `test_ingestion_purity` |
| 035 | transport seam mocked everywhere | SC-008 row |

## Delivery slicing (for /speckit-tasks)

The plan is one feature, but tasks should be ordered so each phase ends green and demoable:
**A** pure blocks (extract/chunk/scanner/embed/transport) → **B** migration + storage + executor + upload/doc/job
APIs (US1 done) → **C** binding authoring + always-on retrieval + citations + invoke wiring (US2 done) → **D** scanner
wired at both checkpoints + containment tests (US3 done) → **E** tool loop (US4) → **F** eval assertion (US5) →
**G** contract regen + docs. If the slice proves too large mid-build, **E + F** split cleanly into a follow-up (024b)
without touching A–D.

## Complexity Tracking

| Choice that looks heavier | Why needed | Simpler alternative rejected because |
|---|---|---|
| Heartbeat lease + SECURITY DEFINER reaper | Q3 = A requires "never stuck"; multi-replica API is the deployment shape | A startup "fail all processing jobs" sweep kills other replicas' live jobs |
| Transport tool-call assembly in this milestone (G3) | Q2 = A tool mode must work against a real provider wire | Shipping tool mode mock-only violates constitution P1 (mock presented as implemented) |
| Document generations | FR-005 atomicity + replace-without-gap | One txn per file holds a DB transaction open across many provider calls |

## Analyze remediations (2026-11-14, owner-approved)

| ID | Change | Where |
|---|---|---|
| C1 | Dev/held-out split for scanner corpora and known-question set; rates reported on held-out only | spec FR-026/SC-001/SC-004; tasks T041, T050–T052, T056, T060 |
| C2 | `knowledge_refreshed_since_eval` + `refreshed_kb_ids` on version/agent detail, computed from the latest eval run's recorded `kb_index_version`; drift flag untouched | spec FR-031; contracts/api.md; tasks T081–T082 |
| H1 | Eval start gate: caller must USE every bound KB (`403 eval.knowledge_forbidden`); bypass only inside the run | spec FR-016a; contracts; tasks T048, T070 |
| H2 | Bindings and `knowledge_status` for KBs the viewer can't USE are masked (`restricted: true`) | spec FR-016a; contracts; tasks T035, T040, T043 |
| H3 | One active job per document (partial unique index + 409 `document.processing`; reingest skips active) | spec edge case; data-model §4; tasks T006, T011, T022, T030 |
| H5 | Hourly per-workspace orphan-object sweep (no cross-tenant SQL) | data-model §2; tasks T079–T080 |
| H6 | US4 "cost bound" removed (bounds = search count, turn budget, deadline) | spec US4 |
| H4 | **Decided 2026-11-14**: dimension 1024; customer KB content embedded ONLY on AWS Bedrock (Titan v2); OpenAI direct DEV-ONLY with synthetic data; enforced by `AC_EMBEDDING_ALLOWED_PROVIDERS` (prod = bedrock). Logged in `docs/scope.md` | research D2/D3; tasks T083–T086 |
| R-SEGV / R-PIN / R-CA | New from the live check: intermittent segfault on the macOS dev host; litellm pin is `<2` not `1.106.x` (image has 1.109.1); container TLS blocked by an intercepting CA | research D2 |

## Risks & open items

- **Scanner efficacy is bounded**: deterministic rules catch known shapes; novel phrasing will pass. Containment
  (F3 + data rendering) is the real guarantee; docs must say so. SC-004 is measured on our corpus only, so no
  generalized claim.
- **Embedding pricing/dimension provisional**: `text-embedding-3-small` profile pricing is unreconciled
  (`provisional: true`). The first real embedding call is owner-gated (paid).
- **Embedding provider key**: the embedding provider (OpenAI) may differ from the agent's chat provider; ingestion and
  query embedding resolve the workspace's key for the *embedding* provider via feature 014 custody. No key ⇒ upload
  refused with `503 provider_key.missing` (ingest) / binding `degraded` (invoke). Confirm the pilot workspace has an
  OpenAI key, or choose another 1024-dim embedding model before go-live.
- **API-process CPU**: PDF parsing in the API process can spike CPU; bounded by concurrency 2 and the 20 MiB cap.
  The M11 worker split remains the real fix.
- **Bounded spend overshoot**: a running ingestion finishes its current document after the cap is crossed (≤ one
  document's embedding cost), documented in D16.
- **Orphaned S3 objects** on a crash between DB commit and object delete: best-effort sweep, documented.
- **FE wiring not in scope**: the knowledge pages stay mock until an M12 slice wires the new endpoints.
- **Tool-mode latency**: tool mode forces buffered (non-streaming) responses, as for any agent with tools today. TTFT
  in tool mode is effectively full-answer latency; documented, not hidden.
