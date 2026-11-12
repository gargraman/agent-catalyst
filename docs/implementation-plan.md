# Agent Platform — Architecture Review + Spec-Driven Implementation Roadmap

> **⚠️ Secret custody — as built (feature 014, 2026-11-11):** every "Vault" / "Vault Transit" / "per-workspace DEK" / "AppRole" reference in this roadmap is **superseded**. Provider-key custody now runs through a pluggable secrets backend (**AWS Secrets Manager** / LocalStack / `local`, selected by `AC_SECRETS_PROVIDER`); other cred families are designed for the same seam but not built. Read "Vault" as "the secrets backend." See `docs/scope.md` (2026-11-11) and `docs/backend/09 §9`.

## Context

**Why this plan exists.** `agent-catalyst` is an internal platform to build → configure → test → publish
LLM agents, whose highest-risk engineering is the **provider abstraction / runtime** and the **eval +
drift-detection core** (`docs/scope.md`). The Next.js frontend is built but mock-only (253 `@backend`
seams). The standalone `mvp/` viability experiment now has Phase-1 helpers, a narrow H2 provider harness,
and a local live-call log. `services/` also contains foundation code (hashing/provider primitives, API
helpers, migrations, and tests), but no end-to-end API, runtime invocation path, or worker. The HLD/LLD
remain the design basis for those production services.

This document is the architecture critique and production roadmap. Spec Kit feature artifacts will own
the reviewable behavior requirements and actionable tasks for each bounded change.

**SDD adoption status (updated 2026-11-06).** GitHub Spec Kit was selected on 2026-10-31 and project
constitution v1.0.0 ratified the same day (`.specify/memory/constitution.md`). The toolchain is now live
and **seven features have been driven through the full `spec → clarify → plan → tasks → checklist →
analyze → implement` chain** under `specs/`: `001-m0-data-model`, `002-provider-gateway`,
`003-runtime-invoke-spine` (M2a), `004-eval-drift-engine` (M3a), `005-thin-api-tenancy` (M4a),
`006-resource-crud-sync` (M4-remainder backend), and `007-ai-output-trust-boundary` (M2b — the AI-output
trust boundary). Existing documents (`scope.md`, `spec.md`, `mvp-spec.md`, backend designs, integration
maps) are evidence/design inputs, not a substitute for each feature's reviewed artifact chain. The
standalone `mvp/` viability experiment delivered its GO/NO-GO (advisory-gate decision logged in
`scope.md`); **H1 remains blocked** until the approved two-rater κ≥0.60 calibration prerequisite is
available (the publish gate stays advisory until then).

The required SDD artifact set is incomplete: the project constitution is ratified, but each feature still
needs `spec.md`, `plan.md`, `tasks.md`, and the Spec Kit requirements checklist. Add `research.md`,
`data-model.md`, `quickstart.md`, and API contracts when the feature plan needs them. The checklist and
supporting artifacts must be generated/reviewed through the official Spec Kit workflow, not represented by
this roadmap alone. Do not begin experiment implementation until this feature set and its unresolved
decisions are reviewed.

**Blocking gaps before that feature is ready:**
- H1's task, source domain (public release notes/changelogs), and candidate schema are approved, but the
  exact corpus/URLs/hashes, required-fact labels, prompt lesions, and acceptance examples are not frozen.
- The current OpenAI H2 profile is stale: it marks deprecated `gpt-5-mini` active and its published
  input/output/cached-input prices do not match OpenAI's listed prices. `gpt-5.6-terra` + Ollama and a
  $10 combined hosted-spend cap are selected, but the profile, provider-specific cache semantics, and
  worst-case cost preflight must be reconciled before the H2 rerun. The existing live log cannot be
  retroactively used as cost evidence.
- The user approved Haiku 5.5 (`claude-haiku-5-5`, effort `medium`), 20 development documents, 60
  calibration pairs, 50/50 held-out arms, κ ≥ 0.60, repeatability checks, and the $10 cap. However, two
  independent human raters are not available. This makes the approved H1 calibration protocol
  infeasible. The user chose to preserve the gate and keep H1 blocked until two independent raters are
  available; do not substitute a weaker calibration method. No H1 provider calls are authorized while
  this blocker remains.
- Production design documents still name Claude Haiku 4.5 as the judge (`backend/00`, `backend/05`,
  `backend/07`, `eval-gap-analysis`, and diagrams). Haiku 5.5 is approved for the H1 experiment only;
  this does not select the production judge. Reconcile the production model/settings/pricing and capability
  profiles before specifying the production eval/drift feature.
- The existing design corpus overlaps (`scope.md`, `spec.md`, backend designs, frontend map); feature
  specifications must turn those inputs into user-visible acceptance scenarios and stable API contracts,
  not mechanically convert every `@backend` comment into a task.
- `uv 0.13.6` and `specify-cli 1.2.5` are installed in the user tool directory; `specify version`
  confirms CLI 1.2.5 on Python 3.12.13. Project initialization remains pending. The system Git/Python
  shims fail because Xcode Command Line Tools are absent; their installer cannot run here without an
  active GUI session. Only the ratified constitution has been added under `.specify/`; no Spec Kit scaffold
  or Codex skill integration has been created. The official
  [existing-project guide](https://github.github.io/spec-kit/guides/existing-projects.html) calls for a
  reviewable baseline before initialization, and Git cannot currently inspect or record one.
  Resolve the toolchain/baseline path before initializing Spec Kit or treating its generated artifacts
  as authoritative.

**Sequence confirmed 2026-10-31.** Finish the standalone `mvp/` H1/H2 experiment and record its
GO/NO-GO **before starting or extending production backend modules**. The existing partial M0 foundation
is historical work; it does not change this gate. On GO, continue the production plan from M0, then M1–M3;
on NO-GO, stop backend expansion and revisit the product hypothesis. The old choice-C sequence (build
M1–M3 first, then validate) is superseded.

**Product boundary and scope.** The first release is an **internal pilot** for one corporate deployment.
The eventual backend target is the full current frontend capability surface, phased rather than silently
dropping UI features. The initial pilot plan covers P1 plus promoted P0 (chat core, gateway/runtime,
eval/drift, publish/export, minimal RAG, security/tenancy, observability/cost, deployment, adoption and
governance). Current UI surfaces classified P2 (tools/MCP, skills, channels/connectors, full guardrails,
long-term memory, sandbox, and advanced spend/concurrency controls) remain follow-on phases, not first-
pilot scope. Voice remains deferred.

**Build cadence (confirmed TDD rule, governed by SDD).** For each feature: approved behavior spec →
clarification → implementation plan with acceptance-criterion-to-verification mapping and named commands
→ quality checklist and dependency-ordered tasks → consistency analysis → implementation. With the
Codex integration, invoke these through installed `$speckit-*` skills rather than generic slash-command
notation. During implementation, settle the contract in the reviewed artifacts → write and run the Red
test before production code → show the test files and expected-failure evidence and get confirmation →
make the minimum change to reach Green → refactor while keeping all affected checks green. Production
types, interfaces, validators, and migrations follow this same test-first order. The exploratory `mvp/`
code does not waive this cadence for `services/`.

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

## Production implementation roadmap (after the viability gate)

> **Every module and every `.a/.b/.c` step below is governed by the ritual in "Working process, progress
> tracking, debugging & environments"** — the DoR/DoD gate (§A), the three-layer tracking (§B), the
> debugging protocol (§C), and the local→prod ladder (§D). The modules describe *what* to test and build;
> that section is *how* each step is run, tracked, debugged, and validated before it goes anywhere.

### Module dependency DAG (build order)

```
  PRE-PRODUCTION     ┌───────────────────────────────────────────────────┐
                     │ `mvp/` H1 + H2 experiment → documented GO / NO-GO │
                     └──────────────────────────┬────────────────────────┘
                                                │ GO only
  STAGE 0            ┌──────────────────────────▼───────────────────────┐
  (foundation)       │ M0 data model + snapshot_hash + conventions       │
                     └──────────────────────────┬───────────────────────┘
                                                │
  STAGE 1            ┌──────────────────────────▼──────┐  ┌─────────────┐
  (defensible        │ M1 provider gateway (#1)        │─▶│ M2 runtime  │
   cores)             └──────────────────────────┬──────┘  │ .invoke     │
                                                │         └──────┬──────┘
                                                └───────▶┌───────▼──────┐
                                                         │ M3 eval/drift│
                                                         └──────────────┘
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

### PRE-PRODUCTION — Standalone viability experiment and gate

Complete this gate in `mvp/` before starting or extending production backend modules. The current H2
harness is exploratory evidence only; it is not a pass. H1 and the combined decision remain open.

- **H2:** one `NormalizedRequest` drives at least two providers (the current hosted OpenAI + local Ollama
  pair), including streaming; calculated cost is within the pre-locked ±5% hand-calculation tolerance;
  every required capability downgrade is recorded as a `Substitution`. A second hosted vendor is a later
  confidence check, not part of the currently written two-provider pass bar.
- **H1:** the approved protocol uses 20 public-text development documents, 60 separate
  pairwise calibration examples, and 100 held-out documents (50 unchanged controls + 50 seeded
  regressions). It retains the existing ≥80% regression-detection and <10% false-positive targets.
  Dataset, judge, calibration sample, agreement threshold, schema, and threshold-tuning method must be
  fixed in reviewed Spec Kit artifacts before measurement. Although `κ ≥ 0.60` is approved, the required
  independent human raters are unavailable, so H1 cannot currently be measured as specified.
- **Output:** reproducible scorecard, saved assertions/run metadata, tuned threshold, GO/NO-GO, and write-
  back to `scope.md`. GO unlocks M0 onward; NO-GO stops expansion pending a product/technical decision.

**Why this order:** the confirmed pre-production gate keeps unresolved model/eval risk from being hidden
inside a larger backend build. After GO, M0 establishes `VersionSnapshot`/`snapshot_hash`; M1→M2→M3 build
the production provider/runtime/eval core. Stage 2–4 address the product surface and high-severity
operational findings (rate limiting, correlation, budget durability, SSRF, and parallel contention).

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

> **Built-state (2026-11-04, feature `specs/003-runtime-invoke-spine`): M2 was split into M2a (spine, DONE) + M2b (validators, DONE 2026-11-06).** M2a shipped the shared `invoke`/`invoke_result` **as a plain async pipeline, not LangGraph** (research D1 — LangGraph is deferred to the first genuinely branching feature; a single generate-and-stream turn does not need a graph). Built: validate-IO → budget pre-check (`tokens.count_tokens`, over-estimating) → gateway stream → event re-surfacing (incl. reasoning/tool-call/stop) → `deadline_s`-bounded execution (+ wire-level transport timeout) → exactly one honest `RunRecord` via a context sink. **Not in M2a scope at all** (later milestones, as originally designed): LangGraph graph/checkpointer, sub-agent depth/fan-out caps, DB-persisted Run+Spans and persist-then-reconcile spend atomicity (the runtime only *emits* the Run to a sink — D2/D3; persistence belongs to the API/worker caller). `stop_reason` on a deadline is the gateway/stop value carried through; the Run's outcome is `timeout`.
>
> **M2b built-state (2026-11-06, feature `specs/007-ai-output-trust-boundary`): the AI-output trust boundary is now ENFORCED.** The shared `_pipeline` gained two modes: **pass-through** (plain text, no tools → live streaming, unchanged) and **buffered validated mode** (json output OR bound tools → buffer each turn, validate, correct, emit only the final validated events). Wired the already-tested validators into the pipeline — output **validate/repair** vs `output_schema` (F1/F2) and **tool-call enforcement** (F3) — behind a **bounded self-correction loop**: one repair re-ask on bad/empty-schema JSON, one feed-back re-ask on a rejected tool call, under a dedicated `ctx.max_corrections` budget (default 2) and the existing deadline. New fail-closed outcomes `output_invalid` / `tool_call_rejected`; new `RunRecord.correction_turns` (persisted into `runs.usage`, no migration); intermediate correction turns suppressed from the client stream but fully accounted (summed usage/cost, P7/P8). A **valid tool call is surfaced but NOT executed** — real tool execution/sandbox and the **guardrail execution engine** (`guardrail_config` stays data-only) remain **deferred to P2**. API/SSE layer unchanged (new outcomes ride the existing `error`+`done` frames). 388 tests green; `runtime/` purity intact (P6); no live calls.


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
  **Finding tests:** F18 — judge uses a fixed model ID, prompt/rubric hash, and provider-supported API
  settings; repeat-run variance is reported (do not require `temperature=0` on providers that reject it);
  `pairwise_win_rate`
  all-ties does **not** auto-pass. F20 — the eval suite/case authoring path exists in P1 (reconcile the
  phasing).
- **3.b (Green):** `deterministic.py` + `judge.py` (fixed/versioned, cached, batched) + `drift.py` (verdict
  + publishable + `DRIFT_TRIGGERS` invalidation) + the eval worker + `assert_publishable`.
- **3.c (Refactor):** verdict cache keyed `(caseId, baselineHash, candidateHash, judgeVersion)`; Batch API;
  deterministic pre-filter before judge.

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

| Step | Spec Kit / engineering gate | Gate (must be true to advance) |
|------|----------------------------|-------------------------------|
| **Repository bootstrap / feature start (DoR)** | `$speckit-constitution` once, then `$speckit-specify` + `$speckit-clarify` per feature | Constitution is ratified; user outcome and acceptance scenarios are unambiguous; data/security boundaries and compatibility are explicit |
| **Plan** | `$speckit-plan` | Design fits the existing service boundaries; APIs, data ownership, failure behavior, privacy, and operational needs are specified |
| **Readiness** | `$speckit-checklist` + `$speckit-tasks` + `$speckit-analyze` | Every acceptance criterion maps to a dependency-ordered task, a verification method, and a named command; blocking ambiguity or cross-artifact conflict is resolved before implementation |
| **.a RED** | `$speckit-implement` + approved task + TDD | Add the smallest relevant failing test before production code; run it and retain evidence of the expected behavioral failure; show test files and evidence to the user and get confirmation before implementation |
| **.b GREEN** | Verification | Make the minimum change; run the focused test, then relevant regression tests and applicable plan checks; retain command/output evidence and make no pass claim without it |
| **.c REFACTOR** | Review | Refactor without changing approved behavior; rerun affected tests and checks and keep them green |
| **Module done (DoD)** | `$speckit-converge` + CI/review | Tests, lint/build, security/eval gates, and review evidence are complete; report what was and was not verified; PR only when asked |

### B. Progress tracking (three layers, always in sync)

1. **Spec Kit artifacts** — one ratified project constitution at `.specify/memory/constitution.md`; one
   bounded feature per `specs/<feature>/` directory with `spec.md`, `plan.md`, `tasks.md`, and the generated
   requirements checklist. Add `research.md`, `data-model.md`, `quickstart.md`, and contracts when required
   by the feature plan. These reviewed artifacts are the actionable source of truth; `mvp/` viability is
   the first feature.
2. **This plan file as the roadmap** — the stage/module status table below records cross-feature order and
   completion; it must not duplicate per-feature task detail.
3. **Git + CI as the evidence signal** — feature branches/worktrees and CI evidence after the repo's Git
   toolchain is operational. A production module is green only when its deterministic suite, required AI
   eval, secret scan, and tenant-isolation checks pass.

**Module status table** (maintained in this file as work proceeds):

| Module | Stage | Core? | DoD gate (headline) | State |
|--------|-------|-------|---------------------|-------|
| MVP viability experiment (`mvp/`) | Pre-production | — | H1/H2 evidence, reproducible scorecard, documented GO/NO-GO | H2 harness implemented; local log has 8 calls, including one empty Ollama output; assertion report not retained; the OpenAI profile has stale lifecycle/pricing. User approved H1 candidates/protocol and H2 `gpt-5.6-terra` + Ollama under a $10 cap; exact corpus is unpinned and two human calibration raters are unavailable. Existing OpenAI log is historical evidence only. |
| **DE-RISK GATE** | Pre-production | — | H1 ≥80% detection / <10% false positives with calibrated judge; H2 cross-provider/stream/cost/substitution bars; write back to scope | Blocked until the experiment spec is complete and evidence is reproducible |
| M0 data model | 0 (after GO) | — | hash property tests + RLS zero-rows | **Data-model layer built + verified (feature `specs/001-m0-data-model`, 2026-11-03):** SQLAlchemy 2.0 async ORM (`services/api/models.py`), tenant-scoped `AsyncSession` factory as `ac_app` (`services/api/db.py` + `tenancy.py`), pure `VersionSnapshot` + `AgentVersion.to_snapshot()` (`services/runtime/snapshot.py`, reuses `hashing.py`), and migration `0004` (knowledge_bases + tool/skill/retrieval bindings + enums + RLS). 166 unit / 20 integration tests green on live Postgres — full services suite, re-verified 2026-11-03 (cross-tenant isolation, fail-closed, clone-stable + kb_index-excluded snapshot, immutability-raises, runtime purity). **Flagged follow-ups:** (a) `agent_versions` lacks doc columns `status`/`created_by`/`note`/`unique(agent_id,label)` — reconcile in a later migration; (b) binding `*_id` registry FKs + per-binding immutability triggers deferred (M4+); (c) `0.a` finding tests F16/F17 (validated-model hashing, CHECK constraints) not yet done. **0d cross-cutting (2026-11-03): settings layer + CI built + verified** — typed `pydantic-settings` config with fail-closed DB-driver validation (`services/api/settings.py`, 6 tests; `db.py` now sources URLs from it) and a 4-job GitHub Actions gate (`.github/workflows/ci.yml`: services unit + Postgres-backed integration + mvp offline + gitleaks secret scan with a dev-cred allowlist `.gitleaks.toml`). All four job sequences verified locally (services 166 unit / 20 live-DB integration / mvp offline / gitleaks clean). **CI note:** the workflow was briefly removed in commit `59abc93` and restored on 2026-11-03 (see the `scope.md` decision log) — it is the tracked DoD gate. The CI service container uses Postgres port 5432; the local compose stack publishes 5433 (see `ci.yml` port note). **Viability gate recorded as 🔴 GATE-OPEN (no GO)** in `docs/viability-gate.md` + `scope.md` (2026-11-03): H2/H1 mechanically wired but blocked on the owner-gated paid `gpt-5.6-terra` run and κ-rater calibration; owner authorized building the walking skeleton in parallel with the publish-gate held **advisory-only** (P4). OpenAPI-drift CI check deferred to Phase 4 (no API yet). |
| M1 gateway | 1 (after GO) | #1 | cross-provider contract + honest cost + explicit substitutions (F1–F4 validators are **M2** scope, not M1) | **Provider gateway built + verified (feature `specs/002-provider-gateway`, 2026-11-03)**, under the 2026-11-03 advisory-gate authorization (production expansion in parallel with the viability gate held 🔴 GATE-OPEN; publish-gate advisory-only). **Transport decision LOCKED: LiteLLM transport-only** — single seam `runtime/providers/transport.py` (`acompletion(stream=True, drop_params=False)`), litellm `response_cost` never read (P7/R1-R3). **Loader:** `resolve_profile` implemented (content-match on provider+model from `runtime/providers/profiles_data/*.json`, `@lru_cache`, fails closed with FileNotFoundError, FR-008). **3 adapters** (openai, ollama, **net-new anthropic**) on shared `_common` plumbing; per-provider usage mappers (OpenAI-shaped + Anthropic cache_read/cache_creation add-back, robust to both native-exclusive and litellm-normalized shapes). **Gateway:** resolve → adapter → OUR cost (`compute_cost`/`quantize_usd`, provisional→`cost_is_estimate`, FR-010) → substitution merge (preflight `supports()` + runtime wire-gaps, deduped) → fail-closed unknown model + **retired-model guard** (`ModelRetiredError`) → transport-error normalization to one `ERROR` event (no raw SDK type, api_key scrubbed, FR-009/FR-012/P8) → interrupted-stream (no USAGE) → degraded ERROR. **Capability honesty (US3/P7):** native features (response_format/tool_choice/reasoning_effort) forwarded only when the profile supports them, else omitted + emulated-via-prompt; every downgrade recorded as a Substitution (incl. M1 wire-gaps: cache breakpoints deferred to M7, token-budget reasoning). **8 profiles** in `profiles_data/` (openai gpt-5/gpt-5-mini, ollama llama3.1/3.2, anthropic opus-5-5/sonnet-5-5/haiku-4-5[deprecated]/judge haiku-5-5); pricing sourced from `mvp/profiles` + `frontend/src/lib/data.ts` (+ judge price cited from the claude-api skill), all `provisional` until the owner-gated paid H2 reconciliation. **166 unit / 20 integration tests green** (re-verified 2026-11-03); `test_runtime_purity.py` green (runtime/ imports no inbound web framework, P6); TDD per P9 (genuine behavioral RED before each GREEN; no live/paid calls — transport seam mocked). **Deferred:** structured `TOOL_CALL_*` events → M2 (C1, M1 adapters emit TEXT_DELTA+USAGE only); judge-model naming reconciliation (docs Haiku 4.5 vs approved `claude-haiku-5-5`) → M3; real Vault key custody → P1 (skeleton uses config/env). |
| M2 runtime.invoke | 1 (after GO) | spine | pipeline + deadline + AsyncSession-under-load + **F1–F4 (output validate/repair, tool-call enforce, tokenizer) + TOOL_CALL_*/STOP/finish_reason surfacing + transport timeout** (all deferred here from M1) | **M2a spine built + verified (feature `specs/003-runtime-invoke-spine`, 2026-11-04)** — split from M2 per the architect review (M2 was a cliff; validators → M2b). **Single pure `runtime.invoke`/`invoke_result`** (one shared path for all callers, no caller-branching) as a **plain async pipeline — NOT LangGraph** (research D1; LangGraph deferred to the first branching feature). Reads a **frozen `VersionSnapshot`** and builds the **FROZEN `CapabilityProfile` from `snapshot.profile_snapshot`** — never re-resolves live profiles (P6 immutability + P7). **Pipeline (fail-closed front):** tenancy well-formedness (non-empty `workspace_id` on ctx-or-snapshot, else `config_error` — converge C1/P5) → input-validation (zero-spend reject, gateway never called) → context-budget guard (`tokens.count_tokens`, conservative **over-estimating** heuristic — exact tokenizers deferred) → `gateway.stream(…, timeout=deadline)` → re-surface events → **deadline-bound** (`asyncio.timeout(deadline)` + wire-level transport timeout, T012/D5) → **exactly one honest `RunRecord`** via `ctx.record_run` sink (**never persisted by the runtime** — D2/D3). **Outcomes:** success / invalid_input / context_overflow / config_error / provider_error / timeout / degraded (`bounded_exit` reserved for the M2b+ tool loop). **US4 fidelity:** reasoning / tool_calls / stop_reason accumulated + surfaced; a tool-call-only response is a **non-empty success** (closes review C4). **Cost carried from the gateway USAGE, never recomputed** (FR-014/P7); `cost_is_estimate`/`substitutions` carried verbatim. `max_tool_calls` **carried-but-not-enforced** (clarification D6). **Fully mocked (SC-008)** — no live/paid call anywhere; the first live call is deferred to **Phase 5**. **26 M2a tests** (TDD, genuine behavioral RED→GREEN per P9); **full unit suite 195 green**, `test_runtime_purity.py` green (P6 — `runtime/{context,run,tokens,io,invoke}.py` import no inbound web framework). **Deferred to M2b:** F1/F2/F3 output/tool-call **validators + repair loop** (the AI-output trust boundary). **Known gap (honest):** M1's `stream_via_transport` still emits only `TEXT_DELTA`+`USAGE` from litellm chunks — the runtime **surfaces** `REASONING_DELTA`/`TOOL_CALL_*`/`STOP` when the gateway yields them (mock-verified), but making the transport **emit** them from real litellm chunks is live-integration work (Phase 5). |
| M3 eval/drift | 1 (after GO) | #2 | verdict branches + publish-gate + determinism (F18) | **M3a scoring core built + verified (feature `specs/004-eval-drift-engine`, 2026-11-05)** — built under the 2026-11-03 advisory-gate authorization (viability gate 🔴 GATE-OPEN; publish-gate held advisory-only). **Pure library `services/evals/`** (purity test green — no web/DB imports, P6), split M3a-style from the full M3 (persistence/gate/worker deferred). **Layer 1 deterministic** (`deterministic.py`, verbatim port of `mvp/evals/deterministic.py`): empty/first-person-refusal/malformed → hard_fail (skips judge); missing-field/out-of-enum/expected-mismatch → soft; task-agnostic via per-case `required_fields` (triage default); deterministic (F18). **Verdict math** (`drift.py`, verbatim port of `mvp/evals/drift.py`): load-bearing ordering `low_confidence → hard-assert → quality → efficiency → pass` under a per-workspace `EvalPolicy`; `score_quality` (1–5→0–100), `efficiency_delta` (cost%, baseline-zero safe); added `verdict_with_reason` for specific case-grounded reasons (FR-009); `DRIFT_TRIGGERS` ported as a constant (NOT wired to a gate here). **Advisory judge** (`judge.py`, net-new): runs through the **same `runtime.invoke_result`** (P6), pinned to **`claude-haiku-5-5`** / `judge_version="judge-v1"` (resolves the docs-Haiku-4.5 skew, 0c), versioned identity+prompt-hash (P4), fail-closed `low_confidence` on unparseable output, seedable pairwise order; **advisory-only — never gates publish** (owner decision #3). **Runner** (`runner.py`, net-new): tenancy fail-closed (P5, cross-workspace rejected before any call), deterministic pre-filter → judge via an **instance-scoped circuit breaker** (F8 — independent of any future prod breaker), emits the **full frontend `EvalRun` shape** (quality/qualityDelta/costDelta/latencyDelta/dimensions[]/failingCases[]) with advisory `pairwise_rate`. **56 M3 tests** (TDD: deterministic/verdict ports landed as port-verification GREEN; judge/breaker/tenancy/runner written test-first with genuine behavioral RED→GREEN, P9); **253 unit tests green** (was 197 + 56), eval purity green, no regressions; **fully mocked — no live/paid calls** (SC-005), first live judge call → Phase 5. **Deferred to later M3/M4/M5:** publish-gate enforcement (`assert_publishable`/409) + wiring `DRIFT_TRIGGERS`→`publishable`, DB-backed `eval_baselines`/`eval_runs` + audited re-freeze, the verdict cache + C2 hash-coverage/`latest_trigger_time` tests, the async eval worker + `GET /jobs/:id`, Batch-API/incremental-eval cost controls, and judge **κ calibration** (what later flips the judge from advisory to gating). **Honest gaps:** FR-024 (cache-off) is a forward marker only — the M2a runtime has no response cache, so eval runs are tagged `source="eval"` for a future cache to honor; judge pairwise is order-randomized but labeled (blind A/B de-labeling → Phase-5 hardening, matters only for a live judge). |
| M4 API | 2 | — | typed SSE frames (F9) + idempotent creates (F14) | **M4a thin-API slice built + verified (feature `specs/005-thin-api-tenancy`, 2026-11-05)** — the first HTTP caller layer, built under the 2026-11-03 advisory-gate authorization. **Net-new `services/api/`** FastAPI app (`main.py` + `deps.py` + `sse.py` + `schemas/` + `routers/`) wrapping the pure cores — `services/runtime` + `services/evals` stay import-pure (purity tests green; only `api` imports FastAPI, P6). **US1 SSE invoke** (`POST /api/v1/agents/:id/invoke`, console-session plane) wraps the SAME `runtime.invoke` and re-encodes `NormalizedStreamEvent`→typed SSE frames (`run_start`/`text_delta`/`reasoning_delta`/`tool_call_*`/`usage`/`error`/`done`, F9); a runtime error → typed `error` frame + `done` (no raw 500); invalid input → rejected pre-call (zero spend). **US2 fail-closed tenancy (P5)** — `deps` resolves+authorizes the workspace (403 on unheld claim, 401 no principal) then opens `tenant_session` (ac_app + GUC + RLS); **negative cross-tenant → 404 verified on live Postgres**. **US3 agents CRUD** — create/get/list/PATCH + POST-version, typed Pydantic mirroring `data.ts` + the `errors.py` envelope + cursor pagination; **idempotency in-flight guard** (409 `idempotency.in_progress`, F14); **optimistic concurrency via Postgres `xmin` ETag** (409 `conflict.stale`, If-Match) — migration-free. **US4** — `POST eval-runs` runs the M3 engine **inline** over the shared runtime (suite in the body), `POST publish` is **advisory** (returns verdict, never raises `publish.eval_gate` — owner decision #3). `rate_limit()` seam (F5). **21 M4 tests** (TDD: router behavior written test-first, genuine RED→GREEN, P9); **300 tests green** (279 prior + 21), run against the live compose Postgres (migrations 0001–0004); **fully mocked at the provider boundary — no live/paid calls** (SC-007), first live call → Phase 5. **No new dependencies; no new migration.** **Deferred to M4-remainder/M5:** the 253-seam CRUD, caller bearer-token plane + token mint, real OIDC/IdP (18) + Vault + full RBAC + org-admin plane, the async worker + `GET /jobs/:id`, Redis idempotency replay, real rate-limit thresholds, ClickHouse/Langfuse, the real publish gate (κ-calibrated), and the FE→generated-client drift-check (M12). **Honest gaps:** created agents store an empty `profile_snapshot` (profile freezing at create deferred — invoke is proven with seeded versions); the invoke run record goes to an **in-memory sink** (no `runs` table yet — persistence deferred). <br>**M4-remainder backend built + verified (feature `specs/006-resource-crud-sync`, 2026-11-05):** migration `0005` adds 7 workspace-scoped RLS tables (`tools`, `mcp_servers`, `mcp_tools`, `skills`, `workspace_settings`, `members`, `runs`) + `agents.needs_reeval` + `archived_at`. New `services/api` routers: **US1** tool/MCP/skill catalog CRUD (archive/restore, `xmin` ETag, idempotency, FR-021 secret rejection, unsigned-provenance skill publish, published-edit→draft); **US3** KB metadata CRUD (ingestion honest-deferred, M7); **US4** workspace settings/limits/eval-policy + member roster (role stored, **not enforced** — RBAC → M5); **US5** durable `runs` persistence (**closes the in-memory-sink gap** — invoke + eval now record identity+metrics only, no raw content, SC-009) + 30-day daily-bucket dashboard metrics. Deterministic `drift.py` flags bound agents on a trigger-surface edit (tool auth/scopes, skill content, KB config, MCP-tool grant). **365 tests green** (unit+integration on live Postgres, purity intact, no live calls); post-`/code-review` hardened (recursive secret scan, duplicate→409, MCP If-Match, archived-publish guard, paginated MCP list). **Still deferred to M5/M7/P2:** provider keys/tokens/tool-approval + Vault custody, OIDC/IdP + full RBAC + access-policy, real ingestion, channels, workspace-root create/delete, async-worker deployable. **US2 frontend sync (client-gen + hook swaps + build verify) is NOT done — blocked on an unavailable Node/npm toolchain in the build env; OpenAPI 3.1 emitted to `frontend/openapi.json`, `gen:api` script + `openapi-typescript` devDep wired, tracker at `docs/backend-integration-status.md` (0/253 seams wired).** |
| M5 security | 2 | — | isolation suite + `rate_limit()` (F5) + P1 SSRF (F19) | **M5 RBAC-enforcement slice built + verified (feature `specs/008-rbac-enforcement`, 2026-11-06)** — closes the biggest enterprise-readiness gap: `deps.resolve_workspace` authorized membership but **ignored the claimed role**, so every role (incl. viewer) could do everything. Now a server-side permission model (`api/permissions.py`, the 4-role×19-permission map **generated from `frontend/src/lib/permissions.ts`** via `scripts/gen_rbac.py` → `rbac_permissions.json`, with a drift test asserting parity — no Node needed) + a `require_permission(...)` dependency on **all 47 workspace-scoped routes** (operation→permission contract in the feature's data-model). **Deny-by-default by construction** (a coverage test fails on any `/api/v1` route lacking a permission, FR-014); **fail-closed** (unknown/empty role → zero permissions → 403, P3); **403 before resource load** (no existence leak, P5); role check **composes with** membership + RLS (defense in depth). Role sourced from the resolved principal claim. **407 tests green** (+15: model/parity, per-tier allow/deny matrix incl. envelope-shape + 403-before-404 + invoke-denial-before-SSE, deny-by-default coverage); existing publish/members/settings tests moved to publisher/admin claims (FR-013 — itself evidence enforcement works). API-layer only — `runtime/`+`evals/` untouched (P6); no new deps, no migration. <br>**M5 pluggable-IdP authN slice built + verified (feature `specs/012-pluggable-idp-auth`, 2026-11-08)** — replaces the UNVERIFIED `X-Dev-Principal` trust with real bearer-token verification behind a **vendor-pluggable seam** (`api/auth/`: `base.IdPProvider` protocol → `dev` + `trellix-iam` providers → `registry.get_active_provider`, config-selected via `AC_AUTH_PROVIDER`/`AC_ENV`). `JwtBearerProvider` (PyJWT+JWKS) verifies signature (RS256 allow-list, `alg=none`/HS* rejected) + `iss` + `aud`=`mcafee` + `exp`/`nbf` within a bounded skew, JWKS fetch+cache w/ refresh-once-on-unknown-`kid` (FR-017); fail-closed 401 on every failure, **no token/secret in logs** (FR-011). AuthN only (FR-013): the AC **role is sourced from the `members` store** (feature 006) for the asserted workspace under RLS — `resolve_workspace` resolves it; `require_permission` (008) unchanged. The dev provider (verbatim old header path) is **disabled under `AC_ENV=prod`** (FR-008), preserving the ~447 existing tests. **471 tests green** (+24: JWT verify matrix, registry/prod-gate, API-edge 401 matrix + no-leak, membership-sourced authZ incl. cross-tenant deny); **live Trellix-IAM token verified end-to-end** through the real provider against the live JWKS (opt-in `scripts/iam_live_check.py`, no secret in CI — FR-015/SC-007); purity green (P6). One dep (`pyjwt[crypto]`), no migration. <br>**M5 per-resource access-policy slice built + verified (feature `specs/013-access-policy-enforcement`, 2026-11-09)** — enforces the `access_policy` JSONB that every workspace row already carried (default `{"visibility":"workspace"}`) as a deterministic, fail-closed control (P3) **on top of** RBAC (008) + membership (012) + RLS. `api/access_policy.py` is a pure evaluator (`can_use`/`can_manage` over the raw stored dict → `PolicyPrincipal{role, teams, subject}`): three tiers — `workspace` (all members use/manage), `restricted` (role AND team, **separate USE vs MANAGE** role+team lists), `private` (owner subject + admins) — with **fail-closed matching** (a restricted policy naming neither role nor team grants no one) and **admin bypass** (so an admin can always repair a broken policy). The enforcement spine in `deps.py`: `access_principal` builds the caller view (role from the resolved claim, `teams` via one RLS-scoped `membership.teams_for`, subject); `ensure_use` raises **`NotFound` on deny so visibility is never leaked** (P5 — a denied resource is byte-identical to an absent one); `ensure_manage` is **404-then-403** (not-usable → 404, usable-but-not-manageable → 403, after the RBAC gate). Wired across every read/list/get/manage/invoke/eval-run point in `agents`/`knowledge`/`skills`/`tools`/`invoke`/`evals` routers (lists filter by `can_use`; create/patch stamp the `private` owner). An **executer** caller (token/channel plane, no console role) is evaluated on **TEAMS alone** — a role-only policy denies it; `private` denies every runtime caller (contract defined here; token mint itself is the NEXT feature). One structured **non-secret deny log** per refusal (capability/tier/subject/teams/resource — no policy body, no credential). Migration **0006** adds `member.teams` + backfills; FE contract synced (member `teams` surfaced read-only on the roster per FR-017, `openapi.json` + typed client regenerated, build + lint clean). **533 tests green** (+ deny-log); API-layer only — `runtime/`+`evals/` untouched, purity green (P6); no new deps. <br>**M5 rate-limiting slice built + verified (feature `specs/016-m5-rate-limiting`, 2026-11-11)** — closes finding **F5**: the no-op `rate_limit()` seam (`deps.py:139`) becomes a real per-identity **sliding-window** limiter. New shared async **Redis client** (`api/redis_client.py` — first consumer of `AC_REDIS_URL`, None-safe no-op-allow when unset; also feeds 018/021), a pure **identity-agnostic engine** (`api/ratelimit.py` `check(key,limit,window_s)` → `Decision`; ZSET sliding-window *log* in one `MULTI`/`EXEC` pipeline with a compensating `ZREM` so a rejected hit never consumes a slot — no Lua, keeps the test path `lupa`-free), and a `rate_limit(route_class)` **dependency factory** replacing the no-op. Keyed **per principal** (`rl:prin:{subject}:{route_class}` — no token/secret in the key, P5); per-**token** invoke keying deferred to 018 via the same engine. Reviewable **limits table** in settings (`invoke` 120/60s, `playground` 60, `eval` 20, `ingest` 20, `default` 300 — conservative tunable defaults, fallback-safe via `rate_limit_for`). Wired on all **32 gated endpoints** across 9 routers (1 `invoke`, 1 `eval`, 30 `default`; `ingest` class reserved for M7 ingestion). 429 `rate.limited` carries `Retry-After` + `X-RateLimit-*` (new back-compatible `AppError(headers=…)`); **fails open + alert on a Redis outage** (availability control, FR-024a — `ac_rate_limit_degraded_total`; deny-set/spend fail *closed*, 018/021); 429s counted via `ac_rate_limited_total{route_class}`. On invoke the limiter runs after authz and **before** the spend-cap DB read (no abuse→ledger amplification). **571 tests green** (1 integration skipped; +9 rate-limit TDD: window/isolation/reset/fail-open/limits-table/headers/no-secret, Red shown before Green per P9); full regression incl. P6 purity intact — API-layer only, `runtime/` untouched; one **test** dep (`fakeredis`), `redis` already present, no migration. <br>**M5 SSRF / egress-guard slice built + verified (feature `specs/017-m5-ssrf-egress-guard`, 2026-11-12)** — closes finding **F19** at the P1 app level: a pure validator `api/egress.py` (no web/DB imports) that an eventual HTTP-tool executor calls before every outbound dial. Scheme allow-list → **per-tool host allow-list, deny-by-default** → resolve → block **private/loopback/link-local/multicast/ULA/reserved/cloud-metadata** (IPv4+IPv6, 169.254.169.254 + fd00:ec2::254) → operator **extra-deny CIDRs** (`AC_EGRESS_EXTRA_DENY_CIDRS`, CSV, CIDR-validated at load, fail-closed config) → **DNS-rebind/redirect re-validation** (`validate_connected_ip`/`validate_redirect` re-check the *actually-resolved* IP, not just the hostname). The baseline block-set is **always on** and keys off no enable flag (security control, fails closed, FR-027). `EgressBlocked`→**502 `egress.blocked`** (owner-accepted code choice); `ac_egress_blocked_total{reason}`. **Built UNWIRED by design** — no tool-execution path exists in P1 (sandboxed tool calls are P2), so the guard ships as a verified library with a documented integration seam, *not* yet called on a live path. **46 egress tests** (TDD, infra-free: DNS is an injected resolver, "zero bytes sent" proven by asserting refusal *before* the resolver is consulted, Red shown before Green per P9); purity intact, no new deps, no migration. <br>**M5 caller-token mint + deny-set slice built + verified (feature `specs/018-m5-caller-token-denyset`, 2026-11-12)** — builds the **executer caller plane** the access policy (013) already contracted for. Migration **0008** adds `api_tokens` (workspace-scoped, RLS enabled+forced, `token_hash` UNIQUE, `expires_at>created_at` CHECK, no DELETE grant) plus `api_token_resolve(hash)` — a **SECURITY DEFINER** function that performs the one unavoidable cross-tenant lookup (the workspace is *derived from* the token, so RLS can't be set yet) through a **narrow owned bypass**, so `ac_app` never bypasses RLS broadly (new delta vs the umbrella design). `api/publish/token_auth.py::resolve_caller` hashes the bearer (sha256+prefix, raw secret shown **once** at mint, FR-010/011) → resolves → unknown/revoked/expired → 401 → **deny-set** → per-**token** rate-limit `rl:token:{id}` → `CallerCtx{role=executer, subject="token:{id}"}`. `api/auth/denyset.py` is a Redis **deny-set that fails CLOSED** on a configured-Redis outage (the exact inverse of 016's availability-oriented limiter — a revocation DB that silently fails open is a security hole, FR-024b); TTL floored ≥1h so it outlives any token/session (FR-014). The deny-set is checked on **both** invoke planes — console (`session_principal`, so a revoked human session is cut too; D2-accepted that this makes configured-Redis a soft-SPOF for the console, validated in 021) and published — including a **throttled in-flight stream re-check** (`AC_DENYSET_STREAM_RECHECK_S`) that cuts a mid-stream revoke (FR-012). `routers/tokens.py`: mint/list/revoke on `agent.publish`, `POST /org/revoke-user` on `member.manage` (**D5 — reuses existing permissions, no new `token.manage`**). The **published invoke plane** `POST /v1/agents/:id/invoke` (`published_router`) shares the SAME refactored `_run_invoke` body as the console route (P6 — the two planes can never drift), building a `PolicyPrincipal{role=executer, teams=caller.teams}` so 013's team-only executer gate applies; a token scoped to agent A invoking agent B → 404 no-leak. **648 tests green** (full regression; +31 TDD across token mint/list/revoke, deny-set fail-closed + stream-cut, migration-0008 RLS/SECURITY-DEFINER grant, Red shown before Green per P9; coexists with 017's 46). API-layer only — `runtime/`+`evals/` untouched, purity intact; no new runtime deps (`fakeredis` test-only, already present). **Deltas vs umbrella:** migration is **0008** not 0007; the SECURITY DEFINER resolver is new; durable `audit_logs` deferred (no audit-row infra exists yet → structured log for the `revoke-user` event). **Still deferred (M5-remainder):** console user-login (auth-code/OIDC) flow (019), IdP group sync (020), Redis degraded-mode + PgBouncer hardening (021 — validates the deny-set/limiter SPOF behaviour end-to-end); tool approvals + the live wiring of the 017 egress guard await the P2 tool-execution sandbox. |
| M6 obs/cost | 2 | — | failure-rate counters (F12) + correlation (F13) + budget durability (F7) | **M6 durable spend-cap slice built + verified (feature `specs/009-spend-cap`, 2026-11-07)** — first M6 cut: a **durable monthly workspace spend cap** (F7's durability half). `api/spend.py` sums month-to-date spend from the **Postgres `runs.cost_usd` ledger** (current calendar month, UTC) — no evictable counter is the source of truth, so a cache loss **cannot un-cap** (F7). An `enforce_spend_cap` dependency gates **both paid paths** (`POST …/invoke` and `…/eval-runs`): when month-to-date ≥ the workspace cap (`WorkspaceSettings.limits.monthlySpendUsd`, default 2000) it raises **`402 budget.exceeded`** (cap + MTD in `detail`) **before any provider call or SSE frame**, with **no Run row** for the refusal (surfaced via the 402 + a structured log). **Fail-closed** (FR-009): a spend-computation error refuses with a controlled 500 (`spend_indeterminate`), never a false 402. **Authz precedes budget** (403 before 402 — the gate sits after `require_permission`, header-only authz resolves first); workspace-scoped (RLS + explicit filter — a tenant's spend never counts for another). New `GET /api/v1/workspace/spend` (perm `view`) reports MTD vs cap from the **same helpers the gate uses** (one SoT). **423 tests green** (+16: pure model, over-cap deny on both paths, under-cap/prev-month/unset-cap/cross-tenant allow, fail-closed, authz-ordering, spend view); API-layer only — `runtime/`+`evals/` untouched (P6); no new deps, no migration. FE contract synced (`WorkspaceSpend` type + `useWorkspaceSpend()` seam + 402 note) — **build-verified 2026-11-07** via `nvm use 24` (lint + `next build` pass; the earlier "Node unavailable" note was wrong — nvm wasn't loaded in the non-interactive shell). <br>**M6 run-observability slice built + verified (feature `specs/010-run-observability`, 2026-11-07)** — the infra-light halves of F12+F13. **F13 correlation ingestion:** `api/correlation.py` resolves an inbound id at the edge (W3C `traceparent` trace-id → `X-Correlation-Id` → generated; shape-validated, malformed ignored — P3) and both paid handlers thread it into `RunContext.correlation_id` (previously always overwritten with a fresh uuid) and echo it on an `X-Correlation-Id` response header. **F12 run metrics:** `api/metrics.py` (prometheus_client, custom registry) exposes `ac_runs_total{status,source}` (+1 per recorded run, in `run_sink`) and `ac_requests_refused_total{code}` (402 budget.exceeded + 403 access.denied, in `app_error_handler` — refusals that never become a run) at an **unauthenticated `GET /metrics`** (non-`/api/v1`, aggregate-only — no tenant/content). Telemetry **best-effort** (a metrics error can't fail a run/persistence, FR-009). **445 tests green** (+22); API-layer only — `runtime/`+`evals/` untouched (P6); **one dep** (`prometheus-client`), no migration. **Still deferred (M6-remainder):** Redis in-flight reservation (tighten the bounded concurrency overshoot ≤ in-flight × largest-single-run cost), per-agent spend cap, per-request preflight, Langfuse v3 spans / ClickHouse warehouse / per-call ModelCallLog, correlation propagation to the outbound provider call, deadline-rate histogram. |
| M7 min-RAG | 2 | — | right-chunk + indirect-injection scan (F6) | ☐ todo |
| M8–M10 P0 | 3 | — | reg-gate / org-metadata-only / notify-once | ☐ todo |
| M11 deploy/scaling | 4 | — | worker split (F21) + concurrency suite (F7/F8) | ☐ todo |
| M12 frontend wiring | 4 | — | All current frontend backend seams covered by approved feature specs and wired to the OpenAPI contract | ☐ todo |

### C. Debugging protocol (by failure class)

Governing rule for all classes: reproduce the failure, identify its root cause, then fix; never patch
speculatively or fix a failing eval by loosening a threshold without a reviewed experiment decision.

| Failure class | First move | Tools / substrate |
|---------------|-----------|-------------------|
| Deterministic test fail | the RED test *is* the repro; bisect to root cause | pytest `-x`, the failing assertion |
| Eval/drift regression or flaky verdict | inspect the failing case's trace; confirm pinned model ID, fixed API settings, prompt/rubric hash, and calibration result; inspect repeated-run verdict flips. Do not assume `temperature=0`; if a real behavior gap, **add a regression eval case** (feedback loop), don't move the bar | per-call JSONL log, Langfuse trace, the two-layer harness |
| Provider / transport failure | reproduce with a fault-injecting adapter stub | breaker state (Redis), retry/backoff + `Substitution` records |
| Tenancy / RLS leak | run the isolation suite; trace the `SET LOCAL` GUC | `pg_policies`, GUC trace, fail-closed default |
| Cost mismatch | reconcile computed ↔ hand-calc ↔ provider billing | capability profiles (the SoT), `compute_cost` |
| "Why did it answer differently?" | replay the run from its `VersionSnapshot` + `trace_id` | **immutable snapshot + correlation id (F13) + JSONL log = any run is replayable** |

Local observability available from day one: Langfuse UI, structured JSON logs keyed by correlation id,
`/healthz` + `/readyz`, Prometheus counters.

### D. Environments & go-live ladder (local → qa → prod)

> **Terminology:** in *this* repo "sandbox" is the **P2 gVisor tool-exec unit** (a follow-on phase, not
> the first internal pilot), **not** a staging env. The pre-prod ladder is **local (docker compose) → qa →
> prod** (`settings.env ∈ dev|qa|prod`).

- **Local-first, always.** `deploy/compose` brings up Postgres+pgvector, Redis, Vault, Langfuse v3 +
  ClickHouse, MinIO. The standalone experiment uses local Ollama plus only explicitly approved, capped
  hosted-model spend; H1 cannot be assumed to run entirely locally. No production backend expansion starts
  before the documented viability GO.
- **Spend safety (scope.md ≤ $100/mo).** Live provider spend is gated on an explicit greenlight; the
  **enforced budget cap (M6) must be verified locally** (it actually refuses over-spend) **before any real
  provider key is ever loaded**.
- **Secrets safety.** No real keys/tokens/PAT-bearing remotes in code, commits, or output —
  ever; Vault only; dev uses cheap/local creds.
- **Deploy safety.** Feature branches/worktrees only; **never** push to `main`/prod or run a
  deploy without explicit in-session confirmation (a push to an auto-deploy branch *is* a prod deploy).
- **Per-stage pre-live checklist** (gate before promoting beyond local):

  | Stage | Must be green locally before promotion |
  |-------|----------------------------------------|
  | Viability gate | H1/H2 pass bars and judge agreement threshold locked before measurement; scorecard reproducible; scope decision recorded |
  | Internal pilot | Tenant-isolation suite green; secret scan clean; rate limits active; budget cap refuses over-spend; playground first-token p95 < 2s |
  | Governance | Registration gate enforced; cross-workspace report metadata-only for private resources and audited |
  | Ship | Concurrency suite green; full compose stack healthy end-to-end; frontend build/lint green — then, only with explicit confirmation, promote QA → prod |

- **How to run it:** services via compose + `uvicorn`; frontend `cd frontend && npm run dev` (**verify Next
  16.3.8 APIs against `node_modules/next/dist/docs/` first**); use the `run` skill to drive/screenshot the
  app; `ai-monitor` once anything is live.

## Cross-cutting SDD + TDD conventions (apply to every feature)

- **Design the contract first; implement it test-first.** Define intended contracts in the reviewed feature
  spec/plan. Then add and run tests before production types/interfaces, Pydantic v2 boundary models, frozen
  dataclasses (`VersionSnapshot`/`RunContext`), validators, migrations, or behavior. For M1/M3/M7, F1–F5
  validators, the tokenizer, and `rate_limit()` are contract/design work in `.a`; executable code follows
  their Red tests.
- **Strict Red→Green→Refactor.** RED must fail for the intended behavioral assertion, not because the
  test cannot compile or the environment is broken. Output the **test files and expected-failure evidence
  first and get confirmation** before implementation code (the user's rule). GREEN is the smallest change
  plus focused and relevant regression checks. REFACTOR preserves the approved behavior and reruns affected
  checks. Keep one todo per TDD step; never skip or weaken a test to force Green.
- **Keep bootstrap non-behavioral.** If a test needs a package skeleton or harness setup before it can run,
  list that setup in the task and keep it free of the behavior the Red test is meant to prove.
- **Resolve ambiguity at the spec boundary.** If a test exposes an unspecified or conflicting behavior,
  pause implementation, update and review the feature artifacts, then update verification to match the
  approved decision. Record and get project-owner approval for any exception to test-first sequencing.
- **Right-sized verification.** Docs-only changes use link, consistency, and rendering checks; they do not
  need invented unit tests. For AI behavior, deterministic tests remain ordinary CI checks; live model calls
  and paid evaluations follow the approved frozen-set, calibration, preflight, and spend gates.
- **Defensive AI boundaries.** Every LLM call result passes a strict validator (F1/F2/F3) before it's
  trusted; transport gets breaker + capped backoff + fallback (M1).
- **Observability-ready.** Structured JSON logs with a correlation id threaded UI→API→runtime→provider
  (F13); the per-call log line emitted by one shared helper (M6).
- **Two-layer AI testing.** M1/M3/M7 carry a versioned-prompt eval set vs a frozen baseline in CI
  alongside deterministic tests; a model- or knowledge-affecting change must show a PASS drift report.
- **Commit convention:** infer from `git log` (no enforced style yet in this repo); never commit to
  `main`; branch + PR only when explicitly asked. No deploy/push without in-session confirmation.

## First production file candidates (after viability GO; refine per feature plan)
`services/runtime/providers/abstraction.py` (IR + adapters + profile resolution + **the F1–F4 validators +
tokenizer**) · `providers/profiles/*.json` · `runtime/invoke.py` · `evals/drift.py` (+ `deterministic.py`,
`judge.py`) · `api/models.py` + `migrations/` · `api/publish/endpoint.py` · `api/errors.py` +
`api/pagination.py` + `api/idempotency.py` (from `00L`) · `api/openapi` + `export/snippet.py`.

---

## Verification (how each stage proves itself)

- **Per module:** each behavior change starts with a valid Red result for the expected behavior; the new
  focused test and relevant regression tests pass at Green, then remain green after Refactor. No module is
  "done" until test-first evidence and all applicable checks named by its feature plan are retained.
- **Pre-production viability gate:** H2 contract tests (same `NormalizedRequest` across ≥2 providers,
  recorded substitutions, cost within ±5% of hand-calc) and H1 seeded-regression measurements (≥80%
  detection, <10% FP) with judge agreement at the criterion locked before measurement; write the result to
  `scope.md`. **A NO-GO halts production backend expansion.**
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
