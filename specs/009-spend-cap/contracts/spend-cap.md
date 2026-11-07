# Contract: Spend Cap Gate + Spend View (009)

## 1. Gate on paid-invocation routes

Applies to:
- `POST /api/v1/agents/{agent_id}/invoke`  (interactive, SSE)
- `POST /api/v1/agents/{agent_id}/eval-runs`

**Behavior**: after authn + membership + role checks pass, and **before** the provider is called / the SSE
stream opens, the server computes month-to-date spend for the caller's workspace and compares it to the
active monthly cap.

- `month_to_date_spend < cap` → request proceeds unchanged (stream / eval as today).
- `month_to_date_spend >= cap` → request is refused with **HTTP 402** and the body:

```json
{
  "error": {
    "code": "budget.exceeded",
    "message": "monthly spend cap reached",
    "detail": { "cap_usd": 2000.0, "month_to_date_usd": 2000.0 }
  }
}
```

- Month-to-date spend **cannot be determined** (ledger error) → fail-closed: the request fails before any
  provider call (propagated internal error / 5xx); **not** translated to a false `402`.

**Invariants**:
- No provider/model call and no SSE frame is emitted on a `402` refusal (SC-002).
- No `runs` row is created for a refused call (FR-011).
- The `402` is reachable only by an otherwise-authorized caller; an unauthenticated/unauthorized/
  unknown-workspace caller gets `401`/`403` first (FR-006, no existence leak).
- Enforcement is identical on both routes (SC-006).

## 2. Spend view (observability, US3)

`GET /api/v1/workspace/spend`  — permission: `view`

**200 response**:
```json
{ "monthToDateUsd": 1234.56, "capUsd": 2000.0, "remainingUsd": 765.44 }
```

- `monthToDateUsd` and `capUsd` are computed from the same helpers the gate uses (SC-004 — one source of
  truth). `remainingUsd = max(capUsd - monthToDateUsd, …)` (may be reported as `cap - mtd`, can be ≤ 0 when
  over cap).
- Workspace-scoped (RLS + `workspace_id`); requires workspace selection (`X-Workspace-Id` / `?workspace=`)
  and a role claim, like every other `/api/v1` route.
- Carries a `required_permission` marker so the deny-by-default coverage test (feature 008) stays green.

## 3. Error envelope reference

`402 budget.exceeded` is an **existing** class (`api/errors.py::BudgetExceeded`); this feature is the first
to raise it. The envelope shape is the standard `{"error": {"code", "message", "detail"?}}` (08 §1).

## 4. Frontend contract (build-unverified — Node unavailable)

- Cap of record stays `Limits.monthlySpendUsd` (`frontend/src/lib/data.ts`), served inside workspace
  settings/limits as today.
- **New**: `WorkspaceSpend = { monthToDateUsd: number; capUsd: number; remainingUsd: number }` + a
  `@backend GET /v1/workspace/spend` seam (`useWorkspaceSpend()`), with a mock value.
- **New**: the invoke/playground error path models the `402 budget.exceeded` outcome (user-facing "monthly
  spend cap reached" message), closing the one FE/BE contract gap.
- These TS edits are not compiled/linted here; shapes preserved for a later wired pass.
