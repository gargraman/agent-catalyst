# Implementation Plan: Durable Monthly Workspace Spend Cap

**Branch**: `feat/backend-m0-foundation` (slice 009) | **Date**: 2026-11-07 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/009-spend-cap/spec.md`

## Summary

Add a deterministic, fail-closed, workspace-scoped **monthly spend cap** enforced in the API layer
*before* any paid model call. Month-to-date spend is summed from the durable `runs.cost_usd` ledger for
the current calendar month (UTC); the cap comes from `WorkspaceSettings.limits.monthlySpendUsd`
(default 2000, mirroring the frontend). When month-to-date ≥ cap, the invoke and eval-run routes refuse
with the existing `402 budget.exceeded` envelope before the stream opens / provider is called. A read
surface exposes month-to-date spend vs cap (US3). Durability (F7) is intrinsic: the ledger is Postgres,
no evictable counter is the source of truth. Runtime stays pure (P6). No new deps, no migration.

## Technical Context

**Language/Version**: Python 3.12.13

**Primary Dependencies**: FastAPI/Starlette, SQLAlchemy 2.0 async (asyncpg), Pydantic v2 — all existing.
No new dependency.

**Storage**: PostgreSQL (pgvector/pg16) via compose, host port 5433. Reuses existing tables: `runs`
(`cost_usd` Numeric(12,6), `created_at`, `workspace_id`) and `workspace_settings` (`limits` JSONB). **No
migration.**

**Testing**: pytest + pytest-asyncio (`asyncio_mode=auto`), httpx ASGITransport client; gateway mocked
(no live/paid call). Run: `cd services && .venv/bin/python -m pytest`.

**Target Platform**: Linux server (FastAPI service).

**Project Type**: web service (`services/`) with a mock-only Next.js `frontend/` whose contract must stay
in sync (contract-level edits only — Node toolchain unavailable, so FE is build-unverified).

**Performance Goals**: the admission check adds one indexed `SUM(cost_usd)` aggregate + one
`workspace_settings` point-read per invocation, on the request's existing RLS session. Negligible vs a
model call. (A cached fast-path is explicitly out of scope; the durable query is the control.)

**Constraints**: API-layer only — `runtime/` and `evals/` keep zero HTTP/DB imports (P6). Fail-closed
(P3). Workspace-scoped, composes with RLS + RBAC (P5/008). No raw content read or written (P8/SC-009).

**Scale/Scope**: internal enterprise pilot; per-workspace monthly caps (single-digit→low-hundreds of
workspaces). Concurrency overshoot bounded and documented; Redis reservation deferred to P2.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Gate | Status |
|---|---|---|
| **P1/P9 honesty** | deferrals documented; genuine behavioral RED before GREEN; FE build-unverified flagged | PASS — overshoot limit, Redis reservation, per-agent cap all documented as deferred; RED = over-cap workspace currently allowed to invoke. |
| **P3 deterministic fail-closed** | cap enforced in code before the paid call; model/UI not relied on; fail-closed when spend indeterminate | PASS — `enforce_spend_cap` dependency; FR-009 fail-closed (ledger error is not swallowed → no provider call). |
| **P5 tenancy** | workspace-scoped; RLS composes; negative cross-tenant test | PASS — SUM runs on the RLS session filtered by `workspace_id`; a cross-workspace negative test asserts another tenant's spend never counts. |
| **P6 runtime purity** | `runtime/`+`evals/` untouched; no HTTP/DB added there | PASS — change is entirely in `api/`; purity test stays green. |
| **P7 provider honesty** | cost from our own profiles | PASS — reuses the existing `compute_cost`/`cost_usd` ledger; no new cost source. |
| **P8 observability** | spend observable; refusals not silent | PASS — US3 read surface; budget refusal → `402` + structured log/metric; no Run row for a non-execution (clarified). |
| **Paid-call protocol** | no new paid test calls | PASS — gateway mocked; tests assert it is never reached on refusal. |

**No violations. No Complexity Tracking entries required.**

## Project Structure

### Documentation (this feature)

```text
specs/009-spend-cap/
├── plan.md              # This file
├── research.md          # Phase 0 — decisions (month boundary, fail-closed realization, no-Redis)
├── data-model.md        # Phase 1 — entities (cap, MTD spend, ledger) + the SUM query
├── quickstart.md        # Phase 1 — how to validate RED→GREEN + the matrix
├── contracts/
│   └── spend-cap.md     # Phase 1 — gate contract + GET /workspace/spend + 402 envelope
└── tasks.md             # /speckit-tasks (next)
```

### Source Code (repository root)

```text
services/
├── api/
│   ├── spend.py                 # NEW — pure helpers: month_to_date_spend(), active_cap_usd(), DEFAULT_MONTHLY_CAP_USD, _month_start_utc()
│   ├── deps.py                  # EDIT — add enforce_spend_cap dependency (depends on resolve_workspace + tenant_db)
│   ├── errors.py                # UNCHANGED — BudgetExceeded (402 budget.exceeded) already exists
│   ├── schemas/
│   │   └── workspace.py         # EDIT/NEW — WorkspaceSpendOut response model (monthToDateUsd/capUsd/remainingUsd)
│   └── routers/
│       ├── invoke.py            # EDIT — add Depends(enforce_spend_cap) to POST /agents/{id}/invoke
│       ├── evals.py             # EDIT — add Depends(enforce_spend_cap) to POST /agents/{id}/eval-runs
│       └── workspaces.py        # EDIT — add GET /workspace/spend (require_permission("view"))
└── tests/
    ├── test_spend_model.py            # NEW — pure: month boundary, default cap, cap from settings, SUM
    ├── test_api_spend_cap.py          # NEW — RED→GREEN: over-cap 402 before stream/provider (invoke + eval-run); under-cap allowed; cross-tenant negative; fail-closed; no Run row
    └── test_api_spend_view.py         # NEW — US3: GET /workspace/spend matches the enforcement figure

frontend/                               # contract-level, BUILD-UNVERIFIED (Node unavailable)
└── src/lib/
    ├── data.ts                 # EDIT — add WorkspaceSpend type + @backend GET /v1/workspace/spend seam; note budget.exceeded on invoke
    └── store.ts                # EDIT — add useWorkspaceSpend() seam + mock; model the 402 budget.exceeded invoke error
```

**Structure Decision**: Pure web-service change under `services/api/`, mirroring the feature-008 pattern
(a thin dependency + a small pure module + a coverage-safe route). The spend math lives in a pure
`api/spend.py` so it is unit-testable without HTTP and reused by both the gate and the read endpoint
(one source of truth → SC-004). The gate lives in `deps.py` beside `require_permission` so the two
deterministic controls sit together and compose in a predictable order (authz → budget).

## Key design decisions (detail in research.md)

1. **Durable source = Postgres SUM, no Redis.** `month_to_date_spend` = `SELECT COALESCE(SUM(cost_usd),0)
   FROM runs WHERE workspace_id = :ws AND created_at >= :month_start`. Intrinsically eviction-proof (F7).
   A Redis fast-path/reservation is explicitly **not** built (deferred P2).
2. **Dependency ordering gives authz-before-budget for free.** `require_permission` (header-only, no DB)
   is listed first on the route; `enforce_spend_cap` depends on `tenant_db`. FastAPI resolves the
   header-only authz path first → 403 before any DB work, so FR-006 holds with no extra code. The budget
   error is only ever seen by an otherwise-authorized caller (no existence leak).
3. **Same session, no second connection.** `enforce_spend_cap` depends on `tenant_db`, which FastAPI
   caches per-request — the gate's SUM runs on the *same* RLS session the handler later uses, so the GUC
   scopes it to the tenant and there is no second `tenant_session` (avoids the invoke-generator deadlock
   that `run_sink` documents).
4. **Fail-closed by not swallowing.** If the ledger SUM errors, the exception propagates and the request
   fails before any provider call — fail-closed by construction. We deliberately do **not** convert it to
   a `402 budget.exceeded` (which would falsely claim the budget is spent). Tested by forcing the spend
   computation to raise and asserting the gateway is never called.
5. **Inclusive threshold, calendar month UTC.** `spend ≥ cap` blocks; month window is `[first-of-month
   00:00 UTC, now]`. Previous-month spend does not count.
6. **No Run row for a refusal** (clarified): a budget denial never reaches `runtime.invoke`; surfaced via
   the `402` + a structured log, consistent with RBAC denials.

## Phase 0 / Phase 1 outputs

- `research.md` — the six decisions above with rationale + alternatives.
- `data-model.md` — the (reused) entities, the exact SUM/cap resolution, and the `timestamp`/UTC handling.
- `contracts/spend-cap.md` — the gate behavior, the `402` envelope with `detail`, and `GET
  /api/v1/workspace/spend`.
- `quickstart.md` — RED→GREEN steps + the deny/allow/cross-tenant/fail-closed matrix + purity check.
