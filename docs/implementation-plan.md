# Agent Platform — Phase 1 Critique + Phase 2 TDD Implementation Plan

## Context

**Why this plan exists.** `agent-catalyst` is an internal platform to build → configure → test → publish
LLM agents, whose only defensible engineering lives in two places (`docs/scope.md`): the **provider
abstraction / runtime** and the **eval + drift-detection core**. Today **only `frontend/` is built** — a
mock-only Next.js console (253 `@backend` seams, drop-in-replaceable typed stores). The backend is
**fully *designed* but unbuilt**: `docs/backend/00–18` (HLD) + `docs/backend/lld/*` (buildable LLD) spec a
`services/` tree (`api`, `runtime`, `evals`, `ingestion`, `mcp`, `sandbox`) that **does not exist on disk**.

This document executes the user's two requested phases **before any implementation code**:
- **Phase 1** — architectural verification & critique of the HLD/LLD.
- **Phase 2** — an iterative TDD implementation plan (contract-first, Red→Green→Refactor per module).

**Decisions locked with the user (this session):**
- **Sequencing = C (de-risk on kept TDD code).** No throwaway spike. Build the gateway + runtime + eval
  cores *once* under full TDD, then run the `mvp-spec.md` H1/H2 de-risk experiments **on that production
  code** as a GO/NO-GO gate before building the rest of P1. Resolves the contradiction between the brief's
  "strict TDD from line one" and `mvp-spec.md`'s "throwaway spike, minimal tests."
- **Scope = P1 + promoted P0.** Chat core, gateway, runtime, eval/drift, publish/export, minimal RAG,
  security/tenancy, observability/cost, deployment/scaling, **plus** the promoted P0 items
  (adoption/enforcement `14`, org governance `15`, owner notifications `16`). P2 (tools/MCP/skills/full
  guardrails/connectors/long-term memory/sandbox) is **out of scope for this plan**.

**Build cadence (the user's TDD rule).** For every module: **contract/types first → output the failing
test files and get confirmation → minimum implementation to green → refactor.** Implementation code is
never written before its tests are confirmed.

---

## Phase 1 — Architectural Verification & Critique

**Overall verdict.** This is an unusually disciplined design. The LLD layer is genuinely buildable (typed
signatures, canonical hashing, exception hierarchy, RLS DDL) and relentlessly self-critical — it catches
its own HLD hand-waving (`10.lld` flags the per-call log line is "never actually emitted"; `09.lld` fixes a
`SET LOCAL` bind bug; `05.lld` closes a publish-gate TOCTOU). So the findings below are **not** beginner
gaps — they are the load-bearing holes that slip *between* documents, or live in a dependency that has
**no LLD**, which is exactly where a build hits a wall the prose says is solved.

Two meta-patterns: **(1)** the **AI-output trust boundary is the conspicuous hole in an AI platform** —
transport resilience is excellent, but *semantic* validation of model output at runtime is essentially
absent; **(2)** several controls are **referenced but never defined** (`rate_limit()`, the tokenizer behind
context-overflow, P1 SSRF enforcement, budget-counter durability).

### Findings (bucketed, severity-ranked, with doc citations)

File labels: `NN` = `docs/backend/NN-*.md`; `NNL` = `docs/backend/lld/NN-*.lld.md`.

| # | Sev | Vector | Finding | Evidence |
|---|-----|--------|---------|----------|
| F1 | **High** | AI boundary | **No runtime validation of model output vs `io_contract.output_schema`.** `InvokeResult.structured` is a bare dict returned as-is; the schema is only consumed *offline* by the eval deterministic check (quality scoring, not a trust boundary). A published `output_format=json` agent can return malformed/schema-violating JSON to an external caller. | `08:436`, `01L:369` |
| F2 | **High** | AI boundary | **No repair/retry loop for bad model output.** All retry/breaker/fallback is transport-only (429/5xx/timeout). "Model returned invalid JSON" has no path. Emulated structured output (model lacks native support → "enforced via instruction") is the highest-risk case and has **no** post-hoc validation. | `02:240,248-290`, `02L:271` |
| F3 | **High** | AI boundary / edge | **Hallucinated tool name / schema-violating tool args have no defined enforcement home.** The tool schema is frozen into `snapshot_hash`, but nothing specifies what the runtime does when the model calls an unbound name or args failing `schema_snapshot`. | `01:563`, `02L:47` |
| F4 | **High** | Edge / context overflow | **Token counting has no defined mechanism.** The entire context-overflow/compaction guard rests on `_tokens()` / `_context_budget()` which are `...`; **there is no pre-call tokenizer abstraction** across providers (the IR carries provider-reported usage *post-hoc* only). | `03L:308-343`, `02 §2` |
| F5 | **High** | Edge / rate limiting | **No rate limiting on the builder/console API** (which triggers the most expensive ops: playground, eval, ingestion). `rate_limit()` is referenced but **never defined** anywhere — no algorithm, no limits table, no 429 headers/quota. | `09:308,464`, `09L:154` |
| F6 | **High** | Edge / security | **Indirect prompt injection bypasses guardrails.** Input stages scan first-turn user text only and run **once, before** retrieval + the tool loop. Retrieved KB chunks, tool results, and skill fragments re-enter the model unscanned. (Flagged independently by two reviews.) | `06:94`, `03:18-31,76-89,164` |
| F7 | **High** | Cost / edge | **Budget enforcement is not durable.** Orphaned reservations (no TTL/reaper) accumulate → **false 402s**; the Redis counter is ephemeral while Postgres is the money SoT, so a Redis eviction/failover **silently un-caps spend** until month-end. Eval/judge spend is monitored but not *enforced* (can contend with prod budget). | `07:54-58`, `07L:41-43`, `10L:216-217` |
| F8 | **High** | Parallelism | **Eval fan-out and production share one circuit breaker + provider quota.** A `worker·batch` eval run (KEDA 0→30) hammering a model can 429-storm and **trip the Redis-shared breaker (per provider,model) for live traffic** on that same model. Compounds F10/F11 under load. | scaling diagram; `02L:208-255` |
| F9 | Med | API contract | **SSE wire contract is outside the "OpenAPI is source of truth" guarantee.** The 14 stream frames are prose/JSON examples, not Pydantic — generated TS client covers requests but none of the streamed half the FE renders. `invoke` returns SSE-or-JSON keyed off a body field (+ idempotent replay returns JSON) — one operation OpenAPI can't express. | `08:594-636`, `00L:269-273` |
| F10 | Med | Resilience | **Mid-stream retry/fallback double-emits tokens.** Retry wraps the stream loop and catches inside it; a failure *after* the first `TEXT_DELTA` replays from the top → client sees partial primary + full fallback. No "retry only before first token" guard. | `02:260-278` |
| F11 | Med | Resilience | **`Retry-After` is uncapped.** Only *computed* backoff has the 8s cap; a large provider `Retry-After` burns the whole 30s deadline sleeping — the "can't breach the deadline" claim is false. | `02L:195-196,204` |
| F12 | Med | Observability | **Failure-rate SLIs are specified but their counter emission is never written.** `_emit_metrics` does tokens/cost/provider-error only; the `status`-labeled run-level counters the availability + deadline-rate SLIs depend on are deferred to `_close_run` "with a comment, no body"; `status=error` vs provider-timeout label semantics don't exist. | `10:218-219`, `10L:282-292` |
| F13 | Med | Observability | **Correlation id does not span UI→API.** `trace_id` is minted *inside* `runtime.invoke` — no inbound `traceparent` ingestion, no outbound propagation to the provider; Langfuse trace and OTel spans are disjoint (no tracing backend). Browser→API→worker→provider cannot be reconstructed from one id. | `10:153,160-163`, `10L:89-90` |
| F14 | Med | API contract | **Idempotency is invoke-only.** Builder creates (`POST /agents`, `/versions`, `/tokens`) have no dedup → double-submit makes duplicate drafts. No API deprecation/sunset policy for the externally-exported invoke surface; error envelope carries no correlation/trace id. | `08:62`, `00L:204-288` |
| F15 | Med | Data model | **Immutability trigger is weaker than claimed.** `agent_versions_freeze` uses `do instead nothing` (silently drops the UPDATE, app thinks it succeeded) and only fires when `snapshot_hash` changes — an UPDATE mutating `system_prompt` without recomputing the hash leaves frozen columns mutated + hash stale. | `01:229-236` |
| F16 | Med | Data model | **`snapshot_hash` reads raw jsonb, bypassing the Pydantic models.** camelCase/snake_case or jsonb float re-serialization drift means the hash and the validated model can disagree — a cross-write hash-stability risk `canonical_json` float-normalization doesn't fully close. | `01L:512-567` |
| F17 | Med | Data model | **Integrity CHECKs missing.** `chunks.embedding vector(3072)` not tied to per-KB `embedding_dim`; `tool_bindings` has no exactly-one-of CHECK (tool_id/mcp_tool_id/subagent_config_id all nullable); `agents.active_version_id` FK doesn't constrain same-agent; `api_tokens.token_hash` not unique; DDL omits `ON DELETE` while ORM declares cascades. | `01:324,561-568,220-222,614` |
| F18 | Med | Eval | **Judge/per-case sampling non-determinism undermines the <10% FP target.** Judge temperature is never pinned; each case is run once with no variance/significance test. A temp>0 agent re-evaluated twice can trip `Q_THRESHOLD` from sampling noise. `pairwise_win_rate` returns 1.0 when all cases tie (uniformly-mediocre candidate passes). | `05:351-352`, `05L:149-150` |
| F19 | Med | Security | **P1 SSRF enforcement unspecified.** The egress proxy/gVisor sandbox is P2; in P1, HTTP tools have allow-lists but no stated enforcement mechanism, and DNS-rebinding / redirect-following bypasses are unaddressed. Per-user DEK isolation is app-logic-only (not Vault-policy-enforced). Redis is a hard SPOF for *all* auth (deny-set + rate-limit + idempotency). | `09:459,462,480-484`, `09L:446-451` |
| F20 | Low | Phasing | **Eval phasing tension.** The eval harness + publish gate are P1, but the eval-suite/case/baseline *authoring* routes are tagged P2 — "you can't run a suite you can't author." Must reconcile before sequencing. | `eval-gap-analysis.md §5.3` |
| F21 | Low | Deployment | **`worker` is one row in `00 §2` but split into `worker·interactive` + `worker·batch` in the scaling diagram.** Reconcile: two Deployments sharing the worker codebase, different autoscalers. | `00:43` vs scaling-model diagram |

**Disposition:** every High/Med finding is folded into the relevant TDD module below as a **first-class
failing test or a net-new contract** (several — F1/F2/F3 validators, F4 tokenizer, F5 `rate_limit()` — are
contracts that *do not yet exist in the design* and must be designed as the module's first step).

---

## Phase 2 — Iterative TDD Implementation Plan

> **Every module and every `.a/.b/.c` step below is governed by the ritual in "Working process, progress
> tracking, debugging & environments"** — the DoR/DoD gate (§A), the three-layer tracking (§B), the
> debugging protocol (§C), and the local→prod ladder (§D). The modules describe *what* to test and build;
> that section is *how* each step is run, tracked, debugged, and validated before it goes anywhere.

### Module dependency DAG (build order)

```
                    ┌────────────────────────────────────────────────┐
  STAGE 0 (gate)    │ M0  data model + snapshot_hash + conventions    │  P0 foundation
                    └───────────────┬────────────────────────────────┘
                                    │
  STAGE 1           ┌───────────────▼───────────┐   ┌──────────────────┐
  (defensible       │ M1 provider gateway (#1)   │──▶│ M2 runtime.invoke │
   cores +          └───────────────┬───────────┘   │    shared core    │
   DE-RISK GATE)                    │               └─────────┬────────┘
                                    │                         │
                                    └────────▶ ┌──────────────▼───────┐
                                               │ M3 eval/drift (#2)    │
                                               └──────────┬────────────┘
                              ╔═══════════════════════════▼══════════════════════════╗
                              ║  DE-RISK GATE: run H1/H2 on M1–M3 → GO/NO-GO          ║
                              ║  (mvp-spec.md §2); write back to scope.md             ║
                              ╚═══════════════════════════┬══════════════════════════╝
                                                          │ GO
  STAGE 2           ┌──────────┬──────────────┬──────────┴───────┐
  (product          │ M4 API   │ M5 security/ │ M6 obs/cost      │ M7 minimal RAG
   surface)         │ surface  │   tenancy    │   wiring         │  (+ indirect-inj)
                    └────┬─────┴──────┬───────┴────────┬─────────┴──────┬──────────┘
                         │            │                │                │
  STAGE 3           ┌────▼────────────▼────────────────▼────────────────▼────┐
  (promoted P0)     │ M8 adoption/enforcement · M9 org governance · M10 notify│
                    └──────────────────────────┬─────────────────────────────┘
                                               │
  STAGE 4           ┌────────────────────────┬─▼─────────────────────────────┐
  (ship)            │ M11 deployment/scaling  │ M12 frontend wiring (@backend) │
                    └────────────────────────┴───────────────────────────────┘
```

**Why this order:** M0 is the P0 gate — `VersionSnapshot`/`snapshot_hash` is the identity everything anchors
to. M1→M2→M3 are the two defensible cores + the spine; building them first lets the **de-risk gate** run on
real TDD'd code (choice C). Stage 2–4 are mostly "boring reliable software" but carry the highest-severity
*operational* findings (rate limiting, correlation, budget durability, SSRF, parallel contention).

**AI-module testing (M1, M3, M7).** These get the **two-layer** treatment (per the `ai-eval` approach):
Layer-1 deterministic unit/integration tests **+** a Layer-2 versioned-prompt eval set scored vs a frozen
baseline, both wired into CI. Everything else is single-layer deterministic TDD.

---

### STAGE 0 — Foundation

#### M0 — Data model, `snapshot_hash`, cross-cutting conventions
Source: `01`, `01L`, `00L`. SQLAlchemy 2.0 async + Alembic, Pydantic v2, Postgres 16.
- **0.a (Red):** property tests for `canonical_json` determinism (sorted keys, list order, float→canonical
  decimal) across processes; `compute_snapshot_hash` — the `DRIFT_TRIGGERS` table (`01L:572-588`): mutate
  each trigger → hash changes; `io_contract`/`kb_index_version` are the documented *exceptions*;
  clone-stability via stable binding-sort keys. Error taxonomy: one test per `AppError` subclass → correct
  status/code envelope (`00L:76-115`). Pagination cursor round-trip + malformed → 400. Idempotency
  check/reserve/replay incl. concurrent-key → `409 idempotency.in_progress`. RLS: unset GUC → zero rows.
  **Finding tests:** F15 (an UPDATE that mutates a frozen column is *rejected/raises*, not silently
  dropped, and the hash can't go stale); F16 (hash computed from the **validated model**, not raw jsonb —
  camelCase + jsonb-float round-trip stability); F17 (CHECK: `chunks.embedding` dim == `kb.embedding_dim`;
  `tool_bindings` exactly-one-of per `kind`; `active_version_id` same-agent; `token_hash` unique;
  `ON DELETE` parity between DDL and ORM).
- **0.b (Green):** Alembic migrations (core tables + native enums + indexes + RLS policies + the fixed
  immutability trigger + the new CHECKs); `VersionSnapshot` frozen dataclass + `to_snapshot()`;
  `canonical_json` + `compute_snapshot_hash` reading the validated model; `errors.py` + handler; `Page[T]`
  codec; idempotency wrapper; `WorkspaceScoped` mixin + tenant GUC.
- **0.c (Refactor):** registry-driven RLS policy loop; CI property test asserting every `DRIFT_TRIGGERS`
  field folds into the hash (the C2 guard) + a sibling asserting every trigger advances
  `latest_trigger_time`.

---

### STAGE 1 — Defensible cores + de-risk gate

#### M1 — Provider gateway (defensible core #1)
Source: `02`, `02L`. **Contract-first: design the three net-new contracts F1–F4 require before anything.**
- **1.a (Red):**
  - IR: `NormalizedRequest`/`NormalizedStreamEvent`/`NormalizedUsage`; cross-provider contract test (same
    request → same event *types* + correct substitutions across ≥2 adapters).
  - `compute_cost`: `billable_input = input − cached − cache_write`; `billable_output = output − reasoning`;
    no double-count; `quantize_usd` half-even @6dp; `batch_multiplier` only for eval/ingest.
  - `Substitution` preflight rule table; capability leak test (every downgrade recorded, none silent).
  - Circuit breaker (per provider,model, N=5, cooldown, half-open) via Redis `HINCRBY`; concurrent
    increments don't lose counts.
  - **Net-new contract — AI output validator (F1/F2):** `validate_output(model_json, io_contract) ->
    Structured | RepairNeeded`; malformed/schema-violating JSON → `RepairNeeded`; one bounded repair
    re-ask; emulated-structured-output path is validated *post-hoc*, not trusted.
  - **Net-new contract — tool-call validator (F3):** `validate_tool_call(name, args, snapshot) -> ok |
    UnknownTool | BadArgs`; hallucinated name / schema-violating args rejected and fed back to the model as
    an error result (not executed).
  - **Net-new contract — tokenizer abstraction (F4):** `count_tokens(messages, profile) -> int` per
    provider family; context-budget derived from it, not from `...`.
  - **Finding tests:** F10 (mid-stream failure after k deltas → client receives **no duplicated tokens**);
    F11 (total possible sleep bounded even with adversarial `Retry-After`); F8 (breaker opening from eval
    fan-out is scoped so it does **not** needlessly starve a distinct caller — verify the breaker key
    granularity + a bulkhead between `run_source=eval` and `run_source=api` on the same model).
- **1.b (Green):** LiteLLM wrapper + per-provider adapters behind one Protocol; retry/backoff/jitter
  (capped incl. `Retry-After`); breaker; fallback with reprice snapshotted as a drift trigger; the three
  validators + tokenizer; profiles as versioned JSON.
- **1.c (Refactor):** retry-only-before-first-token guard; eval/prod breaker bulkhead; half-open
  single-flight.

#### M2 — `runtime.invoke` shared core + graph
Source: `03`, `03L`, `00`. The single entry point all four callers share.
- **2.a (Red):** `invoke(snapshot, inputs, ctx)` pipeline order (validate IO → budget pre-check → input
  guardrails → graph → output guardrails → persist Run+Spans → reconcile spend); `deadline_s` cuts a long
  generation with partial usage recorded + `stop_reason="deadline_exceeded"`; `MAX_TOOL_CALLS` bounds cost;
  context overflow → compaction at 0.7 headroom using the **M1 tokenizer** (F4), `context_truncation` span;
  sub-agent depth/fan-out caps → `subagent_depth_exceeded`/`subagent_fanout_exceeded`; `AsyncSession`
  everywhere (no sync DB call inside the async generator — a regression test that a blocking call fails the
  event-loop-starvation assertion); persist-then-reconcile atomicity (crash between → over-count, never
  under-bill, never lose a run row). **Finding test:** F3 wired — a hallucinated tool call returns an error
  result into the loop, never executes.
- **2.b (Green):** LangGraph graph/state/nodes + SSE bridge; checkpointer (session memory); budget guard;
  the invoke pipeline.
- **2.c (Refactor):** extract each pipeline step into its own independently-testable module.

#### M3 — Eval / drift engine (defensible core #2)
Source: `05`, `05L`, `eval-gap-analysis.md`.
- **3.a (Red):** `verdict()` each branch isolated (n<min_cases→low_confidence; hard-fail→fail;
  q_drop>Q_THRESHOLD→fail; !within_budget→fail; pairwise<0.5→fail; else pass); `score_quality` excludes
  hard-failed + judge-errored cases; `judge_case_validated` (non-JSON → one strict reprompt → `JudgeError`;
  >20% judge-errored → `eval_failed`, distinct from `verdict=fail`); publish gate (PASS older than
  `latest_trigger_time`→409; low_confidence w/o override→409); concurrency (two finalizes serialize on the
  per-version advisory lock; stale older PASS after a newer trigger does **not** set `publishable`).
  **Finding tests:** F18 — judge pinned at **temperature=0** (determinism: same candidate twice → same
  verdict); per-case variance surfaced (a significance guard or documented repeat-count); `pairwise_win_rate`
  all-ties does **not** auto-pass. F20 — the eval suite/case authoring path exists in P1 (reconcile the
  phasing).
- **3.b (Green):** `deterministic.py` + `judge.py` (fixed/versioned, cached, batched) + `drift.py` (verdict
  + publishable + `DRIFT_TRIGGERS` invalidation) + the eval worker + `assert_publishable`.
- **3.c (Refactor):** verdict cache keyed `(caseId, baselineHash, candidateHash, judgeVersion)`; Batch API;
  deterministic pre-filter before judge.

#### ▶ DE-RISK GATE (choice C) — run H1/H2 on M1–M3
Not a module; a **milestone gate** before Stage 2. Using the TDD'd gateway + runtime + eval:
- **H2:** one `NormalizedRequest` drives ≥2 providers (cheap-hosted + local Ollama) incl. streaming;
  computed cost vs hand-calc within **±5%** (lock the tolerance first); every capability downgrade recorded
  as a `Substitution`.
- **H1:** on a frozen baseline + a 20–30 case suite (non-sensitive task per `mvp-spec.md §9`), seeded
  regressions flagged at **≥80% detection, <10% false-positive**, with a **calibrated judge (Cohen's
  κ ≥ 0.6)** on a human-labeled holdout.
- **Output:** fill the GO/NO-GO, record the empirically-tuned `Q_THRESHOLD`, and **write back to
  `scope.md`** (Models + Evaluation + decision log) — making `spec.md`'s "assumes the viability MVP has
  passed" finally true. A NO-GO stops Stage 2 (rethink the core, not just the plumbing).

---

### STAGE 2 — Product surface

#### M4 — API surface + contracts
Source: `08`, `08L`, `00L`.
- **4.a (Red):** per-router DTO validation (`AgentCreate` bounds, `extra="forbid"`, `AgentPatch` rejects
  `status`); error-envelope invariant; camelCase⇄snake_case round-trip; ETag/`If-Match` → `409
  conflict.stale`; async job `202 {job_id}` + poll; auth matrix per route. **Finding tests:** F9 — **SSE
  frames modeled as Pydantic** (14 event types) so the streamed contract is codegen'd + typed, and the
  SSE-or-JSON/replay media-type ambiguity is pinned (documented asymmetry + header); F14 —
  **`Idempotency-Key` honored on builder creates** (double-submit → one row); correlation/trace id in the
  error envelope; one deprecation/sunset header policy for `/v1/.../invoke`. F16 corollary — DTO dedup
  (single `ChunkConfig`/`AgentCreate` definition).
- **4.b (Green):** routers, schemas, publish/export, OpenAPI 3.1 emission + `openapi-typescript` client-gen
  with a CI drift-fail.
- **4.c (Refactor):** shared list-endpoint `Page[T]` + filter/sort whitelist; export narrowed per-endpoint
  OpenAPI.

#### M5 — Security / tenancy
Source: `09`, `09L`, `18L`.
- **5.a (Red):** RLS (every `WorkspaceScoped` table has a policy — CI check; unset GUC → zero rows;
  A-can't-read/patch/delete-B → 404 not 403; vector search never crosses tenants); Vault envelope (3 DEK
  scopes; scope mismatch → `PermissionError`; platform pool key never resolved in a workspace session);
  token hashing (sha256+prefix, shown once, revoked immediately); deny-set (admin revoke → next request
  401; Redis outage → fail-closed non-2xx); claim-based authz + `require_role`; access-policy at invoke
  (`executer` gated on teams only; `private` denies every caller); org-plane boundary (`ac_app` no EXECUTE
  on `org_report_*`; every org read writes a `workspace_id=NULL` audit row). **Finding tests:** F5 —
  **`rate_limit()` defined and tested** for *both* published tokens **and** builder/console routes (Redis
  sliding window, limits table, 429 + `Retry-After`/`X-RateLimit-*`); F19 — **P1 SSRF enforcement** (an
  HTTP tool refuses a URL resolving to a private/internal IP and refuses an allowed-domain redirect to an
  internal host, *before* the P2 sandbox exists); Redis-SPOF blast radius documented + a degraded-mode
  decision test.
- **5.b (Green):** tenancy guard + RLS DDL loop; IdP-agnostic adapter → `Principal`; Vault Transit client;
  token + deny-set; `rate_limit()`; the P1 app-level egress guard.
- **5.c (Refactor):** PgBouncer txn-mode wiring; per-user DEK Vault-policy note + least-privilege follow-up.

#### M6 — Observability + cost wiring
Source: `10`, `10L`, `07`, `07L`.
- **6.a (Red):** one JSONL `ModelCallLog` line per call (all 7 A2 fields; cost string == `spans.cost_usd`
  byte-for-byte across all four callers); single-source cost (no sink re-derives); telemetry best-effort
  (Langfuse/Prometheus/redaction failure can't 500 a run; `_close_run` PG flush failure alerts + re-raises
  but never un-streams); redaction fail-closed before any write; `/readyz` not-ready on Vault/migration
  issues; budget `reserve` past `cap()` → rollback + 402, `reconcile` adjusts by `actual−est`; 80/90%
  notifications fire once per upward crossing; cache safety (`is_cacheable_exact`/`is_qa_safe` refusals).
  **Finding tests:** F12 — **`ac_runs_total{status}` emitted** on success/error/provider-timeout/deadline
  paths so the availability + deadline-rate SLIs actually compute; first-token SLI capture point defined;
  F13 — **end-to-end correlation** (inbound `traceparent` ingested at the API edge, propagated to runtime +
  outbound provider call; Langfuse trace id tied to it); F7 — **budget reservation TTL + orphan reaper** (a
  crashed run leaves no residual reservation) and **Redis-counter durability/reconciliation** (key
  loss/failover does not silently disable the cap; counter reconciled from `sum(runs.cost_usd)`); eval/judge
  spend attribution decision + an *enforced* eval budget separate from prod.
- **6.b (Green):** span emission + Langfuse v3 trace lifecycle; Prometheus counters + the run-level
  `status` counters; correlation middleware; `BudgetGuard` reserve/reconcile + reaper + reconciliation
  sweep; the per-call log helper.
- **6.c (Refactor):** multi-window burn-rate alerts; ClickHouse analytics DB deferred (P2) but the
  `workspace_id`-injecting rollup API seam stubbed.

#### M7 — Minimal RAG (P1-min) + indirect-injection scanning
Source: `04`, `04L`, `06` (input-stage interface only).
- **7.a (Red):** file upload → ingest (extract→chunk→embed→pgvector namespaced per `(ws,kb)`) → retrieve
  (top-k, similarity threshold, retrieval-as-tool + always-on-stuffing); known-Q → right-chunk; citations
  carried; changing embedding model → re-ingest + re-eval trigger + verdict-cache bust. **Finding test:**
  F6 — **retrieved chunks, tool results, and skill fragments are scanned by the injection stage** before
  re-entering the model (not just first-turn user text); a poisoned KB document cannot steer an
  out-of-allow-list tool action.
- **7.b (Green):** `ingestion/` pipeline + `retrieval/retriever.py` + `RetrievalConfig`; the post-retrieval
  / pre-model injection scan hook in `graph/nodes.py`.
- **7.c (Refactor):** dedup keyed on `(content_hash, embedding_model, chunk_config)`; ingest
  atomicity/rollback.

---

### STAGE 3 — Promoted P0

- **M8 — Adoption / enforcement** (`14`, `14L`): key-custody spine (platform holds provider creds);
  create-time registration gate (`registered_at`); similarity/dedup scanner (`agent_signatures`); import
  flow. TDD: registration gate → `409 registration.gate`; dedup scan surfaces near-duplicates; enforcement
  modes + exemptions.
- **M9 — Org governance reporting** (`15`, `15L`): deployment-wide `admin` plane; elevated audited
  cross-tenant read via `SECURITY DEFINER` on `platform_report` role/connection (metadata-only for
  `private`). TDD: `ac_app` has no EXECUTE on the report fns; every read audited; private content never
  leaks beyond metadata.
- **M10 — Owner notifications** (`16`, `16L`): `notifications` entity + emit at
  drift-transition/eval-fail/budget-threshold/model-deprecation; deduped; recipient fan-out via the
  `group_members` read-model cache with an owner-only floor; model-deprecation scan (worker). TDD: emit
  once per transition; floor never silent; deprecation scan reads capability-profile `availability`.

---

### STAGE 4 — Ship

- **M11 — Deployment / scaling + concurrency** (`11`, `11L`, scaling diagram): **split `worker` into
  `worker·interactive` + `worker·batch`** (F21), two Deployments sharing the codebase, different
  autoscalers (api HPA 2→20; batch KEDA 0→30; sandbox P2 KEDA 0→N). Compose (dev) + Helm (prod); boot +
  migrations; PgBouncer txn-mode. **Concurrency test suite (first-class):** eval fan-out must **not** trip
  the prod breaker (F8); parallel `reserve`s never exceed the cap beyond the stated bound (F7); concurrent
  eval finalizes serialize correctly on the advisory lock; N parallel SSE streams on one api replica don't
  block on a sync query (F — the `AsyncSession` guarantee under load).
- **M12 — Frontend wiring** (`frontend/`): swap the **253 `@backend` seams** for a typed API client
  generated from M4's OpenAPI; keep hook/type **shapes** (`useAuth`, `RunResolution`, `EvalRun`,
  `EvalSuite`, exported `data.ts` types) identical — mocks were built as drop-in replacements. Delete
  `@backend remove` seeds/dev-overrides. **⚠️ Verify every Next.js App Router API against
  `frontend/node_modules/next/dist/docs/` before writing any frontend code** — this repo is a *modified
  Next.js 16.3.8* (`frontend/AGENTS.md`); do not rely on training-data conventions. TDD: contract tests
  that the generated client matches the OpenAPI; a few integration tests per swapped resource (auth gate,
  list/detail, a mutation, an SSE stream render).

---

## Working process, progress tracking, debugging & environments

This is the operating system for the build — the repeatable ritual applied to **every stage and every
step**, so the processes we've set are followed, not just intended.

### A. Per-step process gate (Definition of Ready / Definition of Done)

Every module runs the same loop; each row names the **governing skill/process** and its hard gate. A step
does not advance until its gate is green.

| Step | Governing process | Gate (must be true to advance) |
|------|-------------------|-------------------------------|
| **Module start (DoR)** | `superpowers:executing-plans` + `using-git-worktrees` | Plan step exists; a tracked task is opened (§B); an isolated branch/worktree exists; contract/types drafted first |
| **.a RED** | `superpowers:test-driven-development` | Failing test files written **and shown to the user for confirmation before any impl** (your brief's rule); any pasted code snippet compiles (the LLD "snippet-compile" convention) |
| **.b GREEN** | `superpowers:verification-before-completion` | Minimum impl; the exact test command is **run and its output pasted** — no "done/passing" claim without evidence (CLAUDE.md) |
| **.c REFACTOR** | `/simplify` then `/code-review` | Tests stay green; reuse/simplify/efficiency pass applied; no behavior change |
| **mid-step failure** | `superpowers:systematic-debugging` | Root cause reproduced by a test **before** any fix (§C) |
| **Module done (DoD)** | `superpowers:requesting-code-review` + `ai-eval` (M1/M3/M7) | All tests green (evidence); `lint`/`build` green; AI modules show a **PASS drift report**; review done; a plain report of what was / was **not** verified (CLAUDE.md); PR opened **only when asked**, never to `main` |

### B. Progress tracking (three layers, always in sync)

1. **Harness task list** — one task per module (M0–M12) **+** the de-risk gate; each `.a/.b/.c` step is a
   subtask flipped `in_progress → completed` as it runs. Created at execution start (can't create in plan
   mode). This is the live source of truth the session reports from.
2. **This plan file as a living backlog** — the status table below; each step gets a checkbox; the file is
   edited as modules complete (it is the durable record across sessions).
3. **Git + CI as the objective signal** — branch/worktree per module, a commit per GREEN step (convention
   inferred from `git log`), a PR per module (on request). A module is only "green" when its CI gates pass:
   the two-layer test suite, a drift PASS (AI modules), the secret-scan, and the tenant-isolation suite.

**Module status table** (maintained in this file as work proceeds):

| Module | Stage | Core? | DoD gate (headline) | State |
|--------|-------|-------|---------------------|-------|
| M0 data model | 0 | — | hash property tests + RLS zero-rows | ☐ todo |
| M1 gateway | 1 | #1 | cross-provider contract + validators (F1–F4) | ☐ todo |
| M2 runtime.invoke | 1 | spine | pipeline + deadline + AsyncSession-under-load | ☐ todo |
| M3 eval/drift | 1 | #2 | verdict branches + publish-gate + determinism (F18) | ☐ todo |
| **DE-RISK GATE** | 1 | — | **GO: ≥80% / <10% / κ≥0.6 + cost ±5%; write back scope.md** | ☐ todo |
| M4 API | 2 | — | typed SSE frames (F9) + idempotent creates (F14) | ☐ todo |
| M5 security | 2 | — | isolation suite + `rate_limit()` (F5) + P1 SSRF (F19) | ☐ todo |
| M6 obs/cost | 2 | — | failure-rate counters (F12) + correlation (F13) + budget durability (F7) | ☐ todo |
| M7 min-RAG | 2 | — | right-chunk + indirect-injection scan (F6) | ☐ todo |
| M8–M10 P0 | 3 | — | reg-gate / org-metadata-only / notify-once | ☐ todo |
| M11 deploy/scaling | 4 | — | worker split (F21) + concurrency suite (F7/F8) | ☐ todo |
| M12 frontend wiring | 4 | — | 253 @backend seams swapped; client==OpenAPI | ☐ todo |

### C. Debugging protocol (by failure class)

Governing rule for all classes: **`superpowers:systematic-debugging`** — reproduce with a test, find root
cause, then fix; never patch speculatively, never fix a failing eval by loosening a threshold.

| Failure class | First move | Tools / substrate |
|---------------|-----------|-------------------|
| Deterministic test fail | the RED test *is* the repro; bisect to root cause | pytest `-x`, the failing assertion |
| Eval/drift regression or flaky verdict | inspect the failing case's trace; confirm judge is `temp=0` + κ in-band; if a real behavior gap, **add a regression eval case** (feedback loop), don't move the bar | per-call JSONL log, Langfuse trace, the two-layer harness |
| Provider / transport failure | reproduce with a fault-injecting adapter stub | breaker state (Redis), retry/backoff + `Substitution` records |
| Tenancy / RLS leak | run the isolation suite; trace the `SET LOCAL` GUC | `pg_policies`, GUC trace, fail-closed default |
| Cost mismatch | reconcile computed ↔ hand-calc ↔ provider billing | capability profiles (the SoT), `compute_cost` |
| "Why did it answer differently?" | replay the run from its `VersionSnapshot` + `trace_id` | **immutable snapshot + correlation id (F13) + JSONL log = any run is replayable** |

Local observability available from day one: Langfuse UI, structured JSON logs keyed by correlation id,
`/healthz` + `/readyz`, Prometheus counters.

### D. Environments & go-live ladder (local → qa → prod)

> **Terminology:** in *this* repo "sandbox" is the **P2 gVisor tool-exec unit** (out of scope here), **not**
> a staging env. The pre-prod ladder is **local (docker compose) → qa → prod** (`settings.env ∈ dev|qa|prod`).

- **Local-first, always.** `deploy/compose` brings up Postgres+pgvector, Redis, Vault, Langfuse v3 +
  ClickHouse, MinIO. Models = **cheap-hosted + local Ollama** (zero/near-zero spend). **Everything through
  Stage 1 + the de-risk gate runs entirely locally** on cheap/local models.
- **Spend safety (scope.md ≤ $100/mo).** Live provider spend is gated on an explicit greenlight; the
  **enforced budget cap (M6) must be verified locally** (it actually refuses over-spend) **before any real
  provider key is ever loaded**.
- **Secrets safety (CLAUDE.md).** No real keys/tokens/PAT-bearing remotes in code, commits, or output —
  ever; Vault only; dev uses cheap/local creds.
- **Deploy safety (CLAUDE.md).** Feature branches/worktrees only; **never** push to `main`/prod or run a
  deploy without explicit in-session confirmation (a push to an auto-deploy branch *is* a prod deploy).
- **Per-stage pre-live checklist** (gate before promoting beyond local):

  | Stage | Must be green locally before promotion |
  |-------|----------------------------------------|
  | 1 | de-risk **GO** (≥80% detection / <10% FP / κ≥0.6) + cost within ±5% on cheap/local models |
  | 2 | tenant-isolation suite green; **secret-scan clean** (no secret in logs/traces/export); rate limits active; budget cap refuses over-spend; playground first-token **p95 < 2s** |
  | 3 | org read metadata-only + audited; registration gate enforced |
  | 4 | concurrency suite green (eval fan-out ≠ prod-breaker trip); full compose stack healthy end-to-end; `frontend` build+lint green — **then, only with explicit confirmation**, promote qa → prod |

- **How to run it:** services via compose + `uvicorn`; frontend `cd frontend && npm run dev` (**verify Next
  16.3.8 APIs against `node_modules/next/dist/docs/` first**); use the `run` skill to drive/screenshot the
  app; `ai-monitor` once anything is live.

## Cross-cutting TDD conventions (apply to every module)

- **Contract-first.** Types/interfaces (Pydantic v2 at boundaries; frozen dataclasses for
  `VersionSnapshot`/`RunContext`) before any class that implements them. For M1/M3/M7 the *net-new*
  contracts (F1–F5 validators, tokenizer, `rate_limit()`) are designed as step `.a`.
- **Strict Red→Green→Refactor.** Output the **failing test files first and get confirmation** before
  implementation code (the user's rule). One todo per TDD step.
- **Defensive AI boundaries.** Every LLM call result passes a strict validator (F1/F2/F3) before it's
  trusted; transport gets breaker + capped backoff + fallback (M1).
- **Observability-ready.** Structured JSON logs with a correlation id threaded UI→API→runtime→provider
  (F13); the per-call log line emitted by one shared helper (M6).
- **Two-layer AI testing.** M1/M3/M7 carry a versioned-prompt eval set vs a frozen baseline in CI
  alongside deterministic tests; a model- or knowledge-affecting change must show a PASS drift report.
- **Commit convention:** infer from `git log` (no enforced style yet in this repo); never commit to
  `main`; branch + PR only when explicitly asked. No deploy/push without in-session confirmation.

## Load-bearing first files (from `spec.md`)
`services/runtime/providers/abstraction.py` (IR + adapters + profile resolution + **the F1–F4 validators +
tokenizer**) · `providers/profiles/*.json` · `runtime/invoke.py` · `evals/drift.py` (+ `deterministic.py`,
`judge.py`) · `api/models.py` + `migrations/` · `api/publish/endpoint.py` · `api/errors.py` +
`api/pagination.py` + `api/idempotency.py` (from `00L`) · `api/openapi` + `export/snippet.py`.

---

## Verification (how each stage proves itself)

- **Per module:** the module's own Red tests pass (Green), then the Refactor keeps them green. No module is
  "done" until its failing tests existed first and now pass.
- **Stage 1 + DE-RISK GATE (headline):** provider-abstraction contract tests (same `NormalizedRequest`
  across ≥2 providers; substitutions recorded; cost within ±5% of hand-calc); drift seeded-regression
  (≥80% flagged, <10% FP on an unchanged agent) **with κ ≥ 0.6**; the GO/NO-GO is filled and written back
  to `scope.md`. **A NO-GO halts Stage 2.**
- **Stage 2:** publish with no/failed/low-confidence eval → 409; export cURL runs + streams + **no secret
  in output/logs/traces/export** (automated scan); tenant-isolation suite green (incl. DB-level
  EXECUTE-denial); builder + published rate limits return 429; an HTTP tool refuses an internal-IP URL;
  playground first-token p95 < 2s.
- **Stage 3:** registration gate → 409; org read is metadata-only for `private` + audited; a drift
  transition emits exactly one owner notification.
- **Stage 4:** `cd frontend && npm run build && npm run lint` green; the concurrency suite passes (eval
  fan-out doesn't trip the prod breaker; parallel reserves respect the cap; SSE streams don't starve);
  the generated client matches OpenAPI; a swapped resource works end-to-end against a running `api`.
- **There is no test suite today** (`npm test` doesn't exist) — this plan creates the first one. Do not
  claim tests pass until they are written and run.
