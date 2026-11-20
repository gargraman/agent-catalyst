# Quickstart / Validation: Durable Monthly Workspace Spend Cap (009)

**Prereqs**: compose Postgres up (host 5433, healthy); `cd services`; venv at `services/.venv`.
Run tests: `.venv/bin/python -m pytest`. No live/paid calls — the provider gateway is mocked throughout.

## TDD order (RED shown to owner before GREEN)

1. **Pure model (RED→GREEN)** — `tests/test_spend_model.py`:
   - `_month_start_utc` returns UTC midnight on the 1st; injectable `now`.
   - `active_cap_usd` → settings value when present; `DEFAULT_MONTHLY_CAP_USD` (2000) when row/key absent.
   - `month_to_date_spend` sums only this-month runs for the workspace (seed a prior-month run → excluded).
   - RED first: `api.spend` does not exist.

2. **Gate behavioral RED** — `tests/test_api_spend_cap.py` (ASGI client, compose DB, gateway mocked):
   - **US1 over-cap invoke**: seed runs summing ≥ cap this month; `POST /agents/{id}/invoke` →
     expect `402 budget.exceeded`, envelope `detail` has `cap_usd` + `month_to_date_usd`, **gateway never
     called**, **no SSE frame**, **no new runs row**. RED now = it currently streams 200.
   - **US1 over-cap eval-run**: same seed; `POST /agents/{id}/eval-runs` → `402`, gateway not called.
   - **US2 under-cap invoke/eval**: spend < cap → proceeds (200/stream), run recorded as before.
   - **US2 previous-month**: prior-month spend ≥ cap but this month < cap → proceeds.
   - **P5 cross-tenant negative**: workspace B over cap, workspace A under cap → A invokes fine (B's spend
     never counts).
   - **FR-009 fail-closed**: monkeypatch `month_to_date_spend` to raise → invoke returns non-2xx **and**
     gateway not called.
   - **FR-006 ordering**: unknown-workspace / insufficient-role caller gets `403` (not `402`) even when over
     cap (authz precedes budget).

3. **GREEN**: add `api/spend.py`; add `enforce_spend_cap` to `deps.py`; attach it to invoke + eval-run
   routes (after `require_permission`). Re-run → matrix green.

4. **US3 view (RED→GREEN)** — `tests/test_api_spend_view.py`:
   - `GET /api/v1/workspace/spend` → `{monthToDateUsd, capUsd, remainingUsd}` matching the seeded ledger +
     cap; equals the figure the gate uses. `view` permission required; coverage test still green.

## Regression / constitution checks

- Purity (P6): `.venv/bin/python -m pytest tests/test_runtime_purity.py -q` — `runtime/`+`evals/` still
  import-clean (no api/DB imports leaked).
- Deny-by-default coverage (feature 008): `tests/test_api_rbac_coverage.py` green — new `/workspace/spend`
  route carries a `required_permission`.
- Full regression: `.venv/bin/python -m pytest -q` — no deltas beyond the new tests; record the count.

## Frontend (build-unverified — Node unavailable)

- Eyeball `frontend/src/lib/data.ts` + `store.ts`: `WorkspaceSpend` type + `useWorkspaceSpend()` seam
  present; `402 budget.exceeded` modeled on the invoke/playground error path; `Limits.monthlySpendUsd`
  unchanged. Not compiled/linted — reported as build-unverified.

## Definition of Done

- All new tests green; full regression green; purity + coverage green.
- Spec FRs satisfied; deferrals (Redis reservation, per-agent cap, overshoot bound) documented.
- Docs reconciled (M6 spend-cap built-state banner in implementation-plan + backend security/observability
  docs) at converge/DoD.
