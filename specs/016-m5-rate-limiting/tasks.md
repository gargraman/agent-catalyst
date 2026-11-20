---
description: "Task list — 016 M5 Rate Limiting (F5)"
---

# Tasks: M5 — Rate Limiting (F5)

**Input**: Design documents from `/specs/016-m5-rate-limiting/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/rate-limit.md
**Tests**: REQUIRED — Constitution P9 mandates test-first (Red→Green→Refactor). All paths under `services/`.

**Single user story (US1).** MVP = US1 = the whole feature. Per-**token** invoke keying is deferred to 018
(the engine is built identity-agnostic here so 018 re-keys with no rework — see plan Complexity Tracking).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no incomplete deps)
- Paths are repo-relative; run commands from `services/`.

---

## Phase 1: Setup (Shared Infrastructure)

- [x] T001 Add `redis` (async client, prod) and `fakeredis` (dev/test) to `services/pyproject.toml` and sync the venv (match the repo's existing dependency-group layout).
- [x] T002 [P] Add the rate-limit config to `services/api/settings.py`: a `rate_limits` table mapping each `route_class` (`invoke`, `playground`, `eval`, `ingest`, `default`) → `{window_s: int, limit: int}` with conservative defaults, plus an enable flag. (`AC_REDIS_URL` already exists — do not re-add.)

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ Must complete before US1 implementation can go Green. Scaffolding only — no tested behavior yet (keeps Red evidence valid per P9).**

- [x] T003 Create `services/api/redis_client.py` **stub**: module-level lazy `redis.asyncio` pool from `AC_REDIS_URL`; `get_redis() -> Redis | None` (returns `None` when unset) and `is_healthy()` signature. No limiter logic yet (scaffold so tests import).
- [x] T004 Create `services/api/ratelimit.py` **stub**: `Decision` dataclass (`allowed: bool`, `remaining: int`, `reset_epoch: int`, `retry_after: int`, `degraded: bool`) with the invariant `degraded=True ⇒ allowed=True`; `async def check(key, limit, window_s) -> Decision` raising `NotImplementedError`. Scaffold only.
- [x] T005 Extend `AppError` in `services/api/errors.py` with an optional `headers: dict | None = None` constructor arg (default `None`, back-compatible) and make `app_error_handler` pass `headers=exc.headers` to `JSONResponse`.
- [x] T006 [P] Add rate-limit counters to `services/api/metrics.py`: `ac_rate_limited_total{route_class}` and `ac_rate_limit_degraded_total` (best-effort; never affects the response — mirror `record_refusal`).

**Checkpoint**: Redis client + limiter module import cleanly; `AppError` can carry headers; metrics exist.

---

## Phase 3: User Story 1 — Rate limiting (Priority: P1) 🎯 MVP

**Goal**: Over-limit requests on gated surfaces get `429 rate.limited` + `Retry-After`/`X-RateLimit-*` before any provider/ingestion work; per-identity; fail **open + alert** on Redis outage.

**Independent Test**: Drive a principal past a route's window limit → 429 with headers, no provider call; a second principal is unaffected; with Redis raising, requests are allowed and the degraded counter increments.

### Tests for User Story 1 (write FIRST, confirm they FAIL on behavior — not on import) ⚠️

- [x] T007 [US1] Add the `fakeredis` async fixture + test-module scaffold in `services/tests/test_rate_limit.py` (import the real app/deps; no behavior assertions yet) so subsequent Red tests fail on behavior, not import.
- [x] T008 [US1] Red test `test_over_limit_429_headers` in `services/tests/test_rate_limit.py`: N+1 requests in one window → (N+1)th returns `429` code `rate.limited` with `Retry-After`, `X-RateLimit-Limit/Remaining/Reset`; assert **no provider call** for the rejected request AND that `ac_rate_limited_total{route_class}` increments (429 telemetry; code-review #2) (FR-001/FR-004/FR-009, AS1).
- [x] T009 [US1] Red test `test_console_route_throttled_before_work`: an expensive console route (playground/eval/ingest) over its limit → `429` raised before any model/ingestion work begins (FR-002, AS2).
- [x] T010 [US1] Red test `test_per_identity_isolation`: two distinct principals on the same route — one throttled, the other still `200` (FR-005, AS3).
- [x] T011 [US1] Red test `test_window_reset_allows`: after the window elapses, a previously-throttled identity succeeds again (AS4).
- [x] T012 [US1] Red test `test_redis_down_fails_open_alerts`: with `get_redis`/`check` raising a Redis error, the request is **allowed** (no 429) and `ac_rate_limit_degraded_total` increments (FR-024a, SC-016a, AS5).
- [x] T013 [US1] Red test `test_limits_table_applied`: window/limit come from the settings `rate_limits` table per `route_class` (FR-003).
- [x] T013b [US1] Red test `test_unlisted_route_class_falls_back_to_default`: a `route_class` absent from the table resolves via `settings.rate_limit_for()` to the `default` bucket (no `KeyError` at request time; code-review #4) (FR-003).
- [x] T014 [US1] Red test `test_success_headers_and_no_secret`: a successful gated response carries `X-RateLimit-*`; assert no raw token/secret appears in the rate-limit key or logs (FR-005/FR-028, P5).

### Implementation for User Story 1 (make the above Green, in dependency order)

- [x] T015 [US1] Implement `services/api/redis_client.py`: real lazy pool + `get_redis()` None-safe + `is_healthy()` ping.
- [x] T016 [US1] Implement the sliding-window engine in `services/api/ratelimit.py` `check()`: sorted-set `ZREMRANGEBYSCORE`→`ZCARD`→`ZADD` wrapped in an atomic Lua script; populate `Decision`; on any Redis error return `Decision(allowed=True, degraded=True)` (fail open). Depends on T015.
- [x] T017 [US1] Use `settings.rate_limit_for(route_class)` (the fallback-safe helper, T002 — NOT a raw `rate_limits[...]` lookup; code-review #4) for window/limit, and, on `degraded`, increment `ac_rate_limit_degraded_total` + emit a warning log (the alert signal). Depends on T016, T006.
- [x] T018 [US1] Replace the no-op `rate_limit()` in `services/api/deps.py` with a `rate_limit(route_class: str)` factory: resolve `session_principal` → key `rl:prin:{subject}:{route_class}`, call `ratelimit.check`, on deny raise `RateLimited(message, headers={Retry-After, X-RateLimit-*})`, on allow attach success headers; on `degraded` allow. Depends on T016, T017, T005.
- [x] T019 [US1] **⚠️ ATOMIC with T018** (code-review #1): the factory signature change and this migration MUST land in the same change — FastAPI binds the factory's `route_class` as a request query param on any un-migrated `Depends(rate_limit)` site (silent, not a clean crash). Update **every** `Depends(rate_limit)` occurrence (~30 endpoints across the 9 router files: `agents.py`, `evals.py`, `invoke.py`, `knowledge.py`, `tools.py`, `skills.py`, `members.py`, `provider_keys.py`, `workspaces.py`) to `Depends(rate_limit("<route_class>"))`, placing it **after** `require_permission` and **before** `enforce_spend_cap`/handler. **Route-class mapping**: `invoke.py` invoke → `invoke`; `evals.py` run → `eval`; `knowledge.py` source-add/reingest → `ingest`; all other CRUD reads/writes → `default`. (Verified: no test overrides `rate_limit`, so the factory signature change is safe.)
- [x] T020 [US1] Attach success `X-RateLimit-*` headers to gated responses by having the `rate_limit(route_class)` **dependency set them on the injected FastAPI `Response`** (not middleware), so T014 passes without altering handler bodies.

### Refactor (keep all US1 tests green)

- [x] T021 [US1] Extract the key-builder + header-builder into small pure helpers in `ratelimit.py`; confirm limiter ordering (after authz, before spend/handler) and that the engine API stays identity-agnostic (`check(key,…)`) for 018 reuse.

**Checkpoint**: US1 fully functional and independently testable — `pytest tests/test_rate_limit.py` green.

---

## Phase 4: Polish & Cross-Cutting

- [x] T022 Run full regression `pytest -q` — the 9 routes' existing auth/membership tests stay green after the `rate_limit(route_class)` factory change.
- [x] T023 [P] `ruff check . && mypy .` clean.
- [x] T024 [P] Secret-scan check: assert no token/secret value appears in rate-limit keys, logs, or the 429 envelope (P5 / FR-028).
- [x] T025 Run `quickstart.md` validation (incl. optional manual live Redis check).
- [x] T026 At DoD: update tracker memory `m5-security-split` (016 impl ✅) and reconcile the F5 / rate-limit row in `docs/backend-integration-status.md` + the M5 §5.a note in `docs/implementation-plan.md` (per the update-docs-after-feature rule).

---

## Dependencies & Execution Order

- **Setup (T001–T002)** → **Foundational (T003–T006)** → **US1 (T007–T021)** → **Polish (T022–T026)**.
- Within US1: **tests (T007–T014) before implementation (T015–T020)**; T015 → T016 → T017 → T018 → T019/T020; refactor T021 last.
- T005 (AppError.headers) blocks T018 (429 headers) and T008/T014 going green.

### Parallel Opportunities

- T002 ∥ (after T001).
- T006 ∥ other foundational (different file).
- Red tests T008–T014 are all in **one file** (`test_rate_limit.py`) → write sequentially after the T007 scaffold (not parallel).
- T023 ∥ T024 in Polish.

---

## Implementation Strategy

Single-story MVP: finish Setup + Foundational, then drive US1 strictly test-first (Red evidence shown to owner
before Green, per P9), then Polish. Keep `services/runtime/` untouched (P6). No live provider calls anywhere —
`fakeredis` + error injection cover the window and fail-open paths.

## Notes

- Per-token invoke keying is intentionally **out of this slice** (→ 018); the engine's `check(key,…)` API makes
  that a wiring change, not a rewrite.
- Fail-open-on-Redis-outage is the one justified deviation from fail-closed (availability control) — paired with
  the degraded-counter alert. Deny-set/spend reservation (018/021) fail **closed**.
