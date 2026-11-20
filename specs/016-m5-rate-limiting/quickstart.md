# Quickstart / Validation: 016 — Rate Limiting (F5)

Run from `services/`. Deterministic; no live provider calls. Redis simulated with `fakeredis`.

## Prerequisites

```bash
cd services
# .venv present. Tests use fakeredis (no real Redis needed).
# AC_REDIS_URL unset → limiter no-ops (allow); set it only for a manual live check.
# AC_ENV=dev AC_AUTH_PROVIDER=dev for the test principal.
```

Add deps (match repo's pyproject tooling): `redis` (prod), `fakeredis` (dev/test).

## Focused TDD loop (Red → Green)

```bash
.venv/bin/pytest tests/test_rate_limit.py -q         # the slice suite
.venv/bin/pytest -q                                  # full regression (9 routes' auth/membership stay green)
.venv/bin/ruff check . && .venv/bin/mypy .           # lint/type gates
```

## Expected outcomes

- **Over limit**: N+1 requests in a window → `429 rate.limited` with `Retry-After` + `X-RateLimit-*`; no provider
  call occurs for the rejected request.
- **Before work**: an expensive console route (playground/eval/ingest) over its limit → `429` before any model or
  ingestion work starts.
- **Per-identity**: two principals on the same route → only the over-limit one is throttled.
- **Reset**: after the window elapses, requests succeed again.
- **Fail open + alert**: with the Redis client raising, requests are **allowed** and
  `ac_rate_limit_degraded_total` increments (no self-inflicted 429).
- **Headers**: successful gated responses carry `X-RateLimit-*`.

## Manual live check (optional)

```bash
AC_REDIS_URL=redis://localhost:6379/0 .venv/bin/uvicorn api.main:app  # then hammer a gated route, observe 429 + headers
```

## Done-when (this slice)

- `tests/test_rate_limit.py` green + full `pytest` regression green; ruff + mypy clean.
- No token/secret in any rate-limit key, log, or error (P5 secret-scan assertion).
- Update umbrella tracker [[m5-security-split]] (016 plan ✅ → after impl, impl ✅) and, at DoD, reconcile
  `docs/backend-integration-status.md` rate-limit/F5 row.
