# Quickstart — validating the Thin API + Tenancy Slice (M4 slice)

How to prove this feature works. Unit/contract tests need no DB and no credentials; the tenancy negative
tests need the compose Postgres. No live/paid calls anywhere.

## Prerequisites

- `services/.venv` with the `.[api]` + `.[test]` extras (all already installed — FastAPI, httpx, SQLAlchemy,
  asyncpg, pydantic, litellm). No new dependencies.
- Features 003 (`runtime`) and 004 (`evals`) present — the API wraps `runtime.invoke` and `evals.run_eval`.
- For the **integration** tenancy tests only: the compose stack up with migrations `0001`–`0004` applied
  (`deploy/compose.yaml`; RLS `0002`, `ac_app` role `0003`). Unit/contract tests skip the DB entirely.

## Run the API test suite

```bash
cd services
.venv/bin/python -m pytest tests/test_api_*.py -q            # unit + contract (no DB)
.venv/bin/python -m pytest tests/test_api_*.py -q -m integration   # negative cross-tenant (needs compose DB)
.venv/bin/python -m pytest -q -m "not integration"          # whole unit suite — no regressions
```

Optional — run the app locally and read the contract:

```bash
cd services && .venv/bin/uvicorn api.main:app --port 8000    # then GET http://localhost:8000/openapi.json
```

## Validation scenarios (map to spec Success Criteria)

| # | Scenario | How to observe | Proves |
|---|---|---|---|
| 1 | **SSE invoke through the shared runtime** | `test_api_invoke.py`: POST invoke with a mocked gateway; assert `content-type: text/event-stream` and ordered frames `run_start`→`text_delta`→`usage`→`done`. | SC-001, FR-001/002/003 |
| 2 | **Runtime error → typed error frame** | mocked gateway yields an ERROR; assert an `error` frame (code+msg, no stack) then `done`, not a 500. | FR-004, P8 |
| 3 | **Negative cross-tenant** | `test_api_tenancy.py` (integration): caller in ws A GETs ws B's agent → `404`; unset/bad scope → zero rows. | SC-002, FR-010/009 |
| 4 | **ac_app role on the data path** | integration: assert the request session's role is `ac_app` (RLS applies), never the owner role. | SC-003, FR-008 |
| 5 | **Idempotent create** | `test_api_agents.py`: concurrent retry with the same `Idempotency-Key` → `409 idempotency.in_progress`, no duplicate. | SC-005, FR-014 |
| 6 | **Optimistic concurrency** | PATCH with a stale `If-Match` (xmin) → `409 conflict.stale`, row unchanged. | SC-005, FR-015 |
| 7 | **Advisory publish** | `test_api_evals_publish.py`: publish with no passing eval → succeeds + reports the verdict; `publish.eval_gate` never raised. | SC-006, FR-018 |
| 8 | **Eval over HTTP** | POST eval-run (inline suite, mocked runtime) → typed `EvalRunOut` verdict; no live call. | FR-017, SC-007 |
| 9 | **Typed error envelope + OpenAPI** | `test_api_contracts.py`: a 4xx returns `{"error":{code,message}}`; `/openapi.json` is 3.1 and reflects the models. | FR-011, SC-004 |
| 10 | **Purity preserved** | existing `test_runtime_purity.py` / `test_evals_purity.py` stay green after importing `api`. | SC-008, P6 |

## What this slice does NOT prove (honest deferrals)

- No caller **bearer-token** plane / token mint; invoke is console-session only.
- No real **OIDC/IdP**, no **Vault**, no full **RBAC**/access-policy, no **org-admin** plane.
- No **async worker** / `GET /jobs/:id`; eval runs inline.
- No **real publish gate** (advisory until κ calibration); no Redis idempotency replay; no real rate-limit
  thresholds; no ClickHouse/Langfuse.
- **No live/paid invoke** — deferred to Phase 5 under the paid-call protocol.
- The **FE→generated-client wiring + CI drift-fail** is the M12/Phase-5 contract-proof step, not here.

See `plan.md` *Constitution Check* and `spec.md` *Out of Scope* for the full deferral list.
