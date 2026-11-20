# Implementation Plan: Per-Call Model Log + End-to-End Correlation + Latency SLIs

**Branch**: `feat/backend-m0-foundation` (slice 023; no per-feature branch) | **Date**: 2026-11-14 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/023-m6-call-log-correlation/spec.md`

## Summary

Close the spec-A2 contract and the remaining halves of findings F12/F13 without new infrastructure:

1. **Per-call record (US1)** — the shared runtime spine emits one pure `ModelCallRecord` per model turn to a new
   `RunContext.record_call` sink (sibling of `record_run`; runtime stays pure). The API sink (`api/call_log.py`)
   writes one JSON line per call to stdout: A2 fields + run/tenant context, with **metadata by default** (char counts +
   keyed per-workspace fingerprints) and redacted+truncated text **only on opt-in** (Q1 = A). Per-call costs are the
   gateway's turn Decimals, so they sum exactly to the run cost.
2. **Correlation (US2)** — the edge-resolved id (010) goes onto every call line; a **platform-generated** provider
   correlation id (== the correlation id when the platform minted it, else fresh per request — never the caller's
   value, U1) rides `RunContext → NormalizedRequest → litellm extra_headers` (`X-Correlation-Id`) and is also on every
   call line; provider response request ids captured when exposed (spike-gated).
3. **SLIs (US3)** — `ac_first_token_seconds`, `ac_run_latency_seconds`, `ac_deadline_exceeded_total` (+ a call-log
   error counter) observed at the existing exactly-once `metrics.record_run` site.
4. **Opt-in control** — `workspace_settings.content_logging` (migration 0012) + admin-only
   `GET/PUT /api/v1/workspace/telemetry` with an audit log line on change. A new shared redaction primitive
   (`runtime/guardrails/redact.py`) is introduced for reuse by future guardrails.

## Technical Context

**Language/Version**: Python 3.12 (services)
**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 async, litellm (transport), prometheus-client — all existing.
**No new dependencies.** stdlib `hmac`/`hashlib`/`logging`/`re`/`json`.
**Storage**: Postgres — one additive column `workspace_settings.content_logging boolean NOT NULL DEFAULT false`
(migration **0012**). Call-log lines go to stdout (log pipeline), never Postgres.
**Testing**: pytest + pytest-asyncio; httpx ASGITransport; transport seam mocked (no paid calls); prometheus
`REGISTRY.get_sample_value`; `caplog`/handler capture for `ac.call_log`; integration via local Postgres (5433).
**Target Platform**: Linux server (FastAPI `api`), local compose stack.
**Project Type**: web service (`services/`). Frontend: no change in this slice (telemetry endpoint recorded as
BUILT / FE MOCK in the integration status tracker).
**Performance Goals**: ≤ 5% median run-latency overhead vs stub provider (SC-007); per call = 2 HMACs + 1 JSON dump.
**Constraints**: runtime purity (P6); telemetry best-effort, never alters a run (FR-009); no content by default, no
credentials ever (P5, FR-002a/008); aggregate-only metrics (FR-018); no new infra (FR-019); prod requires
`AC_TELEMETRY_FINGERPRINT_KEY` (fail closed at settings load).
**Scale/Scope**: internal pilot; process-local metrics; one log line per model call (agentic runs emit several).

## Constitution Check

*GATE: passes before Phase 0; re-checked after Phase 1 design (below).*

| Principle | Gate | Status |
|---|---|---|
| **1 Verifiable outcome** | operator-visible result per story | PASS — line per call, one-id lookup, SLI metrics; quickstart matrix maps each SC. |
| **2 Scope / phases** | slice bounded, deferrals named | PASS — Langfuse/ClickHouse, budget reservation, eval budget, burn-rate alerts, FE UI out (spec Assumptions). |
| **3 Deterministic controls** | inbound id shape-validated; redaction deterministic | PASS — 010 validation reused + re-checked at transport; regex + exact-match redaction, no model in the loop. |
| **4 Evidence about behavior** | per-call evidence for eval/judge | PASS — judge calls logged with `role=judge`; no change to eval logic. |
| **5 Data / tenancy** | classification, purpose, access, retention/deletion, external processing, minimization, no creds | PASS — all five defined per data item in spec.md **§ Data Handling** (per-call metadata = Internal operational telemetry; opt-in text = Confidential customer content; fingerprint key = Secret; operator-only access via the log pipeline; 14–30 d window, no purge on disable — documented limitation; only new external flow = platform-generated provider correlation id header; caller-supplied ids never forwarded). Content OFF by default (Q1=A); keyed **per-workspace** fingerprints (no cross-tenant content correlation); exact-match key scrub; opt-in admin-only + audited. |
| **6 Reproducible versions** | snapshot identity unchanged | PASS — `correlation_id` added to `NormalizedRequest` only (not hashed); snapshot/prompt hashes untouched. |
| **7 Honest provider/cost** | single cost number; honest provider support | PASS — per-call cost = gateway Decimal, Σ == run cost (invariant test); per-provider correlation support documented as TBD until the spike verifies it. |
| **8 Operate / failures visible** | SLIs + best-effort + explicit degradation | PASS — first-token/latency/deadline metrics; sink failures counted (`ac_call_log_errors_total`), never a run failure. |
| **9 Spec / test-first / evidence** | TDD RED→GREEN, artifacts | PASS — quickstart RED→GREEN order; tasks will be test-first. |
| **Paid-call protocol** | no live provider spend | PASS — transport mocked throughout. |

**No violations.** One additive migration (0012). Zero new dependencies.

## Project Structure

### Documentation (this feature)

```text
specs/023-m6-call-log-correlation/
├── spec.md
├── plan.md              # this file
├── research.md          # D1–D12
├── data-model.md        # ModelCallRecord, CallLogLine, extensions, metrics
├── quickstart.md        # RED→GREEN order + validation matrix
├── contracts/
│   └── observability.md # log line, telemetry API, outbound header, metrics
├── checklists/
│   └── requirements.md
└── tasks.md             # /speckit-tasks (not created here)
```

### Source Code (repository root)

```text
services/
├── runtime/
│   ├── context.py                 # EDIT — RunContext.record_call (CallSink|None), content_logging, provider_correlation_id
│   ├── run.py                     # EDIT — ModelCallRecord (new frozen VO); RunRecord.first_token_ms
│   ├── invoke.py                  # EDIT — per-turn ModelCallRecord emission (try/finally, guarded sink call);
│   │                              #        first_token_ms capture; NormalizedRequest(provider_correlation_id=…)
│   ├── guardrails/
│   │   ├── __init__.py            # NEW
│   │   └── redact.py              # NEW — redact(text, known_secrets=()) shared primitive (D5)
│   └── providers/
│       ├── ir.py                  # EDIT — NormalizedRequest.provider_correlation_id
│       ├── _common.py             # EDIT — stream_via_transport(extra_headers from provider_correlation_id, 32-hex only)
│       └── {openai,anthropic,ollama}_adapter.py  # EDIT — pass req.provider_correlation_id through
├── api/
│   ├── correlation.py             # EDIT — resolve_correlation() → (id, supplied); provider id derivation
│   ├── call_log.py                # NEW — make_call_sink(ws, api_key, content_logging) → fingerprint/redact/JSON line; ac.call_log logger
│   ├── metrics.py                 # EDIT — 3 SLI metrics + ac_call_log_errors_total; record_run(rec) observes them
│   ├── run_sink.py                # EDIT — pass rec to metrics.record_run
│   ├── settings.py                # EDIT — AC_TELEMETRY_FINGERPRINT_KEY (prod-required), AC_TELEMETRY_LOG_CONTENT_IN_DEV
│   ├── models.py                  # EDIT — WorkspaceSettings.content_logging
│   ├── schemas/workspaces.py      # EDIT — TelemetrySettingsIn/Out
│   └── routers/
│       ├── workspaces.py          # EDIT — GET/PUT /api/v1/workspace/telemetry (+ audit line)
│       ├── invoke.py              # EDIT — load effective flag; ctx.record_call = make_call_sink(...)
│       └── evals.py               # EDIT — same for eval ctx (covers judge)
├── migrations/versions/
│   └── 0012_workspace_content_logging.py   # NEW — additive column, default false
└── tests/
    ├── test_call_record_emission.py   # NEW (pure) — per-turn emission, failure/interrupt paths, cost invariant
    ├── test_redact.py                 # NEW (pure) — patterns, exact-match, fail-closed, redact-before-truncate
    ├── test_call_log_sink.py          # NEW (pure) — line shape OFF/ON, fingerprints, error counter, no key leakage
    ├── test_outbound_correlation.py   # NEW (pure) — extra_headers propagation + bounds
    ├── test_sli_metrics.py            # NEW — histograms/deadline counter via record_run
    ├── test_api_call_log.py           # NEW (integration) — 4 callers, identical shape, correlation end-to-end
    └── test_api_telemetry_setting.py  # NEW (integration) — GET/PUT, admin-only, audit-on-change, effective flag
docs/  — backend/10 §1, lld/10 §3/§5, scope.md decision log, implementation-plan M6 row, backend-integration-status
```

**Structure Decision**: Runtime emits pure value objects through an injected sink (same pattern as `record_run`);
all policy (what leaves the process, redaction, fingerprint keys, metrics) lives in `api/`. The redaction primitive
sits in `runtime/guardrails/` because it is pure and is the module the guardrails design will grow into.

## Key design decisions (detail in research.md)

1. **Sink seam, not in-runtime logging** (D1) — purity + per-workspace opt-in possible.
2. **Per-turn emission in a finally** (D2) — covers deadline, error, and client-disconnect paths.
3. **Cost by construction** (D3) — per-call = gateway turn Decimal; Σ == run cost.
4. **Keyed per-workspace fingerprint** (D4) — matchable within a tenant, not reversible, not cross-tenant linkable.
5. **Redaction = exact-match known secrets + patterns, redact-then-truncate, fail closed** (D5).
6. **Opt-in = admin-only endpoint + audit line; dev profile on** (D6).
7. **Outbound `X-Correlation-Id` = platform-generated id only (U1); provider-native ids + response ids spike-verified** (D7).
8. **SLIs at the one exactly-once site; deadline = outcome `timeout`** (D8).

## Risks & open items

- **Provider header support is unverified** (D7) — a spike task must confirm what OpenAI/Anthropic accept/return via
  litellm before the contract table's TBDs are filled; the generic header alone still satisfies FR-012's
  "best-effort, documented".
- **Pattern redaction is incomplete PII detection** — mitigated by default-off; documented residual risk for opt-in.
- **Prod fail-closed guard reaches every process that loads settings** (found in T038): the `migrate` job and
  `scripts/emit_openapi.py` also load `Settings`; dev compose/tooling now default `AC_ENV=dev`, but a prod migrate job
  must inject `AC_TELEMETRY_FINGERPRINT_KEY` (or the guard should move to API startup) — decide before the first prod
  deploy.
- **Metrics over-count on rollback** (inherited from 010 note) — unchanged, acceptable for approximate counters.
- **Interrupted pass-through turns** may lack usage → `cost_known=false`, cost 0 — consistent with how the run row
  already treats them; documented.

## Complexity Tracking

No constitution violations; nothing to justify.

## Post-design Constitution re-check

Re-evaluated after data-model/contracts: still **PASS** on all gates. The design adds no runtime I/O imports, no new
dependency, one additive column, and keeps every new metric label tenant-free.
