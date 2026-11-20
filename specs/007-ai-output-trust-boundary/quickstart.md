# Quickstart — Verify the AI-Output Trust Boundary (M2b)

How to run and validate feature 007. All tests mock the gateway — **no live/paid calls**.

## Prerequisites

- `cd services` ; virtualenv at `services/.venv` with test deps.
- Postgres via `deploy/compose.yaml` running (host port 5433) only for the US3 HTTP/persistence test and the full regression; the pure-runtime unit tests (US1/US2/Foundational) need no DB.

## Run the feature's tests

```bash
cd services
# Foundational + US1 + US2 (pure runtime, no DB):
.venv/bin/python -m pytest tests/test_invoke_output_validation.py tests/test_invoke_tool_enforcement.py \
  tests/test_invoke_corrections.py tests/test_invoke_passthrough_unchanged.py -q

# US3 (HTTP typed frames + persistence) — needs compose Postgres:
.venv/bin/python -m pytest tests/test_api_invoke_trust_boundary.py -q

# Purity must stay green (runtime has no web/DB imports):
.venv/bin/python -m pytest tests/test_runtime_purity.py -q

# Full regression:
.venv/bin/python -m pytest -q
```

## What each check proves (acceptance → verification)

| Story | Check | Expected |
|---|---|---|
| US1 | json output valid first try | passes through; outcome `success`; `correction_turns==0` (SC-003 lower bound) |
| US1 | malformed JSON then valid on repair | outcome `success`; `correction_turns==1`; usage covers **both** turns (AC2, SC-005) |
| US1 | invalid on both turns | outcome `output_invalid`; no structured output surfaced as valid; exactly one Run (AC3, SC-001) |
| US1 | text-mode agent | unchanged — no repair turn, live streaming (AC4) |
| US2 | valid tool call | surfaced unchanged; **not executed** (AC1, FR-009) |
| US2 | unbound tool name | one feed-back re-ask listing available tools; corrected → proceeds (AC2) |
| US2 | bad args on both turns | outcome `tool_call_rejected`; rejected call never surfaced (AC3, SC-002) |
| US1+US2 | adversarial always-invalid model | total correction turns ≤ `ctx.max_corrections` (default 2); fails closed (SC-004) |
| US1+US2 | deadline during correction | outcome `timeout`; one Run (edge case) |
| US3 | each new outcome over HTTP | SSE ends in typed `error` + `done`(status=outcome); **no 500**; Run persisted with `correction_turns` (SC-006) |
| Polish | `invoke` vs `invoke_result` | identical outcome + Run for equivalent inputs (SC-008) |
| Polish | purity | `test_runtime_purity` green (P6) |

## TDD note (P9)

Each story's behavioral RED is written and shown **before** implementation: e.g. for US1, assert that a malformed-JSON turn (gateway mocked to return `"{not json"`) yields outcome `output_invalid` — this fails against today's pass-through pipeline (which surfaces the raw text as `success`), demonstrating a genuine behavioral gap, not an import/syntax error.
