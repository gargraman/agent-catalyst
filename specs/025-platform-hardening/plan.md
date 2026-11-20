# Implementation Plan: Platform Hardening — Provider Resilience + Binding Integrity

**Branch**: `feat/backend-m0-foundation` (spec dir `specs/025-platform-hardening`; the setup script reports
`025-platform-hardening` from the dir name — no branch was created) | **Date**: 2026-11-15 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/025-platform-hardening/spec.md` — **Must tier only (scope items 1–7)**.

## Summary

Close the two orphaned gaps from the 2026-11-15 milestone audit, at the Must tier:

1. **M1 provider resilience** — a pure `runtime/providers/resilience.py` (policy, breaker state machine, store
   Protocol, local store, batch bulkhead) used *inside* `gateway.stream` / `gateway.embed`, so every caller
   (playground, published, channel, eval case, judge, query + ingestion embeddings) gets identical behaviour (P6).
   Retry only before the first surfaced event (F10), capped Retry-After inside the deadline (F11), breaker per
   (provider, model, live|batch) shared via Redis without Lua, live-open refuses batch but not vice versa (F8).
   Also removes a **pre-existing hidden retry**: litellm's OpenAI path defaults the SDK to `max_retries=2` today
   (research G1) — forced to 0 so attempts, cost and metrics are honest.
2. **M0 binding integrity** — migration `0015_binding_integrity` (US4; `runs.retry_count` is the separate tiny `0014_runs_retry_count`, US1): composite same-workspace FKs (RLS-proof, research G8) with `ON DELETE
   RESTRICT`, binding immutability triggers matching F15 (insert only in the version's creating transaction, verified
   on PG16 — G5), `CHECK` exactly-one tool target, deferred FK for active-version-same-agent, chunk-dimension trigger,
   `runs.retry_count`, and a preflight that **refuses** to upgrade over existing violations (never deletes).

## Technical Context

**Language/Version**: Python 3.12 (services venv); TypeScript only for regenerated OpenAPI types
**Primary Dependencies**: FastAPI, SQLAlchemy 2 async + asyncpg, Alembic, litellm 1.106.x (transport only), redis-py 8
(asyncio), prometheus_client; tests: pytest, fakeredis 2.40 (no `lupa` ⇒ no Lua)
**Storage**: Postgres 16 + pgvector (`pgvector/pgvector:pg16`, compose host port 5433); Redis 7 (host 6380) for shared
breaker state
**Testing**: `pytest -m "not integration"` (infra-free: fake transport, fakeredis, injected clock/sleep/rng) and
`pytest -m integration` (Postgres: constraints/triggers/migration round-trip; real Redis multi-instance)
**Target Platform**: Linux container (`services/Dockerfile`), local compose stack; single API replica in the pilot
**Project Type**: web service (monorepo: `services/` backend + `frontend/` Next.js console)
**Performance Goals**: breaker refusal < 50 ms with 0 provider contact (SC-003); zero added latency on the happy path
(no buffering — retry decision only before the first surfaced event)
**Constraints**: every request completes/fails within run deadline + 1 s even with adversarial Retry-After (SC-002);
runtime stays pure (no redis/prometheus/FastAPI imports); no tenant data in breaker keys or metric labels; no live or
paid provider calls; migration refuses rather than repairs
**Scale/Scope**: ~6 new modules, ~10 touched modules, 1 migration, ~12 new test files; default policy 3 attempts /
N=5 / 30 s cooldown / batch cap 4 (tunable `AC_*`)

## Constitution Check

*GATE: evaluated before Phase 0 and re-checked after Phase 1 design.*

| # | Principle | How this plan complies | Gate |
|---|-----------|------------------------|------|
| 1 | Verifiable customer outcome | Each US maps to SC-001…006 with named test files ([quickstart.md](quickstart.md)); "unverified" list is explicit (real Retry-After shapes, thresholds) | ✅ |
| 2 | Scope & phases | Owner decision 2026-11-15 recorded in `docs/implementation-plan.md` audit note; Must items 1–7 only; 8–13 listed as out of scope; a `docs/scope.md` decision row is added at DoD | ✅ |
| 3 | Deterministic controls / fail-closed | Integrity enforced in Postgres, not model/API convention; breaker + bulkhead are ordinary code. Breaker store outage fails **open** — this is an *availability* control, consistent with the 021 degraded-mode parity rule (limiter precedent), recorded in `degraded-mode.md` | ✅ (justified, precedent) |
| 4 | Evidence about agent behaviour | Not a model/prompt change; no eval or judge gate altered. Judge keeps its advisory instance breaker (D7) | ✅ n/a |
| 5 | Tenant boundaries | Composite `(workspace_id, id)` FKs (RI bypasses RLS — G8); preflight uses `row_security=off` so it fails loudly instead of silently under-counting (G7); breaker keys + metric labels carry no tenant data; FK errors map to 404 (no cross-tenant existence leak) | ✅ |
| 6 | Reproducible versions | Binding immutability = F15 parity for all versions (G3); hard-delete of referenced catalog rows blocked; snapshot hash cannot drift from what runs | ✅ |
| 7 | Honest providers & cost | Hidden SDK retries removed (G1); `attempts`/`retries` recorded; retried attempts carry no usage ⇒ cost honest by construction (D9); breaker refusal is a distinct outcome with zero cost | ✅ |
| 8 | Operate; failures visible | New metrics (retries, transitions, refusals, degraded, batch wait); `provider_unavailable` outcome in `ac_runs_total`; degraded-mode record updated | ✅ |
| 9 | Test-first | Every behaviour-changing task has a Red test shown to the owner before Green (tasks.md will name Red file + focused command per task). Scaffolding exception: none requested | ✅ |
| — | Min. verification: Provider/runtime | timeout/failure, streaming (no duplicate output), usage/cost — fake-transport tests; live reconciliation **not** in scope (owner-gated) | ✅ (live gap disclosed) |
| — | Min. verification: Database/tenant | migration round-trip + preflight refusal + negative cross-workspace FK tests | ✅ |
| — | Min. verification: API | typed error mapping tests; OpenAPI + FE typecheck/build after regen | ✅ |
| — | Paid calls | none | ✅ |

**Result (pre-Phase 0)**: PASS — no unjustified violations. **Re-check (post-Phase 1)**: PASS — the design added no
new external call, no new data class, and no new tenant surface; the only fail-open (breaker store outage) is an
availability control with recorded precedent and an alert.

## Project Structure

### Documentation (this feature)

```text
specs/025-platform-hardening/
├── spec.md
├── plan.md                         # this file
├── research.md                     # Phase 0: G1–G12 ground truth + D1–D13 decisions
├── data-model.md                   # Phase 1: runtime value objects + migrations 0014/0015 schema
├── quickstart.md                   # Phase 1: validation guide (no live calls)
├── contracts/
│   ├── runtime-resilience.md       # gateway/guard/breaker/bulkhead behaviour table (S*, B*, K*)
│   └── api-and-data.md             # errors, RunOut, settings, metrics, store, degraded-mode, migration
├── checklists/requirements.md
└── tasks.md                        # Phase 2 (/speckit-tasks — not created here)
```

### Source Code (repository root)

```text
services/
├── runtime/
│   ├── providers/
│   │   ├── resilience.py           # NEW  ResiliencePolicy, TrafficClass, BreakerKey, BreakerStore, LocalBreakerStore, Guard
│   │   ├── gateway.py              # EDIT attempt loops in stream()/embed(); guard + traffic_class params; attempts on events
│   │   ├── transport.py            # EDIT max_retries=0/num_retries=0; TransportError.retry_after; 409 not transient
│   │   └── ir.py                   # EDIT EmbeddingError/EmbeddingResult.attempts
│   ├── retrieval/step.py           # EDIT pass guard + class to gateway.embed
│   ├── context.py                  # EDIT RunContext.guard
│   ├── run.py                      # EDIT Outcome.PROVIDER_UNAVAILABLE, RunRecord.retries
│   └── invoke.py                   # EDIT pass guard/class; map provider_unavailable; sum retries
├── api/
│   ├── breaker_store.py            # NEW  RedisBreakerStore (no Lua)
│   ├── resilience_wiring.py        # NEW  process Guard from settings + observer → metrics
│   ├── db_errors.py                # NEW  IntegrityError → AppError mapping
│   ├── errors.py                   # EDIT VersionFrozen (409 version.frozen)
│   ├── settings.py                 # EDIT AC_PROVIDER_* / AC_BREAKER_* / AC_BATCH_CONCURRENCY
│   ├── metrics.py                  # EDIT 5 new series
│   ├── models.py                   # EDIT constraints mirror + Run.retry_count
│   ├── run_sink.py                 # EDIT persist retry_count
│   ├── schemas/runs.py             # EDIT RunOut.retries
│   ├── ingest_executor.py          # EDIT guard + batch class on embed
│   ├── ingest_repo.py              # EDIT chunk_dim_mismatch → typed job failure
│   └── routers/{invoke,evals,agents}.py   # EDIT inject guard; map integrity errors at write sites
├── migrations/versions/0014_runs_retry_count.py     # NEW (US1)
├── migrations/versions/0015_binding_integrity.py    # NEW (US4)
└── tests/
    ├── test_resilience_retry.py / _breaker.py / _bulkhead.py / _parity.py   # NEW (infra-free)
    ├── test_breaker_store.py       # NEW (fakeredis unit + real-Redis integration)
    ├── test_binding_integrity.py   # NEW (integration)
    ├── test_migration_0015.py      # NEW (integration: preflight refusal + round-trip + ORM parity)
    ├── _fault_transport.py         # NEW (test helper: scripted per-attempt provider outcomes)
    ├── test_resilience_types.py    # NEW
    ├── test_invoke_resilience.py   # NEW (retries/refusal/class through invoke_result)
    ├── test_api_runs_retries.py    # NEW (integration)
    ├── test_api_invoke_breaker.py  # NEW (integration)
    ├── test_api_integrity_errors.py # NEW (integration)
    ├── test_db_errors.py           # NEW
    └── test_transport.py / test_gateway.py / test_gateway_embed.py / test_invoke_parity.py   # EDIT
docs/backend/degraded-mode.md        # EDIT new control row
docs/backend/02-provider-gateway.md  # EDIT as-built resilience note
docs/implementation-plan.md, docs/backend-integration-status.md, docs/scope.md, CLAUDE.md  # DoD reconcile
frontend/openapi.json, frontend/src/lib/api/schema.ts                                      # regenerated
```

**Structure Decision**: existing monorepo layout (web application: `services/` + `frontend/`). Resilience lives in the
pure `runtime/` library with infrastructure injected from `api/`; integrity lives in one Alembic migration mirrored in
the ORM. No new top-level packages or services.

## Delivery order (input for /speckit-tasks)

| Phase | Content | Why first |
|-------|---------|-----------|
| 1 Foundational | transport seam (G1 `max_retries=0`, `retry_after`, 409) + `resilience.py` types | every story depends on it; G1 is a live honesty bug |
| 2 US1 (P1) | retry loop in `gateway.stream`/`embed`, attempts → `RunRecord.retries`, `runs.retry_count` | biggest user-visible win |
| 3 US2 (P1) | breaker state machine, `LocalBreakerStore`, `RedisBreakerStore`, refusal outcome, degraded fallback | needs US1's attempt loop |
| 4 US3 (P1) | traffic classes + batch semaphore + live/batch cross-check | needs US2's breaker |
| 5 US4 (P1) | migration 0015 (preflight → uniques → FKs → CHECK → triggers), ORM mirror, error mapping | independent of 2–4; can run in parallel |
| 6 Polish | metrics, settings, docs (degraded-mode, gateway as-built, status tables, scope decision row), OpenAPI/FE regen, full regression | DoD |

## Rollout / rollback

- **Rollout**: `make up` (now `--build`) runs `alembic upgrade head`; preflight refuses on a dirty DB with an id
  report — the operator fixes data deliberately, then re-runs. Resilience defaults ship on; `AC_PROVIDER_MAX_ATTEMPTS=1`
  disables retry; `AC_BREAKER_FAILURE_THRESHOLD` very high effectively disables the breaker (documented kill switches).
- **Rollback**: `alembic downgrade 0014` removes every 0015 object; `downgrade 0013` also drops `runs.retry_count` (round-trip tested); code rollback is a revert —
  the runtime contract is additive (new optional fields/params), so older sinks keep working.

## Complexity Tracking

| Deviation / risk | Why needed | Simpler alternative rejected because |
|------------------|------------|--------------------------------------|
| Composite `(workspace_id, id)` FKs + 5 extra UNIQUE indexes | RI checks bypass RLS (G8); single-column FKs would allow cross-workspace references | single-column FK: tenant-boundary hole |
| `xmin = pg_current_xact_id()::xid` insert-window trigger | F15 parity: bindings must be writable only while their version is being created (G3/G4) | `created_at = now()` check: theoretical same-µs race; "block all inserts": breaks `create_version` |
| Per-process batch semaphore (cap × replicas effective) | pilot = 1 replica; honest, documented | Redis semaphore: lease/TTL machinery not justified until M11 |
| Breaker store fails open on Redis outage | availability control; refusing on cache outage would turn a Redis blip into a provider outage | fail closed: contradicts 021 parity rule |
