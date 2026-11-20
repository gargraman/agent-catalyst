# Quickstart / Validation: 023 — Per-Call Model Log + Correlation + Latency SLIs

## Prerequisites

- `services/.venv` set up; for integration scenarios the local stack (`make up`, Postgres on 5433) and migrations at
  head (incl. **0012**).
- Integration env (as used across this repo):
  `AC_ENV=dev AC_AUTH_PROVIDER=dev AC_DATABASE_SYNC_URL=postgresql+psycopg://ac:ac_dev_pw@localhost:5433/agent_catalyst
  AC_DATABASE_URL=postgresql+asyncpg://ac_app:ac_app_pw@localhost:5433/agent_catalyst
  AC_APP_DATABASE_URL=postgresql+asyncpg://ac_app:ac_app_pw@localhost:5433/agent_catalyst`
- All provider calls use the mocked transport seam (`transport.stream_chunks` replaced) — **no paid calls**.

## RED → GREEN order (TDD)

1. **Pure units** — `ModelCallRecord` emission per turn (single, correction, failed, interrupted); cost invariant
   (FR-005); redaction (exact-match key scrub + each pattern + fail-closed marker + redact-before-truncate);
   fingerprint (keyed, per-workspace distinct, stable within workspace); call-log line shape with content OFF has no
   text keys; `NormalizedRequest.provider_correlation_id` → `extra_headers` (32-hex only).
2. **Runtime purity** — `tests/test_runtime_purity.py` stays green (no new forbidden imports).
3. **API integration** — playground, published, eval (+ judge) each emit one line per call with identical key sets;
   correlation id identical across run row and lines, outbound kwargs carry only the platform provider id; telemetry GET/PUT (admin vs
   non-admin, audit line on change only); content ON → redacted text present, seeded secrets absent.
4. **Metrics** — first-token histogram observed for a streaming run; deadline counter == timeout runs per source;
   telemetry failure injection → run unaffected, `ac_call_log_errors_total` incremented.

## Validation matrix (maps to Success Criteria)

| SC | Scenario | Expected |
|---|---|---|
| SC-001 | success / provider error / timeout / deadline / disconnect × playground, published, eval, judge | exactly one line per model call |
| SC-002 | single-turn, correction-turn, failed-turn runs | Σ line `cost_usd` == `runs.cost_usd` exactly |
| SC-003 | request with `X-Correlation-Id: demo-123`; and with none | `demo-123` on run row + every line, **never** in outbound kwargs; outbound = line `provider_correlation_id` (32 hex); with none, one generated id on row, lines, outbound |
| SC-004 | seeded corpus (api key, `sk-ant-…`, AWS id, JWT, PEM, email, phone, card) with content OFF and ON | zero raw hits in captured lines |
| SC-005 | sink raises at each stage | run completes, row persisted, SSE unchanged, error counter +1 |
| SC-006 | stub with 1.2 s first-token delay; one deadline-cut run | histogram sample in the ≤1.5 s bucket; deadline counter +1 for that source |
| SC-007 | 200 stub runs with vs without sink | median overhead ≤ 5% (reported, not a CI gate) |
| SC-008 | default settings, any run | no `input_text`/`output_text` keys in any line |

## Commands

```bash
cd services
./.venv/bin/python -m pytest -q -m "not integration"            # pure + unit (incl. purity guard)
<integration env above> ./.venv/bin/python -m pytest -q          # full suite incl. 0012 + API scenarios
<integration env above> alembic upgrade head                     # applies 0012 (content_logging column)
```

Manual spot-check (stack up, dev auth): invoke an agent with header `X-Correlation-Id: demo-123`, then
`docker compose -f deploy/compose.yaml logs api | grep '"event":"model_call"' | grep demo-123` → one line per call;
`curl -s localhost:8000/metrics | grep -E 'ac_first_token_seconds|ac_deadline_exceeded_total'`.

## Docs to reconcile (D12)

`docs/backend/10-observability-monitoring.md` §1, `docs/backend/lld/10-observability-monitoring.lld.md` §3/§5,
`docs/scope.md` decision log (A2 interpretation), `docs/implementation-plan.md` M6 DoD row,
`docs/backend-integration-status.md` (new telemetry endpoint: BUILT, FE MOCK).

## Evidence

### SC-007 — call-log sink overhead (T037, 2026-11-14, local laptop, `invoke_result`, mocked gateway; reported, not a CI gate)

| Stub provider | No sink (median) | Sink, content OFF | Sink, content ON |
|---|---|---|---|
| zero-latency, 200 runs ×2 | 37 µs | 64 µs (+27 µs, ≈ +74%) | 201 µs (+165 µs) |
| ~50 ms (3 events × 16.7 ms), 60 runs | 54.77 ms | 55.28 ms (+0.9%) | 56.06 ms (+2.4%) |

Honest reading: the absolute cost is ~27 µs/call (metadata: 2 HMACs + JSON) and ~165 µs/call with content on
(redaction regexes over ~1 KB of text). Against a **zero-latency** stub the relative overhead is large because the
denominator is ~0 — SC-007 as literally worded ("≤ 5% vs stub provider") is **not met** for that stub. Against any
realistic provider latency (tens of ms to seconds) it is ≤ 2.4% (≤ 0.9% in the default content-OFF mode); the ~50 ms row
is within sleep-jitter noise. Recommend re-wording SC-007 to an absolute budget (e.g. ≤ 1 ms per call) if it is kept.

### T038 — live spot-check (2026-11-14, local compose stack, rebuilt `api` + `migrate`)

- Seeded a workspace + admin member + an **ollama**-profile agent (keeps the check fully local: the container has no
  Ollama, so the provider call fails with a connection error — no external/paid call).
- `POST /api/v1/agents/{id}/invoke` with `X-Correlation-Id: demo-123` → `200`, response header
  `x-correlation-id: demo-123`, SSE ends `done` with `status: provider_error`.
- `docker compose -f deploy/compose.yaml logs api | grep '"event":"model_call"' | grep demo-123` → **exactly one line**:
  `correlation_id: demo-123`, `provider_correlation_id: a49816f8…` (32-hex, ≠ caller id), `success: false`,
  `error_code: provider_error`, `cost_usd: "0"`, `cost_known: false`, `input_chars: 31`, keyed `input_fp`.
  `input_text` was present **because the dev profile forces content logging on** (`AC_TELEMETRY_LOG_CONTENT_IN_DEV`
  defaults true, D6) — prod default is off (covered by integration tests with the override disabled).
- `GET /metrics` → `ac_run_latency_seconds_count{source="playground"} 1.0`; first-token / deadline series not yet
  materialized (no token, no timeout in this run — prometheus shows labeled children only once observed); the
  workspace id appears nowhere in the exposition.
- **Found + fixed during the check:** the compose `migrate` service had no `AC_ENV`, so settings loaded as prod and
  the new fingerprint-key guard blocked migrations. Added `AC_ENV: dev` to `migrate` in `deploy/compose.yaml`.
  ⚠️ Any future prod migrate job must inject `AC_TELEMETRY_FINGERPRINT_KEY` (or the guard must be scoped to the API).
- Not exercised live: a successful provider call (needs a real provider/Ollama), opt-in toggle via UI (no FE seam).
