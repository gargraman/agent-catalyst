# Quickstart / Validation: Runtime Invoke Spine (M2a)

**Feature**: `003-runtime-invoke-spine` · **Date**: 2026-11-04

How to prove M2a works end-to-end **without any live/paid call**. All scenarios substitute a canned
gateway/transport (same discipline as M1). Types & contract: [contracts/runtime-invoke.md](./contracts/runtime-invoke.md).

## Prerequisites

- Project venv: `services/.venv` (Python 3.12.13). No new dependencies.
- No database or Docker needed — M2a is a pure library; the Run is captured by an in-memory test sink.
- Run commands from `services/`:

```bash
cd services
.venv/bin/python -m pytest -q -m "not integration"        # full unit suite incl. M2a
.venv/bin/python -m pytest -q tests/test_runtime_purity.py # P6 — runtime/ imports no web framework
```

## How the gateway is mocked

Monkeypatch `runtime.providers.transport.stream_chunks` (the single litellm seam) with an async generator
of canned LiteLLM-shaped chunks — OR monkeypatch `runtime.providers.gateway.stream` directly with a canned
`NormalizedStreamEvent` sequence. No network, no API key use (SC-008).

## Validation scenarios (map to acceptance criteria)

| # | Scenario | Setup | Expected | Verifies |
|---|---|---|---|---|
| 1 | Happy path | valid inputs, canned stream: content → USAGE → STOP | event stream re-surfaced; one `RunRecord` with `outcome=success`, identity/usage/cost/latency/correlation populated; cost == gateway's cost | US1, US2, SC-001, SC-006 |
| 2 | Frozen-snapshot read | invoke a snapshot, then mutate an unrelated "live" structure | repeat invocation unchanged — snapshot drives behavior | US1 / FR-002 |
| 3 | No caller branches | inspect `invoke` | no `if source == …` branching anywhere | US1 / FR-003 |
| 4 | Honest failure | canned gateway `ERROR` | one `RunRecord`, `outcome=provider_error`, not success, not dropped | US2 / FR-013 / P8 |
| 5 | Degraded stream | canned stream ends with gateway degraded `ERROR` (no USAGE) | `outcome=degraded`, usage/cost absent/best-effort | US2 edge / P8 |
| 6 | Deadline | `ctx.deadline_s` shorter than a slow canned stream | invocation stops at ~deadline; `outcome=timeout`; partial usage/cost recorded | US3 / SC-003 |
| 7 | Invalid input | inputs violating `io_contract.input_schema` | `outcome=invalid_input`; **gateway never called** (assert zero interaction) | US5 / FR-004 / SC-005 |
| 8 | Context overflow | inputs whose counted size > `profile.limits.context_tokens` | `outcome=context_overflow`; **gateway never called** | US5 / FR-005 / SC-005 |
| 9 | Full event fidelity | canned stream with content + reasoning + tool-call(start/delta/end) + STOP | every event class observed by the caller; `stop_reason` captured | US4 / FR-007-008 / SC-004 |
| 10 | Tool-call-only | canned stream whose only output is a tool-call (no content) | tool-call surfaced; `outcome=success` (non-empty), **not** empty/degraded | US4 / C4 / SC-004 |
| 11 | Correlation minted | `ctx` with no `correlation_id` | `RunRecord.correlation_id` non-empty | SC-007 |
| 12 | Token guard safety | `count_tokens` over representative inputs | monotonic in input size; over-estimates (never under-counts) vs a reference | D4 |

## Definition of done (M2a)

- All scenarios above green; the new `test_invoke*` + `test_tokens` suites pass.
- `test_runtime_purity.py` green (P6 intact — no web-framework import crept into `runtime/`).
- Full unit batch green; no regression in M0/M1 suites.
- No live/paid call in any test (SC-008).
- Each behavior-changing task shipped via a genuine behavioral RED → GREEN (P9).
