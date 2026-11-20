# Data Model: Durable Monthly Workspace Spend Cap (009)

**No new tables, no migration.** This feature reads two existing tables and adds no columns. The "model"
here is the derivation of month-to-date spend and the cap, plus the exact query.

## Reused entities

### `runs` (existing — the durable spend ledger)

Relevant columns (`services/api/models.py::Run`):

| Column | Type | Role here |
|---|---|---|
| `workspace_id` | uuid (FK, indexed, RLS key) | tenant scope of the SUM |
| `cost_usd` | Numeric(12,6) | the per-run recorded cost — the ledger value summed |
| `created_at` | timestamp (server_default `now()`) | month-window filter |
| `outcome` | text | not read by the gate (both ok and failed runs cost money and count) |

- **Written by**: `api/run_sink.py::persist_run_record` for every invocation (success *or* failure, P8),
  on the request's RLS session. This is why summing it is complete and durable.
- **Not modified** by this feature.

### `workspace_settings` (existing — the cap source)

Relevant columns (`services/api/models.py::WorkspaceSettings`):

| Column | Type | Role here |
|---|---|---|
| `workspace_id` | uuid (PK, 1:1 with workspace) | lookup key |
| `limits` | JSONB | holds `monthlySpendUsd` (+ `maxConcurrent`, `requestTimeoutS`) |

- `limits.monthlySpendUsd` is the cap. Absent row/key → `DEFAULT_MONTHLY_CAP_USD` (2000).
- **Not modified** by this feature.

## Derived values (pure, in `api/spend.py`)

### `DEFAULT_MONTHLY_CAP_USD = Decimal("2000")`
Mirrors `frontend/src/lib/data.ts::defaultLimits().monthlySpendUsd`.

### `_month_start_utc(now=None) -> datetime` (naive)
`datetime(now.year, now.month, 1)` at UTC midnight, **naive** to match the `timestamp`-typed
`runs.created_at` (D5). `now` injectable for deterministic tests.

### `month_to_date_spend(session, workspace_id) -> Decimal`
```
SELECT COALESCE(SUM(cost_usd), 0)
FROM runs
WHERE workspace_id = :workspace_id
  AND created_at >= :month_start      -- _month_start_utc()
```
Returns a `Decimal`. Runs on the RLS session (double-scoped: GUC + explicit `workspace_id` filter).
May raise on a ledger/DB failure → propagates (fail-closed, D3).

### `active_cap_usd(session, workspace_id) -> Decimal`
Point-read `workspace_settings` by `workspace_id`; return `Decimal(str(limits["monthlySpendUsd"]))` when
present, else `DEFAULT_MONTHLY_CAP_USD`.

## Decision rule (the gate)

```
spent = month_to_date_spend(session, ws)     # Decimal; raises → fail-closed (no provider call)
cap   = active_cap_usd(session, ws)           # Decimal
if spent >= cap:                              # inclusive (D5)
    raise BudgetExceeded(
        "monthly spend cap reached",
        detail={"cap_usd": float(cap), "month_to_date_usd": float(spent)},
    )
```

## State / transitions

No stored state transitions. The only "transition" is temporal: at the UTC calendar-month boundary the
window start advances, so month-to-date spend effectively resets (prior-month runs fall out of the
`created_at >= month_start` filter). No job, no reset write — it is implicit in the query.

## Tenancy & composition

- The SUM is workspace-scoped by both the RLS GUC (set by `tenant_session`) and the explicit
  `workspace_id` predicate — another tenant's spend can never count (P5; negative test in quickstart).
- Order of controls on a gated route: `authn (session_principal)` → `membership (resolve_workspace, 403)`
  → `role (require_permission, 403)` → `budget (enforce_spend_cap, 402)` → handler. 403 precedes 402;
  402 precedes any provider call / stream.
