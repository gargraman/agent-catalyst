# Implementation Plan: M5 — Redis-SPOF Degraded Mode + PgBouncer Transaction-Mode

**Branch**: `feat/backend-m0-foundation` (feature dir `021-m5-redis-degraded-pgbouncer`) | **Date**: 2026-11-12 | **Spec**: [spec.md](./spec.md) · **Milestone capstone (015f)**

**Input**: `specs/021-m5-redis-degraded-pgbouncer/spec.md` · **Parent umbrella**: [`015-m5-security-completion`](../015-m5-security-completion/) (shared design — research R5/R6 + degraded-mode, data-model Redis table) · **Depends on**: 016 (rate limiting) and 018 (deny-set) must exist to validate their degraded behavior — both are **built**.

## Summary

Turn the shared cache (Redis) from an **undocumented** single point of failure into one with a **deliberate, tested, documented degraded behavior per control**, and run per-request DB connections through **PgBouncer transaction-mode** with a proof that tenant context does not leak across pooled connections. This is the milestone capstone: it **validates and documents** controls delivered in 016/018 and **hardens deployment** — it adds very little new application code.

**Key reuse / deltas found in code — this feature is mostly already-built behavior that needs *validation + documentation*, not new controls:**
- **Rate-limit fail-open + alert is ALREADY built (016).** `ratelimit.check` returns `Decision(allowed=True, degraded=True)` on any Redis error (`ratelimit.py:82-83`), and `deps.rate_limit._rl` already raises the alert: `metrics.record_rate_limit_degraded()` (the `ac_rate_limit_degraded_total` counter) + a throttled WARNING (`deps.py:246-254`). FR-024's "rate limiting fails open + alert" → **021 adds a degraded-mode *test*, not code.**
- **Deny-set fail-closed is ALREADY built (018).** `denyset.is_denied` raises on a configured-Redis error (`denyset.py:48`); `session_principal` (`deps.py:72-77`) and `resolve_caller` convert it to `401`. FR-024's "deny-set fails closed" → **021 adds a degraded-mode *test*, not code.**
- **The PgBouncer txn-mode GUC-no-leak mechanism is ALREADY the design (R6).** `tenancy.set_workspace_scope` uses `set_config('app.workspace_id', :ws, true)` — `is_local=true`, i.e. `SET LOCAL` — **inside** `session.begin()` (`db.py:66-76`). Its own docstring already states "a pooled connection (PgBouncer txn-mode) never leaks one request's tenant into the next." FR-026's **mechanism** exists; **021 adds the PgBouncer deployment + a pooled negative test.**
- **PgBouncer is NOT in the deployment.** `deploy/compose.yaml` wires the API straight to Postgres (`AC_APP_DATABASE_URL: ...@postgres:5432/...`); there is **no pgbouncer service**. 021 adds it (Decision D4).
- **Idempotency is IN-PROCESS, not Redis.** `deps.idempotency_guard` (`deps.py:315`) is a process-local `set()` returning `409 idempotency.in_progress`; durable Redis replay is explicitly deferred (its own docstring). So FR-024's "idempotency fails closed with a retryable error" is satisfied **today without any Redis dependency** (the 409 is retryable). The Redis idempotency store is **future** — 021 documents this honestly (D2), it does not build it.
- **Spend reservation reads Postgres, not Redis.** `assert_within_spend_cap` (`deps.py:172`) sums the durable `runs.cost_usd` ledger and **already fails closed** on any error (`deps.py:184-189`). The Redis spend *reservation* in the umbrella table is **future**; the spend *cap* is Postgres-backed and already fail-closed. 021 documents this (D2).

## Technical Context

**Language/Version**: Python 3.12 (`services/`); deployment YAML (`deploy/`).
**Primary Dependencies**: No new runtime dependency. `fakeredis` (test) to simulate Redis-down; PgBouncer (a deployment component, not a Python dep). `redis.asyncio` already present (016).
**Storage**: No migration. Postgres access is **re-routed** through PgBouncer (connection string / deployment change), not re-modeled. Redis structures are 016/018's, unchanged.
**Testing**: pytest async. Degraded-mode matrix forces `fakeredis` to raise (or points `AC_REDIS_URL` at a dead socket) and asserts each control's documented behavior. The pooled cross-tenant no-leak test runs against PgBouncer in txn mode (integration batch) — reuses the `test_session_tenancy` cross-tenant pattern under a pooled engine.
**Project Type**: Web service (`services/api`) + deployment (`deploy/`).
**Performance Goals**: PgBouncer txn-mode improves connection scaling (many app requests → few physical Postgres conns); the GUC check adds nothing (already `SET LOCAL`).
**Constraints**: deny-set + spend reservation **fail closed**; idempotency fails closed with a retryable error; rate limiting **fails open + alert** (FR-024) — each explicit and tested; the blast radius + per-control decision are **documented and test-verified** (FR-025); txn-mode pooling must **never** leak `app.workspace_id` across pooled connections (FR-026, 0 cross-tenant reads).
**Scale/Scope**: internal pilot; a degraded-mode test matrix + a decision-record doc + a PgBouncer compose service + a pooled no-leak test + (optional) a `/readyz` Redis field. Redis SPOF is **accepted** for the P1 pilot given these explicit, tested behaviors (spec Assumptions).

## Decisions requiring review (the load-bearing choices)

> Where the spec's framing and the code on disk need reconciling, or where a real operational call must be made.

**D1 — This feature is validation + documentation + deploy config, not new controls. Scope it honestly. — ✅ RULED (owner, 2026-11-12): confirmed.**
The two headline degraded behaviors (rate-limit fail-open+alert; deny-set fail-closed) and the GUC-no-leak mechanism are **already implemented and merged** (016/018, `tenancy.py`). 021's deliverables are: a **degraded-mode decision record** (FR-025), a **degraded-mode test matrix** (FR-024), a **PgBouncer deployment** + **pooled no-leak test** (FR-026), and the `/readyz` Redis check (D3). *Rationale:* honest scoping (P1 — never represent already-built behavior as new work). Owner confirmed the capstone is a hardening/evidence feature, not a build feature.

**D2 — Idempotency and spend "reservation" degraded behavior is documented against today's reality (in-process / Postgres), not a not-yet-built Redis store. — ✅ RULED (owner, 2026-11-12): document-against-reality.**
The decision record states: **idempotency** = in-process guard, `409` retryable, **no Redis dependency today** (so Redis-down does not weaken it); **spend cap** = Postgres-backed, already fail-closed; the **Redis spend *reservation*** named in the umbrella table is **future** and, when built, MUST fail closed. *Rationale:* FR-024 lists four controls, but only two (rate limit, deny-set) actually touch Redis today. Documenting the other two against future Redis use would claim behavior that doesn't exist (P1/P8). The Redis idempotency/reservation store is **not** built in this slice (stays deferred).

**D3 — A Redis outage flips `/readyz` to `503` (not-ready). — ✅ RULED (owner, 2026-11-12): flip to 503.**
Add a `redis` check to `/readyz` (`main.py:44`) via the existing `redis_client.is_healthy()`; when `AC_REDIS_URL` is **configured but unreachable**, `/readyz` returns `503 {database: ok, redis: down}`. *Rationale:* deny-set fail-closed (018) already makes a configured-but-down Redis return `401` on **every** console request — the service genuinely cannot serve authenticated console traffic, so readiness should reflect that and let the LB/k8s gate hold/drain traffic rather than send users into a wall of 401s. *Consistency:* this makes the readiness signal agree with the actual fail-closed posture. *Carve-out (parity, must not false-trip):* when `AC_REDIS_URL` is **unset** (dev/test, no deny-set to consult — 016/018 no-op-allow parity), Redis is **not** a readiness dependency and `/readyz` stays `200`; only a *configured-but-unreachable* Redis trips `503` (mirror `is_denied`'s "unset → allow, configured-error → fail closed" rule). *Alternative rejected:* report `{redis: degraded}` and stay `200` — leaves the LB sending traffic to an instance that 401s every console request.

**D4 — Add PgBouncer to `deploy/compose.yaml` in transaction mode; point `AC_APP_DATABASE_URL` through it.**
Add a `pgbouncer` service (`pool_mode = transaction`), route the **app** role (`ac_app`) through it, and keep the **migration/owner** role (`ac`) on a **direct** Postgres connection (migrations + the owner engine must not go through a txn-mode pooler — `db.py` already separates `ASYNC_URL`/`APP_ASYNC_URL`). *Rationale:* txn-mode is exactly what `SET LOCAL` was built for (R6); the owner engine (`09 §7`) must never share pooled state. *Confirm:* the app-through-pooler / owner-direct split.

## Constitution Check

*GATE: pre-Phase 0 + re-checked post-Phase 1.*

| Principle | Status | Notes |
|-----------|--------|-------|
| P1 outcome | ✅ | Operators get a documented blast radius + a tested degraded behavior per control; measurable SC-008 (deny-set/spend 0 unchecked through; rate-limit fails open+alert; 0 cross-tenant reads under pooling). **Honestly scoped as validation/hardening, not new controls (D1).** No LLM. |
| P2 scope/phases | ✅ | Bounded 015f capstone. Redis idempotency/reservation store stays **deferred** (D2) — documented, not silently promoted. |
| **P3 deterministic fail-closed** | ✅ | Validates that deny-set + spend fail closed and rate-limit fails open **by test** — the controls are deterministic code (016/018), not model-enforced. |
| P4 evidence | ✅ (N/A judge) | No model/prompt behavior; deterministic degraded-mode matrix. |
| **P5 data/tenant** | ✅ | The headline deliverable is proving tenant isolation holds under txn-mode pooling (FR-026, cross-tenant negative test). No secrets in the decision record or alerts. |
| P6 reproducible | ✅ | `services/runtime/` untouched; no invoke/snapshot change. Deployment-topology change only + tests/docs. |
| P7 providers/cost | ✅ | No provider/cost contract change. |
| **P8 operate/visible** | ✅ | Degradation is **explicit**: rate-limit fail-open raises the `ac_rate_limit_degraded_total` alert (016); a configured-but-down Redis flips `/readyz` to `503` so the LB/k8s drains the instance (D3, consistent with deny-set fail-closed); the decision record names each control's recovery path. Never reports success when a safety check didn't complete. |
| P9 test-first | ✅ | Red→Green→Refactor; the degraded-mode matrix + pooled no-leak test are the Red evidence; docs-only parts use link/consistency checks (P9 docs clause). |

**Gate: PASS** — the only "deviation" is that most of the feature is evidence/docs/deploy (D1); recorded in Complexity Tracking. All decisions (D1/D2/D3) now owner-ruled.

## Design

### Components

- **`docs/backend/` degraded-mode decision record** (NEW doc): per-control behavior + recovery path — deny-set = fail closed; spend cap = fail closed (Postgres); idempotency = fail closed retryable (in-process 409, no Redis today); rate limit = fail open + alert. Plus the Redis **blast-radius** map (which controls depend on Redis and what an outage does to each) — **including that, under the D3 ruling, a configured-Redis outage flips `/readyz` to 503 and 401s console auth (deny-set fail-closed), i.e. the "accepted SPOF" now takes the authenticated console + readiness down together**. Cites the exact code (`denyset.py`, `ratelimit.py`, `deps.py`, `main.py`). This is the FR-025 artifact.
- **`services/tests/test_redis_degraded_mode.py`** (NEW): the FR-024 matrix. Forces Redis-down and asserts: deny-set → `401` (both console + caller planes); rate limit → allowed + `ac_rate_limit_degraded_total` incremented + WARNING; spend cap → fail closed on a forced ledger error; idempotency → `409` on an in-flight retry (independent of Redis). Also: Redis recovers mid-flight → controls resume without restart (edge case).
- **`deploy/compose.yaml`** CHANGE: add a `pgbouncer` service (`pool_mode=transaction`); repoint `AC_APP_DATABASE_URL` (the `ac_app` role) through it; leave `AC_DATABASE_URL`/`AC_DATABASE_SYNC_URL` (owner/migrations) direct (D4). PgBouncer config file under `deploy/`.
- **`services/tests/test_pgbouncer_txn_isolation.py`** (NEW, integration): two sequential requests on the **same pooled physical connection**, different tenants; assert tenant A's `app.workspace_id` never bleeds into tenant B's request (0 cross-tenant reads). Reuses the `test_session_tenancy` cross-tenant assertions under a pooled engine (no `NullPool`).
- **`api/main.py`** CHANGE (D3): add a Redis check to `/readyz` via `redis_client.is_healthy()` — a **configured-but-unreachable** Redis returns `503 {database: ok, redis: down}` (unset `AC_REDIS_URL` → Redis not a readiness dependency, stays `200`, parity carve-out).

**No application *control* code changes** — the deny-set (`denyset.py`), limiter (`ratelimit.py`, `deps.py`), spend cap (`deps.py`), idempotency guard (`deps.py`), and GUC helper (`tenancy.py`) are already correct for their documented degraded behavior (D1). The only app-code touch is the `/readyz` Redis check (D3).

## TDD mapping (acceptance → Red test → command)

| Acceptance / FR | Red test | Command |
|-----------------|----------|---------|
| FR-024 (deny-set), AS1 | Redis down → console + caller auth `401` (fail closed) | `test_redis_degraded_mode.py::test_denyset_fails_closed_both_planes` |
| FR-024 (rate limit), AS1 | Redis down → request allowed, `ac_rate_limit_degraded_total` +1, WARNING emitted | `::test_ratelimit_fails_open_and_alerts` |
| FR-024 (spend), AS1 | forced ledger error → spend cap fails closed (no paid call) | `::test_spend_cap_fails_closed` |
| FR-024 (idempotency), AS1 | in-flight retry → `409` retryable, independent of Redis | `::test_idempotency_fails_closed_retryable` |
| Edge | Redis recovers mid-flight → controls resume, no restart | `::test_controls_resume_after_redis_recovers` |
| D3 (readyz) | configured Redis down → `/readyz` 503 `{redis: down}`; `AC_REDIS_URL` unset → `/readyz` 200 (carve-out) | `::test_readyz_503_when_configured_redis_down` |
| FR-025, AS2 | the decision record documents each control + recovery; a consistency check ties each claim to code | `::test_decision_record_matches_code` (or a docs link/consistency check) |
| FR-026, AS3 | pooled connection reused across tenants → 0 cross-tenant reads; no stale `app.workspace_id` | `test_pgbouncer_txn_isolation.py::test_no_guc_leak_across_pooled_requests` |

Regression: full `pytest -q` (016/018 suites must stay green — nothing in their code changes). Lint/type: `ruff check . && mypy .`.

## Project Structure

```text
services/
├── api/
│   └── main.py                  # CHANGE (optional, D3) — /readyz Redis health field
├── tests/
│   ├── test_redis_degraded_mode.py       # NEW — the FR-024 degraded-mode matrix (fakeredis-down)
│   └── test_pgbouncer_txn_isolation.py   # NEW — FR-026 pooled cross-tenant no-leak (integration)
deploy/
├── compose.yaml                 # CHANGE — add pgbouncer (txn mode); app role through it, owner direct (D4)
└── pgbouncer/                   # NEW — pgbouncer.ini + userlist (transaction pool_mode)
docs/backend/
└── <degraded-mode-decision>.md  # NEW — per-control behavior + recovery + Redis blast-radius (FR-025)
```

**Structure Decision**: 021 is deliberately **light on `api/` code** — the controls already behave correctly (D1); the value is **evidence** (the degraded-mode matrix + pooled no-leak test) and **operability** (the decision record + PgBouncer deployment). The one optional code touch is `/readyz` (D3). `services/runtime/` untouched (P6).

## Complexity Tracking

| Deviation | Why needed | Simpler alternative rejected |
|-----------|-----------|------------------------------|
| Feature is mostly tests + docs + deploy, not new code (D1) | The controls (016/018) + GUC mechanism (`tenancy.py`) already implement the required degraded behavior; FR-024/025/026 are validation/documentation obligations | Building "new" degraded-mode code — would duplicate or destabilize already-merged, tested controls |
| Documenting idempotency/spend degraded behavior against today's reality, not future Redis (D2) | Only rate-limit + deny-set touch Redis today; idempotency is in-process and spend is Postgres-backed | Documenting against a not-yet-built Redis store — claims behavior that doesn't exist (violates P1/P8) |
| Adding PgBouncer to the deployment (D4) | FR-026 requires validating isolation **under transaction-mode pooling**, which isn't in the current compose | Testing isolation without a pooler — would not exercise the actual shared-physical-connection leak path the GUC design defends against |
