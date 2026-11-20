# Research — Thin API + Tenancy Slice (M4 slice)

Phase 0 decisions. The spec-level ambiguities were resolved in `/speckit-clarify` (Session 2026-11-05:
console-session invoke, inline eval, create/get/list/PATCH/version). The decisions below are the plan-level
choices, grounded in the existing code (`api/{errors,pagination,db,tenancy,models}.py`, `runtime`, `evals`)
and `docs/backend/08-api-surface.md`.

---

## D1 — The API wraps `runtime.invoke`; it reimplements no pipeline logic (P6)

**Decision**: The invoke route calls the streaming `runtime.invoke(snapshot, inputs, ctx)` async generator and
re-encodes each yielded `NormalizedStreamEvent` as an SSE frame via a small `api/sse.py`, returned through
Starlette `StreamingResponse(media_type="text/event-stream")`. Input validation, cost, deadline, and the run
record all stay in the runtime.

**Rationale**: The load-bearing invariant (00 §1 / P6) — one `runtime.invoke` shared by playground, eval, and
published callers. The purity test stays green because only `api` imports FastAPI.

**Alternatives**: a non-streaming collect-then-return (`invoke_result`) — rejected for the invoke path (the FE
expects a live SSE stream); it remains the right call for the eval/judge path inside `evals`.

---

## D2 — Per-request tenant session via the existing `tenant_session` (ac_app + GUC)

**Decision**: A FastAPI dependency yields an `AsyncSession` from the existing `db.tenant_session(workspace_id)`
(connects as `ac_app`, `SET LOCAL app.workspace_id`), so RLS (`0002`/`0004`) filters every tenant table. The
workspace id is read from the `X-Workspace-Id` header (falling back to `?workspace=`) and **authorized against
the caller's claim** before the session is opened.

**Rationale**: P5 — defense in depth (app-role + RLS), fail-closed by construction (`tenancy._normalize_ws`
turns an unset/invalid scope into zero rows). Reuses proven M0 code; no new tenancy mechanism.

**Alternatives**: a single app-wide session with manual WHERE filters — rejected (RLS is the enforcement, not
hand-written filters a dev can forget, P3).

---

## D3 — Auth is a minimal session-principal dependency (test-overridable); real OIDC deferred

**Decision**: `deps.session_principal` resolves a `Principal` (identity + per-workspace role claims). In this
slice it is a **dev/test stub** — it reads a dev session marker and the selected workspace and grants the
builder claim for that workspace; a request with no resolvable principal on a protected route raises
`AuthRequired` (401). Tests use FastAPI dependency overrides to inject a principal. The real OIDC cookie/CSRF
adapter (doc 18) is deferred.

**Rationale**: Tenancy authorization (D2) needs *a* claim to check; the stub provides exactly that without
pulling in an IdP. Dependency-override is the standard FastAPI test seam (no real auth in tests).

**Alternatives**: no auth at all in the slice — rejected (then FR-007/FR-019 and the 403/401 paths are
untestable, and tenancy authorization has nothing to check).

---

## D4 — Invoke runs on the console-session plane (clarify); token plane deferred

**Decision**: `POST /api/v1/agents/:id/invoke` authenticated by the console-session principal (the playground
`send()` path), `run_source="playground"`. The caller-bearer-token plane (`/v1/agents/:id/invoke`) and token
mint/custody are deferred; both planes later share the same body + `runtime.invoke` (08 §2), so adding the
token plane is additive.

**Rationale**: Clarify decision 1 — smallest first caller, no token infrastructure. Keeps the slice focused on
the invoke→SSE→runtime path and tenancy.

---

## D5 — Eval-run executes inline and takes the suite in the request body; no eval persistence

**Decision**: `POST eval-runs` accepts the eval **suite inline** (cases + the frozen baseline aggregate) in
the request body, calls `evals.run_eval(...)` synchronously, and returns the `EvalRunResult` as a typed body.
It persists nothing — there are no `eval_suites`/`eval_baselines`/`eval_runs` tables in this slice.

**Rationale**: Clarify decision 2 (inline). The M3 engine already takes cases + baseline as inputs
(data-model 004), and the DB-backed eval lifecycle is explicitly deferred — accepting the suite inline proves
eval-over-HTTP end-to-end without new tables. The async worker + `GET /jobs/:id` shape is named in the
contract for forward-compat but not built.

**Alternatives**: seed a starter suite from the DB — rejected (needs the deferred eval tables); require the FE
to pass cases is consistent with the authoring contract (05 §2a).

---

## D6 — Publish is advisory: returns the verdict, never gates (owner decision #3)

**Decision**: `POST publish` sets the version/agent status to published and returns a `PublishOut` carrying an
**advisory** verdict field (null when no eval was supplied/run). It **never** raises `EvalGateError` /
`LowConfidenceError` (`409 publish.eval_gate`/`publish.low_confidence`). The response/ docs mark the divergence
from the FE's gate expectation explicitly.

**Rationale**: Owner decision #3 — the uncalibrated judge must not gate (P4). The typed gate exceptions exist
in `errors.py` for when the calibrated gate activates; this slice simply does not raise them.

**Alternatives**: enforce the gate now — rejected (violates P4: an uncalibrated judge gating a release).

---

## D7 — Optimistic concurrency uses Postgres `xmin` as the ETag (migration-free)

**Decision**: The `AgentOut` ETag is the row's Postgres system column `xmin` (its MVCC version). `PATCH` with
an `If-Match` header compares it in the UPDATE's WHERE clause (`WHERE id=:id AND xmin=:etag`); zero rows
updated → `ConflictStale` (`409 conflict.stale`).

**Rationale**: `agents` has only `created_at` (no `updated_at`/row-version column). `xmin` is a built-in,
per-row version that changes on every update — giving correct optimistic concurrency **without a new
migration**. Keeps the slice migration-free.

**Alternatives**: add an `updated_at`/`version` column (migration) — deferred (unnecessary for the slice); a
computed content-hash ETag — rejected (misses no-op updates and is fragile vs concurrent writers).

---

## D8 — Idempotency: in-process in-flight guard + DB uniqueness; Redis store deferred

**Decision**: Creates accept an `Idempotency-Key` header. A minimal in-process registry tracks in-flight keys
per `(workspace_id, key)`; a concurrent arrival for an in-flight key raises `IdempotencyInProgress`
(`409 idempotency.in_progress`). Durable dedup of *completed* creates rides on the DB (natural/unique keys).
The short-TTL Redis idempotency store + verbatim replay (08) is deferred.

**Rationale**: Clarify/assumption — proves the `409 idempotency.in_progress` contract without Redis infra.

**Alternatives**: full Redis replay store — deferred (infra beyond the slice).

---

## D9 — Provider credential from settings/env; invoke mocked so none is used

**Decision**: The `RunContext.api_key` for invoke is sourced from the typed settings (`api/settings.py`,
env-backed). Because invoke is mocked at the gateway in all tests, no real key is used. Vault custody is
deferred (P1/M5).

**Rationale**: The runtime requires an api_key on the context; settings/env satisfies the contract for the
mocked slice. Keys never appear in logs/SSE/errors (P5/P8).

---

## D10 — `rate_limit()` is a seam returning 429; no thresholds yet (F5)

**Decision**: A `deps.rate_limit` dependency exists and can raise `RateLimited` (`429 rate.limited`); in this
slice it is a no-op/always-allow seam (optionally a trivial in-process counter for the test). Real per-token
thresholds + concurrency/spend quotas are deferred (M6).

**Rationale**: Finding F5 — the seam must exist so the 429 path and the dependency wiring are real; thresholds
are a later operational concern.

---

## D11 — No new dependencies

**Decision**: FastAPI, Starlette, uvicorn, httpx, SQLAlchemy, asyncpg, pydantic, pydantic-settings, litellm
are all already installed (`.[api]`/`.[test]`). Add nothing to `pyproject.toml`.

**Rationale**: Verified present in `services/.venv`. httpx `ASGITransport` gives an in-process test client with
no running server.
