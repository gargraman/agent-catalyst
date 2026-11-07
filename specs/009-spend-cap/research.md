# Research: Durable Monthly Workspace Spend Cap (009)

Phase 0 decisions. No external research needed — this is a known pattern over an existing data model;
the work is choosing the honest minimal realization and pinning the edge semantics.

## D1 — Durable source of truth: Postgres SUM, no Redis

- **Decision**: Month-to-date spend is computed on demand as
  `SELECT COALESCE(SUM(cost_usd), 0) FROM runs WHERE workspace_id = :ws AND created_at >= :month_start`.
  No Redis counter is introduced in this slice.
- **Rationale**: F7 requires that eviction of a cache must never silently raise the ceiling. If the
  authoritative figure *is* the durable ledger, there is nothing to evict — durability is intrinsic, not
  bolted on. The ledger (`runs.cost_usd`) already exists and is written for every invocation (success or
  failure) by `run_sink`. One indexed aggregate per invocation is negligible next to a model call.
- **Alternatives considered**:
  - *Redis atomic counter as source of truth* — rejected: an evicted/flushed counter resets spend to 0 →
    exactly the silent un-cap F7 forbids. Would need periodic reconciliation to Postgres anyway.
  - *Redis as a fast-path cache in front of Postgres* — deferred (P2): a legitimate latency optimization,
    but it must reconcile to Postgres and is pure performance, not required for correctness. Out of scope.
  - *A materialized `workspace_spend` counter table* — rejected for this slice: adds a migration and a
    write on every run (dual-write consistency risk) for no correctness gain over summing the ledger.

## D2 — Enforcement point & ordering: a dependency beside `require_permission`

- **Decision**: Add `enforce_spend_cap` to `api/deps.py` as a FastAPI dependency that depends on
  `resolve_workspace` + `tenant_db`, and attach it via `dependencies=[Depends(enforce_spend_cap)]` to the
  invoke and eval-run routes, listed **after** `require_permission(...)`.
- **Rationale**: Mirrors the proven feature-008 control shape. `require_permission` is header-only (no DB)
  and listed first, so an unauthorized/unheld-workspace caller is refused (401/403) before the budget gate
  opens a session — authz precedes budget (FR-006) with no extra code, and the `402` is only ever seen by
  an otherwise-authorized caller (no existence leak). Because `enforce_spend_cap` depends on `tenant_db`
  (per-request cached by FastAPI), its SUM runs on the *same* RLS-scoped session the handler uses — the
  GUC scopes the query to the tenant and there is no second `tenant_session` (the invoke StreamingResponse
  generator holds the session; a second one would deadlock, per `run_sink`'s note).
- **Alternatives considered**:
  - *Enforce inside `runtime.invoke`* — rejected: violates P6 (runtime would need DB access) and would
    duplicate the control across the four callers instead of at the one admission boundary.
  - *Starlette middleware* — rejected: too coarse (no per-route targeting), awkward to get the resolved
    workspace/role and the RLS session, and harder to unit-test than a dependency.

## D3 — Fail-closed realization (FR-009)

- **Decision**: If computing month-to-date spend raises, do **not** catch it — let it propagate so the
  request fails before any provider call. Do not translate it into `402 budget.exceeded`.
- **Rationale**: The clarified policy is fail-closed (favor spend safety). Not swallowing the error is the
  simplest honest realization: the invocation cannot proceed to a paid call. Translating an internal
  ledger failure into `budget.exceeded` would lie about *why* (the budget may be nowhere near spent), so
  we keep the failure as an internal error. The spend query uses the same DB the request already depends
  on, so this adds essentially no new availability surface.
- **Alternatives considered**:
  - *Fail-open on error* — rejected by the owner (would open an un-capped window on a ledger blip — the
    silent un-cap F7 exists to prevent).
  - *A dedicated `503 budget.unavailable` controlled refusal* — deferred: cleaner caller contract, but
    adds a new error code/handler for a rare transient; the propagated 5xx already satisfies fail-closed.
    Noted as a possible future refinement.
- **Test**: monkeypatch `month_to_date_spend` to raise; assert the invoke returns non-2xx **and** the
  mocked gateway was never called (fail-closed before the paid call).

## D4 — Cap resolution & default

- **Decision**: `active_cap_usd` reads `workspace_settings.limits` (JSONB) → `monthlySpendUsd`; when the
  row or key is absent, fall back to `DEFAULT_MONTHLY_CAP_USD = Decimal("2000")`.
- **Rationale**: `limits.monthlySpendUsd` is the field the frontend already models (`Limits.monthlySpendUsd`,
  `defaultLimits()` = 2000). Mirroring the default keeps FE/BE in sync and means an unset cap is a sane
  finite ceiling, never "unlimited" (fail-closed spirit).
- **Alternatives**: a hardcoded per-env default via settings — deferred; the constant mirrors the FE and is
  trivially promotable to settings later.

## D5 — Threshold & month window semantics

- **Decision**: Block when `month_to_date_spend >= active_cap` (inclusive). The month window is
  `created_at >= first-day-of-current-month 00:00` interpreted as **UTC**.
- **Rationale**: Inclusive matches "reaching the cap exhausts it". UTC matches how run timestamps are
  stored and avoids per-workspace billing-cycle complexity (explicitly out of scope).
- **Implementation note**: `runs.created_at` is a `timestamp` (naive) defaulted by `func.now()`. The month
  boundary is therefore computed as a **naive** datetime at UTC midnight on the 1st to compare apples to
  apples (no tz-aware vs naive comparison error). Documented so the GREEN step doesn't drift into a
  tz-aware comparison. A previous-month run in tests is seeded with an explicit older `created_at`.

## D6 — Observability (US3) & no Run row on refusal

- **Decision**: Add `GET /api/v1/workspace/spend` (permission `view`) returning
  `{monthToDateUsd, capUsd, remainingUsd}`, computed from the same `spend.py` helpers the gate uses (one
  source of truth → SC-004). A budget refusal writes **no** Run row; it is surfaced via the `402` plus a
  structured log line (operators can grep/metricize the denial) — consistent with how RBAC denials behave.
- **Rationale**: Enforcement without visibility is operationally hostile; reusing the helpers guarantees
  the view and the gate never disagree. Phantom zero-cost "runs" would pollute run-rate/success metrics
  and need a new outcome enum for a non-execution — rejected (clarified).
- **Alternatives**: fold spend into `GET /workspace/settings` — rejected: settings is config, spend is a
  live metric with different caching/permission semantics; a dedicated endpoint is independently testable
  (US3) and matches the FE's separate `useLimits`/metrics seams.

## D7 — Frontend contract sync (build-unverified)

- **Decision**: In `frontend/src/lib/`, add a `WorkspaceSpend` type + a `@backend GET /v1/workspace/spend`
  seam (`useWorkspaceSpend()` with a mock), and model the `402 budget.exceeded` outcome on the invoke/
  playground error path. The cap field stays the already-modeled `Limits.monthlySpendUsd`.
- **Rationale**: Honors the "keep FE in sync" instruction at the contract/type layer. The one real gap is
  the `402 budget.exceeded` invoke rejection, which the FE does not yet model.
- **Honesty caveat (P1/P9)**: the Node toolchain is unavailable in this environment, so these edits are
  **not** `npm run build`/`lint` verified. They are reported as build-unverified; the drop-in-replaceable
  shapes are preserved so a later wired pass compiles them.
