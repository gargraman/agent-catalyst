# Contract: Rate limiting (016 / F5)

Canonical envelope + headers defined in the umbrella
[contracts/rate-limit.md](../../015-m5-security-completion/contracts/rate-limit.md). This slice's delivered
contract = that behavior, applied **per principal** to the 9 gated routes (per-token invoke keying lands in 018).

## Behavior (applied via `rate_limit(route_class)` dependency)

- Runs after `require_permission` (403 first), before `enforce_spend_cap`/handler (429 before any work). Wired
  as a **decorator dependency** on every gated route so it sheds load before the handler's `tenant_db` opens a
  connection (not just as a trailing param).
- **Success** headers `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` ride responses that
  FastAPI serializes. **Known limitation:** handlers that return a `Response`/`StreamingResponse` directly
  (invoke's SSE stream, 204 deletes) do **not** carry the success headers — FastAPI only merges the injected
  `Response`'s headers for serialized return values. The **429 deny path always carries headers** (via the
  `RateLimited` error envelope), so throttled clients always get `Retry-After` + `X-RateLimit-*`.
- **Over limit**:
  ```
  HTTP/1.1 429 Too Many Requests
  Retry-After: <seconds>
  X-RateLimit-Limit: <N>
  X-RateLimit-Remaining: 0
  X-RateLimit-Reset: <epoch-s>
  Content-Type: application/json

  { "error": { "code": "rate.limited", "message": "rate limit exceeded; retry after <s>s" } }
  ```
  Envelope produced by the existing `RateLimited(AppError)` + `app_error_handler` (extended to pass `headers`).
- **Redis unavailable**: request **allowed**, no 429, `ac_rate_limit_degraded_total` incremented + warning log
  (fail open + alert). Never rejects on the limiter's own infra failure.

## Delivered vs deferred

| FR | 016 delivers | Deferred |
|----|--------------|----------|
| FR-001 | engine + invoke route keyed per principal today | per-**token** keying → 018 |
| FR-002 | per-principal limits on playground/eval/ingest | — |
| FR-003 | limits table (defaults) | numeric tuning at tasks |
| FR-004 | 429 + Retry-After + X-RateLimit-* | — |
| FR-005 | per-identity isolation | — |
| FR-024a | fail open + alert on Redis down | deny-set/spend fail-closed → 018/021 |

## Acceptance → see plan.md TDD mapping (`tests/test_rate_limit.py`).
