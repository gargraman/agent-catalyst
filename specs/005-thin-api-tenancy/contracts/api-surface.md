# Contract — Thin API surface (M4 slice)

The HTTP contract this slice exposes. OpenAPI 3.1 is emitted by FastAPI from the Pydantic models
(`api/schemas/`) and is the source of truth for the generated FE client. All routes are the **console-session
plane** (`/api/v1/*`, D4). `▶ RED` marks the behavior a failing test must pin before implementation (P9).
Everything is mocked at the provider boundary — no live/paid calls.

> **Auth**: S = console session (dev-stub principal, D3). **Tenancy**: every route resolves + authorizes the
> workspace (header `X-Workspace-Id` / `?workspace=`) then opens `tenant_session` (ac_app + GUC + RLS).

---

## Agents & versions (US3)

```
POST   /api/v1/agents            S   create a draft agent (body AgentCreate; header Idempotency-Key)
GET    /api/v1/agents            S   list agents in the workspace (cursor-paginated → Page[AgentOut])
GET    /api/v1/agents/:id        S   get one agent (+ versions); response carries ETag (xmin)
PATCH  /api/v1/agents/:id        S   edit (body AgentPatch; header If-Match required)
POST   /api/v1/agents/:id/versions   S   snapshot an immutable version → VersionOut
```

- Create **MUST** scope the new agent to the request workspace (never a body-supplied workspace). ▶ RED (FR-007/FR-012)
- Create **MUST** return `409 idempotency.in_progress` for a concurrent retry of an in-flight `Idempotency-Key`. ▶ RED (FR-014/D8)
- List **MUST** be cursor-paginated (`{items, next_cursor}`) via `Page[T]`. ▶ RED (FR-013)
- GET **MUST** return a `404 not_found` for an id outside the caller's workspace (never another tenant's row). ▶ RED (FR-010/US2)
- PATCH **MUST** require `If-Match`; a stale ETag → `409 conflict.stale`, row unchanged. ▶ RED (FR-015/D7)
- `POST versions` **MUST** persist an immutable snapshot (snapshot_hash set) → `VersionOut`. ▶ RED (FR-012)

---

## Invoke (US1) — SSE, console-session plane

```
POST   /api/v1/agents/:id/invoke     S   stream (text/event-stream); body InvokeIn
```

- **MUST** call the same `runtime.invoke` (not reimplement the pipeline); only `api` imports FastAPI
  (purity tests stay green). ▶ RED (FR-002/P6)
- **MUST** respond `text/event-stream` and emit ordered frames `run_start` → deltas → `usage` → `done`. ▶ RED (FR-001/FR-003)
- A runtime `error` event **MUST** surface as a typed `error` frame (code+message, no secret/stack) then `done` — never an unframed 500 mid-stream. ▶ RED (FR-004/P8)
- Inputs violating the version `io_contract` **MUST** be rejected with no provider call (zero spend). ▶ RED (FR-005)
- Each invoke **MUST** produce exactly one honest run record (workspace/version/usage/cost/latency/outcome). ▶ RED (FR-006/P8)
- A cross-tenant/unknown version id **MUST** be `404 not_found`. ▶ RED (US2)

---

## Eval + advisory publish (US4)

```
POST   /api/v1/agents/:id/eval-runs  S   run the M3 engine inline (body EvalRunIn) → EvalRunOut
POST   /api/v1/agents/:id/publish    S   advisory publish (body {version_id}) → PublishOut
```

- Eval-run **MUST** execute `evals.run_eval` over the same runtime (mocked) and return the typed verdict; no live call. ▶ RED (FR-017/SC-007)
- Publish **MUST** succeed without a passing eval and return the advisory verdict — it **MUST NOT** raise `409 publish.eval_gate`. ▶ RED (FR-018/SC-006/D6)

---

## Tenancy (US2) — applies to every route above

- Every request's DB session **MUST** connect as `ac_app` (RLS applies), never the owner/bypass role. ▶ RED (FR-008/SC-003) *(integration)*
- A cross-tenant resource read **MUST** return `404` (never another tenant's data). ▶ RED (FR-010/SC-002) *(integration, negative)*
- An unset/malformed workspace scope **MUST** yield zero rows (fail closed), not a 500. ▶ RED (FR-009) *(integration)*
- Selecting a workspace the caller holds no claim for **MUST** be `403 access.denied`. ▶ RED (FR-007)

---

## Cross-cutting

- Error envelope: every non-2xx returns `{"error":{code,message,detail?}}` via `app_error_handler`. ▶ RED (FR-011)
- `rate_limit()` seam present; can return `429 rate.limited` (no thresholds this slice). ▶ RED (FR-016/F5)
- No resolvable principal on a protected route → `401 auth.required`. ▶ RED (FR-019)
- `GET /openapi.json` **MUST** emit OpenAPI 3.1 reflecting the typed models (source of truth; FE drift-check is M12). ▶ RED (SC-004)
- Purity: importing `api` **MUST NOT** make `runtime`/`evals` depend on FastAPI (existing purity tests stay green). ▶ RED (SC-008/P6)

---

## Test strategy (mirrors M2a/M3)

- **Unit/contract** (`test_api_*.py`): httpx `ASGITransport` in-process client; FastAPI dependency overrides
  inject the principal and mock `runtime.providers.gateway.stream` / the evals path; no DB, no live calls.
- **Integration** (`pytest.mark.integration`, skip when DB down — mirrors `test_session_tenancy.py`): real
  `tenant_session` + RLS for the negative cross-tenant + ac_app-role proofs.
- Genuine behavioral RED before GREEN (P9); no live/paid calls anywhere (SC-007).
