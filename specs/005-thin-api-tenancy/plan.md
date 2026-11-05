# Implementation Plan: Thin API + Tenancy Slice (M4 — walking-skeleton slice)

**Branch**: `005-thin-api-tenancy` | **Date**: 2026-11-05 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/005-thin-api-tenancy/spec.md`

## Summary

Build the **first HTTP caller layer** — a thin FastAPI app (`services/api/`) that wraps the already-built pure
cores without reimplementing them: the SSE invoke endpoint streams by calling the same `runtime.invoke` (M2a);
the eval-run endpoint runs `evals.run_eval` (M3) inline; agents create/get/list/PATCH + POST-version persist
through the SQLAlchemy ORM. The load-bearing piece is **fail-closed tenancy** (P5): every request opens its DB
session via the existing `tenant_session` (connects as the non-privileged `ac_app` role + sets the
`app.workspace_id` GUC) so the migration-`0002` RLS policies filter every tenant table — with **negative
cross-tenant tests**. Contracts are typed Pydantic mirroring `frontend/src/lib/data.ts` + the existing error
envelope (`api/errors.py`) and cursor pagination (`api/pagination.py`); OpenAPI 3.1 is the source of truth.
Creates honor an idempotency key; PATCH uses `If-Match`/ETag (`conflict.stale`); a `rate_limit()` seam returns
`429`. Publish is **advisory** (returns the verdict, never raises `publish.eval_gate` — owner decision #3).
`services/runtime` and `services/evals` stay pure (only `api` imports the web framework). Fully mocked at the
provider boundary — no live/paid calls (first live call → Phase 5).

## Technical Context

**Language/Version**: Python 3.12.13 (existing `services/.venv`).

**Primary Dependencies**: FastAPI + Starlette (`StreamingResponse` for SSE), SQLAlchemy 2.0 async (asyncpg),
Pydantic v2 + `pydantic-settings`, httpx (ASGITransport in-process test client). **All already installed under
the `.[api]`/`.[test]` extras — no new dependencies.** Wraps `services/runtime` (invoke/invoke_result) and
`services/evals` (run_eval); reuses `api/{errors,pagination,db,tenancy,models,settings}.py`.

**Storage**: Postgres via the compose stack, migrations `0001`–`0004` (RLS policies `0002`, `ac_app` role
`0003`). The request path uses the existing `tenant_session(workspace_id)` factory (ac_app + GUC). No new
migration in this slice (agents/agent_versions already exist); eval-run and advisory-publish persist nothing
new (eval/baseline tables deferred).

**Testing**: pytest + pytest-asyncio (`asyncio_mode=auto`). **Unit/contract tests** drive the ASGI app via
httpx `ASGITransport` with FastAPI dependency overrides — the auth principal is a test override, and
`runtime.providers.gateway.stream` / the evals path are mocked (no DB, no live calls). **Integration tests**
(`pytest.mark.integration`, skip when the DB is unreachable — mirrors `test_session_tenancy.py`) exercise the
real `tenant_session` + RLS for the negative cross-tenant proof. No live/paid calls anywhere (SC-007).

**Target Platform**: Linux server — the `api` deployable (FastAPI/uvicorn).

**Project Type**: Web service (thin API) over pure libraries.

**Performance Goals**: Not latency-targeted in this slice. The one hard behavior: SSE must stream
incrementally (`text/event-stream`, no response buffering, client-disconnect cancels the runtime task).

**Constraints**: `services/runtime` + `services/evals` MUST remain import-pure (the two purity tests stay
green — only `api` imports FastAPI). Tenancy fail-closed (unset/invalid scope → zero rows). No secrets
(api_key, DB creds) in logs, SSE frames, or error bodies (P5/P8). Provider credential for invoke comes from
settings/env in this slice (Vault deferred); invoke is mocked so no key is actually used.

**Scale/Scope**: Single corporate pilot tenant; agents/versions/eval/publish/invoke only (not the 253 seams).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | How this feature complies | Status |
|---|---|---|
| **P1 Verifiable outcome / honesty** | Delivers the first usable HTTP surface for the real runtime; mocked invoke is labeled; completion report will name commands + unverified (live) items. | ✅ Pass |
| **P2 Govern scope / phases** | M4 walking-skeleton slice under the 2026-11-03 advisory-gate authorization; non-goals explicit in *Out of Scope*. | ✅ Pass |
| **P3 Deterministic controls** | Identity, workspace authorization, input contracts, idempotency, optimistic concurrency, rate-limit — all enforced in code, fail-closed; model output is not enforcement. | ✅ Pass |
| **P4 Evidence about agent behavior** | Eval runs through the shared runtime; publish is **advisory** — the uncalibrated judge gates nothing (owner decision #3). | ✅ Pass |
| **P5 Tenant boundaries** | **The load-bearing gate here.** Per-request `ac_app` session + `app.workspace_id` GUC → RLS on every tenant table; workspace authorized against the caller claim; **negative cross-tenant tests required**; owner role never serves requests. | ✅ Pass |
| **P6 Reproducible / same runtime** | Invoke wraps the SAME `runtime.invoke`; eval wraps `evals.run_eval`; the API reimplements no pipeline logic. Runtime + evals purity tests stay green. | ✅ Pass |
| **P7 Provider/cost honesty** | The `usage` SSE frame carries cost from the runtime `RunRecord` (our profiles), never recomputed/trusted from transport. | ✅ Pass |
| **P8 Observable / honest failure** | One honest run record per invoke; failures surface as a typed `error` frame + `done`, or the typed error envelope — never a raw 500 or a leaked stack/secret. | ✅ Pass |
| **P9 Specify/test-first/evidence** | Full SDD chain; strict Red→Green→Refactor; RED shown before implementation. | ✅ Pass |

**Minimum Verification Checks applied** (constitution table):
- *API / integration*: typed request/response + error contracts; **SSE contract**; OpenAPI emitted (the FE
  type/build drift check is a later M12 step — noted, not run here).
- *Database or tenant behavior*: uses existing migrations; **workspace isolation + negative cross-tenant**
  integration tests (the headline evidence).
- *Provider/runtime behavior*: reuses M1/M2 adapter/stream/cost evidence; invoke mocked at the gateway.
- *Security/secrets*: no secret in logs/SSE/errors; generic `500 internal` never leaks a stack; secret-scan
  stays green.

**Result: PASS — no violations.** Complexity Tracking empty. *Note (honest):* this is the first feature with
a request-time DB + web surface, so its integration tests depend on the compose Postgres (as M0 established).

## Project Structure

### Documentation (this feature)

```text
specs/005-thin-api-tenancy/
├── plan.md              # This file
├── research.md          # Phase 0 — decisions (D1–D10)
├── data-model.md        # Phase 1 — API projections + entities
├── quickstart.md        # Phase 1 — how to validate
├── contracts/
│   └── api-surface.md   # Phase 1 — endpoints, SSE frames, error codes, headers
├── checklists/requirements.md
└── tasks.md             # Phase 2 — /speckit-tasks (NOT here)
```

### Source Code (repository root)

```text
services/api/
├── main.py              # NEW — FastAPI app; wire app_error_handler; register routers; OpenAPI 3.1
├── deps.py              # NEW — session_principal (auth stub + per-ws role claim), tenant DB-session dep,
│                        #        workspace resolution+authorization, rate_limit() seam, idempotency guard
├── sse.py               # NEW — encode runtime NormalizedStreamEvent → typed SSE frames (F9)
├── schemas/             # NEW — Pydantic contracts (OpenAPI source of truth; mirror data.ts)
│   ├── __init__.py
│   ├── agents.py        #   AgentOut, AgentCreate, AgentPatch, VersionOut (+ ETag)
│   ├── invoke.py        #   InvokeIn + SSE frame models (run_start/text_delta/.../usage/error/done)
│   └── evals.py         #   EvalRunIn (inline suite) + EvalRunOut (maps evals.EvalRunResult), PublishOut
├── routers/             # NEW
│   ├── __init__.py
│   ├── agents.py        #   POST/GET/LIST/PATCH agents + POST versions
│   ├── invoke.py        #   POST /agents/:id/invoke (SSE, console-session plane)
│   └── evals.py         #   POST eval-runs (inline) + POST publish (advisory)
├── errors.py            # EXISTS — app_error_handler wired in main
├── pagination.py        # EXISTS — Page[T] + cursor codec
├── db.py · tenancy.py · models.py · settings.py   # EXIST — reused unchanged
services/tests/
├── _api_helpers.py              # NEW — ASGITransport client, auth override, mock runtime/evals, seeders
├── test_api_invoke.py           # SSE invoke via mocked runtime (US1)
├── test_api_tenancy.py          # negative cross-tenant (integration, real RLS) + ac_app assertion (US2)
├── test_api_agents.py           # create/get/list/PATCH + version + idempotency + If-Match (US3)
├── test_api_evals_publish.py    # eval-run inline + advisory publish (US4)
├── test_api_contracts.py        # error envelope shape + OpenAPI + SSE frame types
└── test_runtime_purity.py / test_evals_purity.py  # EXISTING — must stay green (P6)
```

**Structure Decision**: A net-new `services/api/` web layer (`main`, `deps`, `sse`, `schemas/`, `routers/`)
wrapping the pure libraries, reusing the already-built `errors`/`pagination`/`db`/`tenancy`/`models`. **No
changes to `services/runtime` or `services/evals`** — they are consumed through their existing public
contracts, keeping the P6 purity invariant intact.

## Complexity Tracking

*No constitutional violations — section intentionally empty.*
