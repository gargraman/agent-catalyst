# Phase 0 Research: 016 — Rate Limiting (F5)

Builds on umbrella [research R1/R2](../015-m5-security-completion/research.md). Slice-local decisions below. No
live provider calls.

## R16-1 — Shared Redis client, None-safe

- **Decision**: `api/redis_client.py` exposes a lazily-created module-level `redis.asyncio` pool from
  `AC_REDIS_URL`. `get_redis()` returns `None` when `AC_REDIS_URL` is unset; callers treat `None` as
  "limiter disabled → allow" (dev/test parity with today's no-op seam). `is_healthy()` pings for readiness.
- **Rationale**: Keeps dev/test runnable with no Redis; matches the existing seam behavior so the 9 routes don't
  break when Redis is absent. One pool avoids per-request connects.
- **Alternatives rejected**: Require Redis in all envs (breaks local/test ergonomics); per-request client (connect
  overhead).

## R16-2 — Test double: `fakeredis`

- **Decision**: Use `fakeredis` (asyncio) in `tests/test_rate_limit.py` for deterministic window/counter behavior;
  simulate outage by monkeypatching the client to raise on the limiter call.
- **Rationale**: Real Redis in CI is flaky/slow for timing tests; `fakeredis` implements the commands/Lua the
  sliding window needs. Fail-open is tested by injecting an error, not by tearing down a real server.
- **Alternatives rejected**: `testcontainers` Redis (heavier, slower, network); hand-rolled in-memory fake
  (reinvents `fakeredis`).

## R16-3 — Sliding-window mechanics + headers

- **Decision**: Per key, maintain a sorted set of request timestamps; on each request:
  `ZREMRANGEBYSCORE key -inf (now-window)` → `ZCARD` → if `< limit` then `ZADD` + allow else deny. Wrap in a Lua
  script for atomicity under concurrency. Emit `X-RateLimit-Limit`, `X-RateLimit-Remaining`,
  `X-RateLimit-Reset` (epoch secs) always; add `Retry-After` on deny.
- **Rationale**: True sliding window (no fixed-window boundary burst); atomic Lua removes the check-then-set race.
  Standard header names clients already handle.
- **Alternatives rejected**: Fixed-window counter (boundary burst doubles effective rate); token bucket (fine but
  heavier to reason about for "N per window"); GCRA (overkill here).

## R16-4 — AppError headers extension

- **Decision**: Add optional `headers: dict|None` to `AppError.__init__`; `app_error_handler` passes it to
  `JSONResponse(..., headers=headers)`. Default `None` → fully back-compatible with existing raisers.
- **Rationale**: The handler currently drops headers; a 429 must carry `Retry-After`/`X-RateLimit-*`. Smallest
  change that keeps one central envelope path.
- **Alternatives rejected**: A dedicated 429 handler (duplicates envelope logic); setting headers via middleware
  (can't see the exception's computed retry-after cleanly).

## R16-5 — Dependency factory + route classes

- **Decision**: `rate_limit(route_class: str)` returns a dependency resolving `session_principal` → subject,
  keying `rl:prin:{subject}:{route_class}`. Route classes: `invoke`, `playground`, `eval`, `ingest`, plus a
  default for the remaining gated reads/writes. Update the 9 `Depends(rate_limit)` sites.
- **Rationale**: Mirrors the proven `require_permission("…")` factory; lets the limits table target the expensive
  classes specifically (FR-002). Identity-agnostic engine (`check(key,…)`) → 018 re-keys invoke to `rl:token:{id}`
  with no engine change.
- **Alternatives rejected**: Infer route_class from request path (brittle, implicit); keep the bare no-op (can't
  distinguish surfaces → can't meet FR-002/003).

## Resolved summary

| Unknown | Resolution |
|---------|-----------|
| Redis client shape | lazy None-safe pool in `redis_client.py` (R16-1) |
| How to test windows/outage | `fakeredis` + error injection (R16-2) |
| Window algorithm + headers | sorted-set sliding window, atomic Lua, standard headers (R16-3) |
| 429 headers delivery | `AppError.headers` + handler passthrough (R16-4) |
| Per-identity/route keying | `rate_limit(route_class)` factory; 9 call-site updates (R16-5) |
