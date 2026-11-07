# Tasks: Durable Monthly Workspace Spend Cap (009)

**Feature**: `specs/009-spend-cap` | **Branch**: `feat/backend-m0-foundation` | **Date**: 2026-11-07

**Inputs**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md) (D1–D7),
[data-model.md](./data-model.md), [contracts/spend-cap.md](./contracts/spend-cap.md),
[quickstart.md](./quickstart.md)

**TDD is mandatory** (P9): each story's behavioral RED (over-cap currently *allowed* to invoke) is written
and shown to fail before the gate is wired. Tests use `httpx` ASGITransport + `dev_headers(claims={ws:
role})` with the gateway mocked; no live/paid calls. Change is **API-layer only** — `runtime/`+`evals/`
untouched (P6). **No new deps, no migration** (reuses `runs.cost_usd` + `workspace_settings.limits` +
`BudgetExceeded`).

**Conventions**: `cd services && .venv/bin/python -m pytest`. `[P]` = parallelizable (different files).
Story labels on story-phase tasks only.

---

## Phase 1: Setup

- [x] T001 Establish the green baseline + confirm anchor points: run `cd services && .venv/bin/python -m
  pytest -q` (record count). Confirm the enforcement anchors exist as the plan assumes — `api/errors.py`
  `BudgetExceeded` (402 budget.exceeded); `api/deps.py` `resolve_workspace`/`tenant_db`/`require_permission`;
  `api/models.py` `Run.cost_usd`/`Run.created_at` + `WorkspaceSettings.limits`; the invoke route
  (`routers/invoke.py`) and eval-run route (`routers/evals.py`). No code change.

## Phase 2: Foundational (blocking prerequisites for all stories)

The pure spend math is shared by the gate (US1/US2) and the view (US3) — build it once, first.

- [x] T002 RED: write `services/tests/test_spend_model.py` asserting the pure helpers in `api.spend`
  (module absent → fails): (a) `_month_start_utc(now)` returns naive UTC midnight on the 1st of `now`'s
  month; (b) `active_cap_usd` → `Decimal` from `workspace_settings.limits["monthlySpendUsd"]` when present,
  else `DEFAULT_MONTHLY_CAP_USD == Decimal("2000")`; (c) `month_to_date_spend` sums `runs.cost_usd` for the
  workspace where `created_at >= month_start` only — seed one this-month run and one prior-month run (explicit
  older `created_at`) and assert only the former counts; (d) it is workspace-scoped (a second workspace's run
  does not count).
- [x] T003 Implement `services/api/spend.py` (GREEN for T002): `DEFAULT_MONTHLY_CAP_USD = Decimal("2000")`
  (mirrors `frontend/src/lib/data.ts` `defaultLimits().monthlySpendUsd`); `_month_start_utc(now=None)`;
  `async def month_to_date_spend(session, workspace_id) -> Decimal` (`SELECT COALESCE(SUM(cost_usd),0)
  FROM runs WHERE workspace_id=:ws AND created_at>=:month_start`, double-scoped by RLS + explicit filter;
  errors propagate — fail-closed per D3); `async def active_cap_usd(session, workspace_id) -> Decimal`.
  Pure data-access only — **no** HTTP imports (P6 lives one layer up).

**Checkpoint**: spend math green and reusable.

## Phase 3: User Story 1 (P1) — Over-cap workspace is blocked before spending 🎯 MVP

**Goal**: an over-cap workspace cannot start a paid invocation (invoke or eval-run); it is refused 402
before any provider call or stream, with no run row.
**Independent test**: seed runs ≥ cap this month → both routes return `402 budget.exceeded`, gateway never
called.
**Depends on**: Foundational.

- [x] T004 [US1] RED: write `services/tests/test_api_spend_cap.py` (ASGI client, compose DB, gateway
  mocked) covering — **over-cap invoke**: seed this-month runs summing ≥ the workspace cap; `POST
  /agents/{id}/invoke` → `402`, envelope `{"error":{"code":"budget.exceeded","detail":{"cap_usd":…,
  "month_to_date_usd":…}}}`, **mocked gateway never called**, **no SSE frame in the body**, **no new `runs`
  row** (count before/after). **over-cap eval-run**: same seed; `POST /agents/{id}/eval-runs` → `402`,
  gateway not called. **FR-006 ordering**: a caller whose role lacks the permission, and a caller selecting
  an unheld workspace, get `403` (not `402`) even while over cap. **FR-009 fail-closed**: monkeypatch
  `api.spend.month_to_date_spend` to raise → invoke returns non-2xx **and** gateway not called. Must fail
  now (no gate → streams 200).
- [x] T005 [US1] Add `enforce_spend_cap` to `services/api/deps.py`: an async dependency depending on
  `resolve_workspace` + `tenant_db`; compute `spent = await month_to_date_spend(session, ws)` and `cap =
  await active_cap_usd(session, ws)`; if `spent >= cap` raise `BudgetExceeded("monthly spend cap reached",
  detail={"cap_usd": float(cap), "month_to_date_usd": float(spent)})`; emit a structured log line on refusal
  (FR-011 — observable, no Run row). Let a spend-computation error propagate (fail-closed, D3). No
  `required_permission` marker (it is a budget gate, not an RBAC gate — must not disturb the 008 coverage
  test, which keys on routes not deps).
- [x] T006 [P] [US1] Attach the gate to the invoke route in `services/api/routers/invoke.py`: add
  `Depends(enforce_spend_cap)` to `POST /agents/{agent_id}/invoke` **after** `require_permission(
  "playground.use")` (authz precedes budget); import `enforce_spend_cap` from `api.deps`.
- [x] T007 [P] [US1] Attach the gate to the eval-run route in `services/api/routers/evals.py`: add
  `Depends(enforce_spend_cap)` to `POST /agents/{agent_id}/eval-runs` after `require_permission("eval.run")`.
  Make T004 pass (GREEN). Run the focused test + `tests/test_api_invoke_trust_boundary.py` +
  `tests/test_api_runs.py` regressions.

**Checkpoint**: MVP — over-cap spend is blocked on both paid paths before any provider call.

## Phase 4: User Story 2 (P1) — Under-cap work is unaffected

**Goal**: under-cap workspaces invoke/eval exactly as before; no false refusals.
**Depends on**: Foundational; US1 gate in place.

- [x] T008 [US2] Extend `services/tests/test_api_spend_cap.py` with the allow-side matrix: **under-cap
  invoke** (spend < cap) → 200/stream as today, run recorded; **under-cap eval-run** → proceeds;
  **previous-month** (prior-month spend ≥ cap, this month < cap) → proceeds (month-to-date, not lifetime);
  **unset cap** (no `workspace_settings` row / no `monthlySpendUsd`) → default 2000 applies and under-default
  work proceeds; **P5 cross-tenant negative** — workspace B over cap, workspace A under cap → A invokes fine
  (B's spend never counts toward A). These must pass against the US1 gate with no further gate changes (the
  inclusive `>=` + month-window + workspace-scoping already satisfy them); if any fails, fix `spend.py`/gate
  rather than weakening the test.

**Checkpoint**: US1 + US2 — the cap bites only at/over the ceiling, for the right tenant, this month.

## Phase 5: User Story 3 (P2) — Spend is observable against the cap

**Goal**: an authorized caller can read month-to-date spend vs cap, consistent with enforcement.
**Depends on**: Foundational (US1/US2 may be in place).

- [x] T009 [US3] RED: write `services/tests/test_api_spend_view.py` — `GET /api/v1/workspace/spend` with a
  `view` role returns `{monthToDateUsd, capUsd, remainingUsd}` matching a seeded ledger + cap, and the
  `monthToDateUsd`/`capUsd` equal what the gate would use for the same workspace at the same time (SC-004).
  Also assert `403` for an unheld-workspace caller. Must fail now (route absent).
- [x] T010 [US3] Implement the view: add a `WorkspaceSpendOut` response model in
  `services/api/schemas/workspace.py` (create if absent) with `monthToDateUsd`/`capUsd`/`remainingUsd`
  floats; add `GET /workspace/spend` to `services/api/routers/workspaces.py` with
  `dependencies=[Depends(require_permission("view"))]`, resolving via `resolve_workspace` + `tenant_db` and
  reusing `api.spend` helpers (`remainingUsd = float(cap - spent)`). Make T009 pass (GREEN). Confirm
  `tests/test_api_rbac_coverage.py` still green (the new route carries `required_permission`).

## Phase 6: Polish & Cross-Cutting

- [x] T011 [P] Frontend contract sync (**build-unverified** — Node unavailable): in
  `frontend/src/lib/data.ts` add `export type WorkspaceSpend = { monthToDateUsd: number; capUsd: number;
  remainingUsd: number }` + a `@backend GET /v1/workspace/spend` note; in `frontend/src/lib/store.ts` add a
  `useWorkspaceSpend()` seam with a mock value and model the `402 budget.exceeded` outcome on the invoke/
  playground error path (user-facing "monthly spend cap reached"). Keep `Limits.monthlySpendUsd` as the cap
  of record. Do NOT run npm (unavailable); report these edits as build-unverified.
- [x] T012 Run purity + full regression: `.venv/bin/python -m pytest tests/test_runtime_purity.py -q` (P6
  green — `runtime/`+`evals/` still import-clean) then `.venv/bin/python -m pytest -q` (no regressions vs the
  T001 baseline + the new spend tests); record the pass count.
- [x] T013 Reconcile docs: add an M6 spend-cap built-state banner to `docs/implementation-plan.md` (M6 row —
  durable monthly workspace spend cap live; Redis reservation / per-agent cap / overshoot bound deferred)
  and to `docs/backend/09-security-tenancy.md` and/or the observability doc; update
  `docs/backend-integration-status.md` if it references spend/limits enforcement.

---

## Dependencies

```
Setup (T001)
  └─► Foundational (T002 RED → T003 spend.py GREEN)
        ├─► US1 (T004 RED → T005 dep → T006[P] invoke, T007[P] eval → GREEN)   ← MVP
        ├─► US2 (T008 allow-side matrix; needs US1 gate)
        ├─► US3 (T009 RED → T010 view GREEN)
        └─► Polish (T011 FE sync [P] · T012 regression · T013 docs)
```

- **Foundational blocks all** (`spend.py` used by gate + view). T002 RED before T003 GREEN.
- **US1** is the MVP; T006/T007 are different files → parallel after T005.
- **US2** adds only assertions (no new gate code expected); do it right after US1.
- **US3** is independent of US1/US2 beyond sharing `spend.py`.
- **T012** runs after US1–US3 land; **T013** docs last.

## Parallel opportunities

- T006 (invoke) and T007 (eval) are different files → parallel after T005.
- T011 (frontend) is independent of the Python tasks → parallel with T012/T013.

## Implementation strategy

- **MVP = Setup + Foundational + US1** (over-cap blocked on both paid paths). US2 proves no false positives;
  US3 adds visibility — both small.
- **TDD per story**: show the over-cap RED (currently streams 200) before wiring the gate; never weaken a
  test to pass.
- Every GREEN runs the focused test + affected regressions; full regression + purity at T012.

## Task count & format

- **Total: 13 tasks** — Setup 1, Foundational 2, US1 4, US2 1, US3 2, Polish 3.
- Per story: **US1 = 4**, **US2 = 1**, **US3 = 2**.
- All tasks carry checkbox + sequential ID + optional `[P]` + story label where applicable + explicit path.
