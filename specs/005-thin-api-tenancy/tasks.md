---
description: "Task list — Thin API + Tenancy Slice (M4 walking-skeleton slice)"
---

# Tasks: Thin API + Tenancy Slice (M4 — walking-skeleton slice)

**Input**: Design documents from `specs/005-thin-api-tenancy/`
**Prerequisites**: plan.md ✓, spec.md ✓, research.md ✓, data-model.md ✓, contracts/ ✓
**Tests**: REQUIRED — the constitution (P9) mandates genuine behavioral Red→Green→Refactor; every code task
names its RED test and focused command. No live/paid calls anywhere (SC-007) — runtime/evals mocked.

**Run (API unit+contract)**: `cd services && .venv/bin/python -m pytest tests/test_api_*.py -q`
**Run (tenancy integration, needs compose DB)**: `… -m integration`
**Full regression**: `cd services && .venv/bin/python -m pytest -q -m "not integration"`

## Format: `[ID] [P?] [Story] Description`

- **[P]**: different files, no dependency on an incomplete task. **[Story]**: US1–US4 (story tasks only).
- Source in `services/api/`, tests in `services/tests/`. No new DB migration (ETag = Postgres `xmin`, D7).

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: App skeleton + test harness (scaffolding, no tested behavior → may precede RED, P9).

- [X] T001 Create the FastAPI app skeleton: `services/api/main.py` with a `create_app()` that sets OpenAPI title/version (3.1), registers `app_error_handler` (from `api/errors.py`) for `AppError`, and includes the (initially empty) routers; create `services/api/routers/__init__.py` and `services/api/schemas/__init__.py`. No business logic yet.
- [X] T002 [P] Create the API test harness `services/tests/_api_helpers.py`: an httpx `ASGITransport` client factory against `api.main.app`; a `with_principal(...)` helper that overrides the `deps.session_principal` dependency; a mocked `runtime.providers.gateway.stream` (reuse `tests/_invoke_helpers` builders) and a mocked `evals.run_eval`; and DB seed helpers (`seed_agent`, `seed_version`) for the integration tenancy tests.

**Checkpoint**: app imports, OpenAPI emits, test client + overrides ready.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The shared request machinery every route needs — identity, workspace authorization, the
tenant-scoped DB session, and the idempotency/rate-limit seams. **BLOCKS all user stories.**

- [X] T003 Implement `services/api/deps.py`: (a) `session_principal` — a dev-stub dependency resolving a `Principal` (`subject`, `workspace_claims: dict[ws→role]`); no principal on a protected route → `AuthRequired` (401). (b) `resolve_workspace` — read `X-Workspace-Id` (fallback `?workspace=`), authorize it against `principal.workspace_claims` → `AccessDenied` (403) if unheld. (c) `tenant_db` — an async dependency yielding `db.tenant_session(workspace_id)` (ac_app + GUC + RLS). (d) `rate_limit` seam that can raise `RateLimited` (429) — no-op allow this slice (F5). (e) an in-process idempotency guard keyed by `(workspace_id, Idempotency-Key)` raising `IdempotencyInProgress` (409) for a concurrent in-flight key (D8).
- [X] T004 Assemble `services/api/main.py`: wire the error handler, register all routers, add a `GET /healthz`; confirm `from api.main import app` imports cleanly and `app.openapi()` returns a 3.1 doc. Confirm `test_runtime_purity.py` + `test_evals_purity.py` still pass after importing `api` (P6).

**Checkpoint**: a request can authenticate (stub), select+authorize a workspace, and open an RLS-scoped session.

---

## Phase 3: User Story 1 — SSE invoke through the shared runtime (Priority: P1) 🎯 MVP

**Goal**: `POST /api/v1/agents/:id/invoke` streams SSE by calling the same `runtime.invoke` (P6).
**Independent Test**: with a mocked gateway, POST invoke → assert `text/event-stream` and ordered frames; a
runtime error → typed `error` frame + `done`, not a 500.

- [X] T005 [P] [US1] **RED** — `services/tests/test_api_invoke.py`: POST invoke (principal overridden, gateway mocked) → `content-type: text/event-stream`, frames arrive ordered `run_start`→`text_delta`→`usage`→`done` with the typed payloads (FR-001/003); a mocked ERROR event → an `error` frame (code+message, no stack) then `done`, never a mid-stream 500 (FR-004); inputs violating the version `io_contract` → typed rejection, gateway NOT called (FR-005); exactly one run record emitted (FR-006); an unknown/cross-tenant version id → `404` (US2). Confirm it FAILS. Focused: `pytest tests/test_api_invoke.py`.
- [X] T006 [US1] Implement `services/api/sse.py`: `sse_encode(event: NormalizedStreamEvent) -> bytes` mapping `EventType.{TEXT_DELTA,REASONING_DELTA,TOOL_CALL_START/_DELTA/_END,USAGE,ERROR}` + synthetic `run_start`/`done` to the frame table in contracts/api-surface.md (`event:` + one JSON `data:` line); the `error` frame carries only `{code, message}` (api_key-scrubbed, no stack).
- [X] T007 [US1] Implement `services/api/schemas/invoke.py`: `InvokeIn {inputs: dict, version_id?: str}` and the typed SSE frame models (run_start/text_delta/reasoning_delta/tool_call_*/usage/error/done) for the OpenAPI doc (F9).
- [X] T008 [US1] Implement `services/api/routers/invoke.py` — `POST /api/v1/agents/{id}/invoke`: under `tenant_db`, resolve the agent's version (`version_id` or `active_version_id`) and `to_snapshot()` (tenant-scoped → `404` if absent/cross-tenant); build a `RunContext` (`api_key` from settings, `source="playground"`, a `record_run` sink); call `runtime.invoke(...)` and return a `StreamingResponse(media_type="text/event-stream")` encoding each event via `sse.py`; on client disconnect cancel the runtime task. Make T005 green. Focused: `pytest tests/test_api_invoke.py`.

**Checkpoint**: the walking-skeleton invoke works over the wire (mocked) — the headline P6 proof.

---

## Phase 4: User Story 2 — Fail-closed tenant isolation (Priority: P1)

**Goal**: prove the tenancy guarantees the foundational `deps` machinery provides.
**Independent Test**: caller in ws A → ws B resource = 404; unset/bad scope = zero rows; data path uses
`ac_app`; unauthorized workspace selection = 403.

- [X] T009 [P] [US2] **RED** — `services/tests/test_api_tenancy.py`: (unit) selecting a workspace absent from the principal's claims → `403 access.denied` (FR-007); no principal → `401 auth.required` (FR-019). (integration, `pytest.mark.integration`, skip when DB down — mirror `test_session_tenancy.py`) seed two workspaces + an agent in A; as a caller scoped to A, GET B's agent → `404` (FR-010/SC-002); an unset/malformed scope → zero rows, not 500 (FR-009); assert the request session connects as `ac_app`, not the owner role (FR-008/SC-003). Confirm it FAILS. Focused: `pytest tests/test_api_tenancy.py`.
- [X] T010 [US2] Harden `services/api/deps.py` (and any route wiring) so the T009 assertions pass — fail-closed workspace resolution, `403` on unheld claim, `401` on no principal, `ac_app` on the data path. (Most of the mechanism is in T003; this closes any gaps the negative tests surface.) Make T009 green. Focused: `pytest tests/test_api_tenancy.py`.

**Checkpoint**: cross-tenant access is impossible through the API (negative tests prove it).

---

## Phase 5: User Story 3 — Agent CRUD + versioning through typed contracts (Priority: P2)

**Goal**: create/get/list/PATCH agents + POST version, with idempotency + If-Match.
**Independent Test**: create (scoped, typed) → get/list (paginated) → POST version (immutable) → idempotent
retry = 409 → stale If-Match = 409.

- [X] T011 [P] [US3] **RED** — `services/tests/test_api_agents.py`: create returns the typed `AgentOut` scoped to the request workspace (FR-012); get/list are cursor-paginated `Page[AgentOut]` (FR-013); a concurrent create reusing an in-flight `Idempotency-Key` → `409 idempotency.in_progress`, no duplicate (FR-014); `POST versions` yields an immutable `VersionOut` with `snapshot_hash` (FR-012); PATCH with a stale `If-Match` (xmin) → `409 conflict.stale`, row unchanged (FR-015); a cross-tenant GET → `404` (US2). Confirm it FAILS. Focused: `pytest tests/test_api_agents.py`.
- [X] T012 [P] [US3] Implement `services/api/schemas/agents.py`: `AgentCreate {name, provider, model, system_prompt, io_contract}` (name 1–80 chars per ORM `String(80)`; `io_contract` MUST contain an `input_schema` object), `AgentOut {id,name,status,active_version_id,workspace_id,created_at,versions[]}` (+ ETag surfaced via header), `AgentPatch {name?,status?,active_version_id?}` (status ∈ `draft|published|archived|eval_failing`), `VersionOut {id,agent_id,label,snapshot_hash,prompt_hash,publishable,created_at}`.
- [X] T013 [US3] Implement `services/api/routers/agents.py` — create (workspace from scope, never body; honor `Idempotency-Key`), get (ETag=`xmin` response header), list (cursor pagination via `Page[T]` + `validate_sort`), PATCH (require `If-Match`; `UPDATE … WHERE id=:id AND xmin=:etag`; zero rows → `ConflictStale`), `POST versions` (immutable `AgentVersion`, `compute_snapshot_hash`). Make T011 green. Focused: `pytest tests/test_api_agents.py`.

**Checkpoint**: the front half of the skeleton (create→version) works with the typed contract + concurrency.

---

## Phase 6: User Story 4 — Eval-run + advisory publish (Priority: P3)

**Goal**: run the M3 engine over HTTP (inline) and publish advisory (never gated).
**Independent Test**: POST eval-run (inline suite, mocked) → typed verdict; publish without a PASS → succeeds +
reports verdict, no 409.

- [X] T014 [P] [US4] **RED** — `services/tests/test_api_evals_publish.py`: `POST eval-runs` with an inline suite (cases + baseline, runtime mocked) → a typed `EvalRunOut` mapping `evals.EvalRunResult`; no live call (FR-017/SC-007); `POST publish` for a version with no passing eval → `200` + `PublishOut.advisory_verdict`, and `publish.eval_gate` is NOT raised (FR-018/SC-006/D6). Confirm it FAILS. Focused: `pytest tests/test_api_evals_publish.py`.
- [X] T015 [P] [US4] Implement `services/api/schemas/evals.py`: `EvalRunIn {version_id, cases[], baseline, policy?}` (mirroring the M3 `EvalCase`/`BaselineAggregate`/`EvalPolicy`; policy default `{min_cases:10, quality_threshold:90, efficiency_budget_pct:10}`), `EvalRunOut` (maps `EvalRunResult`: verdict/reason/quality/quality_delta/cost_delta/latency_delta/cases/dimensions[]/failing_cases[]/pairwise_rate), `PublishOut {agent_id,version_id,status,advisory_verdict,advisory_note}`.
- [X] T016 [US4] Implement `services/api/routers/evals.py` — `POST /agents/:id/eval-runs` (build M3 types from the body, call `evals.run_eval` inline with a mocked-boundary `RunContext`, return `EvalRunOut`; persist nothing) and `POST /agents/:id/publish` (set status=published, return `PublishOut` with the advisory verdict; MUST NOT raise `EvalGateError`/`LowConfidenceError`). Make T014 green. Focused: `pytest tests/test_api_evals_publish.py`.

**Checkpoint**: eval→publish→invoke end-to-end (mocked) through the real engines.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T017 [P] `services/tests/test_api_contracts.py`: a 4xx returns the `{"error":{code,message,detail?}}` envelope; `GET /openapi.json` is OpenAPI 3.1 and includes the typed models + SSE frame schemas (SC-004).
- [X] T018 [P] Confirm `services/tests/test_runtime_purity.py` and `services/tests/test_evals_purity.py` stay green after importing `api` — no web-framework leak into the pure libraries (SC-008/P6).
- [X] T019 Full regression: `cd services && .venv/bin/python -m pytest -q -m "not integration"` (no regressions on the prior 259) + the tenancy integration tests when the compose DB is up.
- [X] T020 **Docs reconciliation (standing instruction — update docs at DoD)**: add a built-state banner to `docs/backend/08-api-surface.md` (built: console-session agents CRUD + versions + SSE invoke + inline eval-run + advisory publish, RLS-GUC tenancy; deferred: token plane, real OIDC/Vault/RBAC, async worker, real publish gate, Redis idempotency, 253 seams) and update the `docs/implementation-plan.md` M4 row to the M4-slice built state.

---

## Dependencies & Execution Order

- **Setup (T001–T002)** → first. **Foundational (T003–T004)** → after Setup; **BLOCKS all stories** (every route needs `deps`).
- **US1 (T005–T008)** → after Foundational. **US2 (T009–T010)** → after Foundational (validates/hardens the tenancy machinery). US1 and US2 both P1; US2's negative tests also cover US1's cross-tenant 404.
- **US3 (T011–T013)** → after Foundational. **US4 (T014–T016)** → after Foundational; US4 eval-run reuses the M3 engine (004), publish reuses the agent row (US3's persistence) — run US3 before US4.
- **Polish (T017–T020)** → after the stories.

### Within each story
- The RED test task is authored and **confirmed failing** before its GREEN implementation task (P9); show the RED + failure to the owner before implementation code.

### Parallel opportunities
- T005 / T009 / T011 / T014 (the four story RED tests) are [P] — different files.
- T012 (schemas/agents) ∥ T015 (schemas/evals) — different files.
- T017 ∥ T018 (polish checks) — different files.

---

## Parallel Example: the story RED tests

```bash
# Author the story RED tests together (different files), then confirm each FAILS:
Task T005: tests/test_api_invoke.py          (US1)
Task T009: tests/test_api_tenancy.py         (US2)
Task T011: tests/test_api_agents.py          (US3)
Task T014: tests/test_api_evals_publish.py   (US4)
cd services && .venv/bin/python -m pytest tests/test_api_invoke.py tests/test_api_tenancy.py tests/test_api_agents.py tests/test_api_evals_publish.py -q   # expect RED
```

---

## Implementation Strategy

### MVP first (US1 + the tenancy foundation)
Setup → Foundational → US1 (SSE invoke) + US2 (tenancy). **Stop & validate**: an agent version can be invoked
over the wire through the real `runtime.invoke`, tenant-isolated — the walking-skeleton's headline.

### Incremental delivery
US1 (invoke) + US2 (tenancy) → US3 (CRUD+versioning) → US4 (eval+advisory publish) → Polish. Each story is
independently testable; the create→version→eval→publish→invoke loop is complete after US4.

---

## Requirement → task coverage (self-check)

| Requirement | Task(s) |
|---|---|
| FR-001/002/003 (SSE invoke via shared runtime) | T005–T008 |
| FR-004 (typed error frame) | T005, T006, T008 |
| FR-005 (input reject, zero spend) | T005, T008 |
| FR-006 (one honest run record) | T005, T008 |
| FR-007 (workspace authz) | T003, T009, T010 |
| FR-008 (ac_app session) / SC-003 | T003, T009, T010 |
| FR-009 (fail-closed scope) | T003, T009 |
| FR-010 (cross-tenant 404) / SC-002 | T009, T010 (+ T005/T011 cross-tenant) |
| FR-011 (error envelope) | T001, T017 |
| FR-012 (CRUD + versioning) | T011–T013 |
| FR-013 (pagination) | T011, T013 |
| FR-014 (idempotency) | T003, T011, T013 |
| FR-015 (If-Match/conflict.stale) | T011, T012, T013 |
| FR-016 (rate_limit seam) | T003 |
| FR-017 (eval-run inline) | T014–T016 |
| FR-018 (advisory publish) / SC-006 | T014, T016 |
| FR-019 (401 no principal) | T003, T009 |
| FR-020 / SC-007 (no live calls) | T002, all RED tests |
| SC-001 (invoke over wire) | T005, T008 |
| SC-004 (OpenAPI/contract) | T017 |
| SC-008 / P6 (purity) | T004, T018 |
| Docs at DoD (standing instruction) | T020 |

## Notes
- [P] = different files, no deps. [Story] maps a task to its user story.
- Verify each RED test fails before implementing; never weaken a test to pass (P9).
- No live/paid provider calls — runtime/evals mocked throughout (SC-007); first live call → Phase 5.
- No new DB migration (ETag = Postgres `xmin`); integration tenancy tests need the compose DB.
- Commit after each task or logical group (commit only when the owner asks).

---

## Phase 8: Convergence

> Appended by `/speckit-converge` (2026-11-05). Implementation satisfies all FRs/SCs, plan decisions (D1–D11),
> and constitution principles (P1–P9) with no CRITICAL/HIGH findings and no constitution violations. Three
> actionable partials remain (below). One further LOW item is **noted but not appended** — it is
> dependency-blocked and documented: the invoke run record goes to an in-memory sink because there is no `runs`
> table yet (FR-006/P8 "produce" is met; durable persistence awaits a `runs` migration in M4-remainder).

- [X] T021 Wire `Depends(rate_limit)` into the mutating API routes (create/patch/version/invoke/eval-run/publish) so the F5 seam is actually on the request path and can return `429 rate.limited` — currently `deps.rate_limit` is defined but attached to no route, per FR-016 / plan D10 (partial)
- [X] T022 Freeze the real `profile_snapshot` at agent/version create in services/api/routers/agents.py (load the provider/model profile JSON, mirroring evals/judge.py's loader) so a *created* agent is invocable end-to-end — currently create stores `profile_snapshot={}` and `_profile_from_json({})` raises `KeyError 'provider'`, breaking the create→invoke loop (invoke is proven only on seeded versions), per US1 / FR-012 (partial)
- [X] T023 [P] Add an endpoint-level test in services/tests/test_api_agents.py exercising create with an `Idempotency-Key` (the in-flight guard is currently only unit-tested at the dependency level), per FR-014 / SC-005 (partial)
