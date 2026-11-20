# Phase 1 Data Model: 016 — Rate Limiting

**No Postgres schema change.** All state is transient in Redis. See umbrella
[data-model Redis table](../015-m5-security-completion/data-model.md).

## Redis structures

| Key | Type | Purpose | TTL | Degraded (Redis down) |
|-----|------|---------|-----|-----------------------|
| `rl:prin:{subject}:{route_class}` | sorted set (ts→member) | per-principal sliding-window count | `window_s` (idle expiry) | **fail open + alert** |
| `rl:token:{token_id}` | sorted set | per-token count (keyed by **018** when `resolve_caller` exists) | `window_s` | fail open + alert |

## Limits table (config, `settings.py`)

Per `route_class`: `{ window_s: int, limit: int }`. Conservative defaults, tunable (FR-003). Finalize numbers at
tasks against expected pilot traffic; the gate is "limits exist + fire before expensive work", not a number.

| route_class | gated routes (examples) | default window | default limit |
|-------------|-------------------------|----------------|---------------|
| `invoke` | `POST /agents/:id/invoke` | 60s | conservative |
| `playground` | playground invoke | 60s | conservative |
| `eval` | eval run/create | 60s | low |
| `ingest` | KB source add / reingest | 60s | low |
| `default` | other gated reads/writes | 60s | generous |

## In-process objects

- **`Decision`** (returned by `ratelimit.check`): `allowed: bool`, `remaining: int`, `reset_epoch: int`,
  `retry_after: int`, `degraded: bool` (True when the limiter failed open on a Redis error).
- **`AppError.headers`** (new optional field): dict surfaced by `app_error_handler`; `RateLimited` sets
  `Retry-After` + `X-RateLimit-*`.

## Validation rules

- Keys never contain a raw token/secret — only `token_id` / `subject` (P5).
- `remaining ≥ 0`; `reset_epoch` is future; `retry_after ≥ 1` on deny.
- `degraded=True` ⇒ `allowed=True` (fail-open invariant) and a `ac_rate_limit_degraded_total` increment.
