---
description: "Task list — 021 M5 Redis-SPOF Degraded Mode + PgBouncer Transaction-Mode (milestone capstone)"
---

# Tasks: M5 — Redis-SPOF Degraded Mode + PgBouncer Transaction-Mode

**Input**: Design documents from `/specs/021-m5-redis-degraded-pgbouncer/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md · **Depends on 016 (rate limiting) + 018 (deny-set)** — both built; this feature **validates + documents + hardens** them.
**Tests**: REQUIRED — Constitution P9 (Red→Green→Refactor); docs-only tasks use link/consistency checks (P9 docs clause). Python paths under `services/`; deployment under `deploy/`; docs under `docs/backend/`.

**Single user story (US1).** MVP = US1 = the whole feature. **This is a validation/documentation/deploy feature, not a new-controls build** (D1): the headline behaviors already exist in code.
**Owner rulings (2026-11-12, all decisions ruled):** **D1** confirmed — validation/evidence/deploy feature, not a new-controls build. **D2** confirmed — document idempotency/spend degraded behavior against today's in-process/Postgres reality; the Redis idempotency/reservation store stays **deferred** (not built here). **D3** — a **configured-but-unreachable** Redis flips `/readyz` to `503`; unset `AC_REDIS_URL` stays `200` (parity carve-out). **No migration** (topology + tests + docs only).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no incomplete deps)
- Paths are repo-relative; run commands from `services/` (Python) or repo root (deploy).

---

## Phase 1: Setup

- [ ] T001 Confirm on disk (no code change) the already-built degraded behaviors this feature validates: rate-limit fail-open+alert (`services/api/ratelimit.py:82`, `services/api/deps.py:246-254`, `services/api/metrics.py:73`); deny-set fail-closed (`services/api/auth/denyset.py:48`, `deps.py:72-77`); `SET LOCAL` GUC (`services/api/tenancy.py:32`, `services/api/db.py:66`); in-process idempotency (`deps.py:315`); Postgres spend cap (`deps.py:172-189`). Record the line refs for the decision doc (T014).

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ Must complete before US1 can go Green. No migration — a deployment-topology change + a Redis-down test harness.**

- [ ] T002 Add a Redis-down test harness in `services/tests/_redis_degraded.py` (or a conftest fixture): a `fakeredis` client whose ops raise `ConnectionError`, plus a helper to toggle `AC_REDIS_URL` set-but-dead vs. unset. Used by the degraded-mode matrix (T006–T011) and the readyz test (T012).
- [ ] T003 Add a PgBouncer service to `deploy/compose.yaml` (`pool_mode = transaction`) + a `deploy/pgbouncer/` config dir (`pgbouncer.ini`, `userlist.txt`). Route the **app** role (`ac_app` / `AC_APP_DATABASE_URL`) through PgBouncer; leave the **owner/migration** role (`ac` / `AC_DATABASE_URL`/`AC_DATABASE_SYNC_URL`) on a **direct** Postgres connection (D4).

**Checkpoint**: the degraded harness imports; `docker compose -f deploy/compose.yaml config` validates with the new pgbouncer service.

---

## Phase 3: User Story 1 — Degrade safely + prove tenant isolation under pooling (P3) 🎯 MVP

**Goal**: With the shared cache unavailable, each dependent control exhibits its **documented** degraded behavior (deny-set + spend fail closed; idempotency fails closed retryable; rate limit fails open + alert); `/readyz` flips to `503` when a configured Redis is down; the blast radius + per-control decision are documented and test-verified; tenant isolation holds under transaction-mode pooling (0 cross-tenant reads).

**Independent Test**: make the shared store unavailable, exercise each control, confirm its documented behavior; confirm `/readyz` 503; confirm 0 cross-tenant reads under PgBouncer txn-mode.

### Tests for User Story 1 (write FIRST, confirm they FAIL on behavior) ⚠️

- [ ] T004 [US1] Scaffold `services/tests/test_redis_degraded_mode.py` using the T002 harness (import real app/deps; no behavior assertions) so Red tests fail on behavior, not import.
- [ ] T005 [US1] Scaffold `services/tests/test_pgbouncer_txn_isolation.py` (integration): bind a pooled engine (NOT `NullPool`) through the PgBouncer service; two-tenant request helper. No behavior assertions yet.
- [ ] T006 [US1] Red `test_denyset_fails_closed_both_planes`: Redis configured-but-down → console session auth AND caller-token auth both 401 (0 through unchecked) (FR-024, AS1).
- [ ] T007 [US1] Red `test_ratelimit_fails_open_and_alerts`: Redis down → request allowed, `ac_rate_limit_degraded_total` increments, a WARNING is emitted (FR-024, AS1).
- [ ] T008 [US1] Red `test_spend_cap_fails_closed`: a forced ledger/compute error → the spend gate refuses before any paid call (FR-024, AS1).
- [ ] T009 [US1] Red `test_idempotency_fails_closed_retryable`: an in-flight retry of the same `Idempotency-Key` → `409` (retryable), independent of Redis (FR-024, AS1).
- [ ] T010 [US1] Red `test_controls_resume_after_redis_recovers`: Redis back mid-run → deny-set/rate-limit resume normal behavior without a restart (edge case).
- [ ] T011 [US1] Red `test_readyz_503_when_configured_redis_down`: `AC_REDIS_URL` set but Redis unreachable → `/readyz` 503 `{database: ok, redis: down}`; `AC_REDIS_URL` **unset** → `/readyz` 200 (D3 carve-out).
- [ ] T012 [US1] Red `test_no_guc_leak_across_pooled_requests` (in `test_pgbouncer_txn_isolation.py`): two tenants reusing one pooled physical connection → 0 cross-tenant reads; no stale `app.workspace_id` across requests (FR-026, AS3).

### Implementation for US1 (make the above Green, dependency order)

- [ ] T013 [US1] Add the Redis check to `/readyz` in `services/api/main.py` (D3): when `AC_REDIS_URL` is configured, call `redis_client.is_healthy()`; unhealthy → `503 {database: ok, redis: down}`; unset `AC_REDIS_URL` → Redis is not a readiness dependency, keep `200`. This is the only application-code change (the controls are already correct — D1).
- [ ] T014 [US1] Write the **degraded-mode decision record** `docs/backend/<degraded-mode>.md` (FR-025): per-control behavior + recovery path (deny-set/spend = fail closed; idempotency = fail closed retryable, in-process; rate limit = fail open + alert) + the Redis **blast-radius** map, each claim citing the code line refs from T001. Note idempotency/spend do not depend on Redis today (D2). **Document the D3-widened radius**: a configured-Redis outage flips `/readyz`→503 and 401s console auth (deny-set fail-closed) — the "accepted SPOF" takes authenticated console + readiness down together (021-M3).
- [ ] T015 [US1] (If D2 so rules) Add a consistency check `test_decision_record_matches_code` asserting each decision-record claim ties to the cited behavior; otherwise a docs link/consistency check over `docs/backend/<degraded-mode>.md` (P9 docs clause).

### Refactor (keep all US1 tests green)

- [ ] T016 [US1] Tidy the `/readyz` body (shared check helper if it grows); confirm no control code (`denyset.py`/`ratelimit.py`/`deps.py`/`tenancy.py`) was altered (D1) and `services/runtime/` untouched (P6).

**Checkpoint**: `pytest tests/test_redis_degraded_mode.py` green; `pytest tests/test_pgbouncer_txn_isolation.py` green against the pooled stack.

---

## Phase 4: Polish & Cross-Cutting

- [ ] T017 Run full regression `pytest -q` — the 016/018 suites stay green (no control code changed; only `/readyz` + tests + docs).
- [ ] T018 [P] `ruff check . && mypy .` clean (defer to CI if tools absent locally — note it honestly).
- [ ] T019 Run `quickstart.md` validation (degraded-mode matrix + the PgBouncer txn-mode isolation run).
- [ ] T020 At DoD: update tracker memory `m5-security-split` (021 ✅, **M5 milestone closed — capstone**) and reconcile the degraded-mode/PgBouncer rows in `docs/backend-integration-status.md` + `docs/implementation-plan.md` (per update-docs-after-feature). Cross-link the decision record (T014).

---

## Dependencies & Execution Order

- **Setup (T001)** → **Foundational (T002–T003)** → **US1 (T004–T016)** → **Polish (T017–T020)**.
- Within US1: **tests (T004–T012) before implementation (T013–T015)**; T013 (readyz) makes T011 green; T014 (doc) + T015 (its check); the degraded-matrix tests T006–T010 are green **without new code** (they validate already-built behavior — if any fails, that is a real 016/018 regression to fix, not new work); refactor T016 last.
- T002 (harness) blocks T006–T011. T003 (PgBouncer) blocks T012.

### Parallel Opportunities

- T002 ∥ T003 (Python harness vs. deploy config).
- Red tests T006–T011 share one file → sequential after T004; T012 is a separate file (∥ with the T006–T011 group).
- T018 ∥ others in Polish.

---

## Implementation Strategy

Single-story MVP, test-first (P9). **Honest scoping (D1):** the degraded-mode matrix and the pooled no-leak test are mostly *validation of already-merged behavior* (016/018, `tenancy.py`) — the only new application code is the `/readyz` Redis check (D3, flips to 503). The deliverables that carry the feature are **evidence** (the matrix + isolation test) and **operability** (the decision record + the PgBouncer deployment). Idempotency/spend degraded behavior is documented against today's in-process/Postgres reality, not a not-yet-built Redis store (D2). `fakeredis` error-injection + the pooled stack cover all paths — no live provider calls. `services/runtime/` untouched (P6). Completing this closes M5.
