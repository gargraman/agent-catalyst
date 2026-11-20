# Contract: Rate limiting (015a / F5)

Not a new endpoint — a cross-cutting behavior applied to existing routes via the `rate_limit()` dependency
(`api/deps.py:139`, today a no-op wired to 9 routes) plus the published-invoke path.

## Applied surfaces

- **Published invoke** (`POST /v1/agents/:id/invoke`): per-token limit (`rl:token:{token_id}`).
- **Expensive console routes** (at least: playground invoke, eval run/create, ingestion trigger): per-principal
  limit (`rl:prin:{subject}:{route_class}`).

## Success response headers (every rate-limited response)

```
X-RateLimit-Limit: <N>
X-RateLimit-Remaining: <n>
X-RateLimit-Reset: <unix-seconds>
```

## Reject response (over limit)

```
HTTP/1.1 429 Too Many Requests
Retry-After: <seconds>
X-RateLimit-Limit: <N>
X-RateLimit-Remaining: 0
X-RateLimit-Reset: <unix-seconds>
Content-Type: application/json

{ "error": { "code": "rate.limited", "message": "rate limit exceeded; retry after <s>s" } }
```

- No provider/ingestion work is performed for a 429 (FR-001/002).
- Error envelope matches the existing `AppError` → envelope shape (`api/errors.py`).

## Limits table (config; conservative defaults, tunable — research R2)

| Surface | Default window | Default limit | Key |
|---------|----------------|---------------|-----|
| invoke (per token) | 60s | TBD-at-tasks (conservative) | `rl:token:{id}` |
| playground (per principal) | 60s | TBD | `rl:prin:{sub}:playground` |
| eval run (per principal) | 60s | TBD (low) | `rl:prin:{sub}:eval` |
| ingestion (per principal) | 60s | TBD (low) | `rl:prin:{sub}:ingest` |

## Degraded behavior

Redis unavailable → **allow + emit alert** (fail open). The limiter MUST NOT reject on its own infra failure
(clarified 2026-11-11). Contrast: deny-set/spend reservation fail closed.

## Acceptance (maps to FR-001..005, SC-001/002)

1. N+1 requests in one window → (N+1)th is `429` with `Retry-After`; no provider call.
2. Expensive console route over limit → `429` before model/ingestion work.
3. Two tokens, one throttled → the other still `200`.
4. Window elapses → requests succeed again.
5. Redis down → requests allowed, alert emitted (not `429`).
