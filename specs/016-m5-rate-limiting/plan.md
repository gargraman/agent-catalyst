# Implementation Plan: M5 — Rate Limiting (F5)

**Branch**: `feat/backend-m0-foundation` (feature dir `016-m5-rate-limiting`) | **Date**: 2026-11-11 | **Spec**: [spec.md](./spec.md)

**Input**: `specs/016-m5-rate-limiting/spec.md` · **Parent umbrella**: [`015-m5-security-completion`](../015-m5-security-completion/) (shared design)

## Summary

Fill the existing `rate_limit()` no-op seam (`api/deps.py:139`, wired to 9 routes) with a **Redis sliding-window
limiter** and introduce the **shared async Redis client** that 018/021 build on. Over-limit requests get
`429 rate.limited` + `Retry-After`/`X-RateLimit-*` **before** any provider/ingestion work; limits are per
identity; on a Redis outage the limiter **fails open and alerts** (availability control — not fail-closed).

**Key reuse / deltas found in code:**
- `RateLimited(AppError)` already exists (`errors.py:58`, 429/`rate.limited`) — raise it, don't define it.
- `app_error_handler` (`errors.py:111`) builds `JSONResponse` with **no headers** → extend `AppError` with an
  optional `headers` dict and have the handler pass it, so a 429 can carry `Retry-After`/`X-RateLimit-*`.
- `rate_limit()` is a bare no-op dependency → replace with a **factory** `rate_limit(route_class)` (mirrors
  `require_permission("…")`); update the 9 `Depends(rate_limit)` call sites to name their route class.
- `AC_REDIS_URL` setting already exists (`settings.py`, default `None`) — unset ⇒ limiter no-op-allows (dev/test
  parity); the new `redis_client.py` owns the pool.

## Technical Context

**Language/Version**: Python 3.12 (`services/`)
**Primary Dependencies**: FastAPI, SQLAlchemy async (unaffected); **NEW**: `redis` (async) prod, `fakeredis` test
**Storage**: Redis (transient counters) — **no Postgres change**
**Testing**: pytest + `fakeredis` (deterministic, no live Redis); Redis-optional per research R1
**Project Type**: Web service (`services/api`)
**Performance Goals**: one pipelined Redis round-trip per gated request; negligible added latency
**Constraints**: limit engages before expensive work; **fail open + alert** on Redis outage (clarified); no
token/secret in keys, logs, or errors (P5); `services/runtime/` untouched (P6)
**Scale/Scope**: 9 existing console routes + the invoke route; per-principal keying this slice

## Constitution Check

*GATE: pre-Phase 0 + re-checked post-Phase 1.*

| Principle | Status | Notes |
|-----------|--------|-------|
| P1 outcome | ✅ | Operator/tenant protection from abuse + runaway spend; measurable SCs. No LLM. |
| P2 scope/phases | ✅ | Bounded slice of the owner-approved M5 split; per-token keying explicitly deferred to 018 (no silent drop). |
| **P3 deterministic fail-closed** | ✅ **(1 justified deviation)** | Server-side deterministic limiter; **rate-limit fails OPEN+alert on Redis outage** (availability control) — see Complexity Tracking. |
| P4 evidence | ✅ (N/A judge) | No model/prompt behavior; deterministic TDD only. |
| P5 data/tenant | ✅ | Keys use `token_id`/`subject`, never secrets; nothing secret logged; per-identity isolation tested. |
| P6 reproducible | ✅ | `services/runtime/` not touched; no snapshot/version change. |
| P7 providers/cost | ✅ | Protects provider quota/spend; no contract change. |
| P8 operate/visible | ✅ | Fail-open path raises an alert signal (metric + log); 429 is explicit, not silent. |
| P9 test-first | ✅ | Red→Green→Refactor; focused command `pytest tests/test_rate_limit.py`; no live calls. |

**Gate: PASS** (one justified deviation).

## Design

### Components

- **`api/redis_client.py`** (NEW, shared): lazy async client from `AC_REDIS_URL`; `get_redis() -> Redis | None`
  (None when unset → caller treats as allow); `is_healthy()` for `/readyz` (readiness wiring is light here,
  completed in 021). Single module-level pool.
- **`api/ratelimit.py`** (NEW): `async def check(key, limit, window_s) -> Decision`. Sliding window via an
  atomic Lua script (or `ZADD`+`ZREMRANGEBYSCORE`+`ZCARD` pipeline). Returns `Decision(allowed, remaining,
  reset_epoch, retry_after)`. On any Redis error → **`Decision(allowed=True, degraded=True)`** (fail open) and
  the caller emits the alert. Limits table lives in settings (per route_class: window_s + limit).
- **`api/deps.py`** CHANGE: `rate_limit(route_class)` factory → resolves `session_principal` → builds
  `rl:prin:{subject}:{route_class}`, reads the limit from the table, calls `ratelimit.check`; on deny raises
  `RateLimited(headers={Retry-After, X-RateLimit-*})`; on `degraded` logs + increments a metric and allows.
- **`api/errors.py`** CHANGE: `AppError.__init__(..., headers: dict|None=None)`; `app_error_handler` passes
  `headers=exc.headers` to `JSONResponse`. (Back-compatible — default None.)
- **`api/metrics.py`** CHANGE: add `ac_rate_limited_total{route_class}` + `ac_rate_limit_degraded_total`
  counters (the fail-open "alert" signal; best-effort, never affects the response).
- **Call sites** (9 routes): `Depends(rate_limit)` → `Depends(rate_limit("<class>"))` (playground/eval/ingest/
  invoke/…). Invoke stays per-principal **for now**; 018 re-keys it to `rl:token:{id}` via the same engine.

### Pipeline position

`rate_limit(...)` runs as a route dependency **after** `require_permission` (403 first) and **before**
`enforce_spend_cap` / the handler body — so a 429 is raised before any provider/ingestion work (SC-001).

## TDD mapping (acceptance → Red test → command)

| Acceptance / FR | Red test (in `tests/test_rate_limit.py`) | Command |
|-----------------|------------------------------------------|---------|
| FR-001/004, AS1 | N+1 in window → `429` + `Retry-After`/`X-RateLimit-*`; no provider call | `pytest tests/test_rate_limit.py::test_over_limit_429_headers` |
| FR-002, AS2 | expensive console route over limit → `429` before work | `::test_console_route_throttled_before_work` |
| FR-005, AS3 | two principals → one throttled, other 200 | `::test_per_identity_isolation` |
| AS4 | window elapses → allowed again | `::test_window_reset_allows` |
| FR-024a/SC-016a, AS5 | Redis raises → allowed + `ac_rate_limit_degraded_total`++ (fail open+alert) | `::test_redis_down_fails_open_alerts` |
| FR-003 | limits table drives window/limit per route_class | `::test_limits_table_applied` |
| headers | success response carries `X-RateLimit-*` | `::test_success_headers_present` |

Regression: full `pytest -q` (the 9 routes' existing auth/membership tests must stay green after the factory
change). Lint/type: `ruff check . && mypy .`.

## Project Structure

```text
services/
├── api/
│   ├── redis_client.py   # NEW (shared infra)
│   ├── ratelimit.py      # NEW (sliding-window engine + limits table lookup)
│   ├── deps.py           # CHANGE rate_limit() → rate_limit(route_class) factory
│   ├── errors.py         # CHANGE AppError.headers + handler passes them
│   ├── metrics.py        # CHANGE rate-limit counters
│   ├── settings.py       # CHANGE limits table + enable flag (AC_REDIS_URL already present)
│   └── routers/*.py      # CHANGE 9 Depends(rate_limit) → Depends(rate_limit("<class>"))
└── tests/
    └── test_rate_limit.py  # NEW (fakeredis)
```

**Structure Decision**: fill existing seams; the engine is identity-agnostic (`check(key, …)`) so 018 reuses it
for token keying with no rework. `services/runtime/` untouched.

## Complexity Tracking

| Deviation | Why needed | Simpler alternative rejected |
|-----------|-----------|------------------------------|
| Rate limiting **fails open** on Redis outage (vs P3 fail-closed default) | It's an availability/cost control; failing closed makes Redis a hard SPOF for all traffic (self-inflicted outage) — clarified 2026-11-11 | Uniform fail-closed rejected; fail-open is paired with a mandatory alert metric (P8), and security controls (deny-set/spend, in 018/021) still fail closed |
| Per-token invoke limit deferred to 018 | The caller-token identity (`resolve_caller`) does not exist until 018; 016 delivers the engine + per-principal keying | Building a token plane inside 016 would merge two slices and break the dependency order; the engine's `check(key,…)` API makes 018's re-keying trivial |
