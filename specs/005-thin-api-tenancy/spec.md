# Feature Specification: Thin API + Tenancy Slice (M4 — walking-skeleton slice)

**Feature Branch**: `feat/backend-m0-foundation` (continuing the backend build branch)

**Created**: 2026-11-05

**Status**: Draft

**Input**: User description: "M4 thin API + tenancy slice — the first HTTP caller layer that wraps the pure
runtime. FastAPI app exposing the minimal endpoints to drive one agent end-to-end (agents CRUD, versions,
advisory publish, eval-runs, SSE invoke), with fail-closed RLS-GUC tenancy, typed Pydantic contracts mirroring
the frontend, idempotency/If-Match, and the advisory-publish divergence (owner decision #3). Keep runtime +
evals pure; only the API imports the web framework. Mocked — no live/paid calls."

> **Scope note (why this is a slice).** `docs/backend/08-api-surface.md` specifies the full API surface
> (253 frontend seams, three auth planes, Vault, ClickHouse, the async worker, the real publish gate). This
> feature builds the **thinnest HTTP layer that makes the already-built pure cores reachable end-to-end**: the
> first caller of `runtime.invoke` (M2a) and the `evals` engine (M3), plus the load-bearing tenancy boundary
> (P5) and the typed contract the frontend already expects. It is the walking-skeleton's API, not all of M4.
> Everything persistence-heavy, infra-heavy, or auth-heavy beyond the minimum is deferred (see *Out of Scope*).

## Clarifications

### Session 2026-11-05

- Q: Which auth plane should the SSE invoke endpoint use in this slice? → A: **Console-session / playground plane** — invoke runs under the console session (the playground `send()` preview path); the caller bearer-token plane (token mint/custody) is deferred. Both planes later share one body + one `runtime.invoke`, so the token plane drops in without rework.
- Q: How should eval-runs execute in this slice? → A: **Inline, synchronous** — `POST` eval-run runs the M3 engine in-request and returns the verdict; the async worker + `GET /jobs/:id` shape is named in the contract but the deployable is deferred.
- Q: Which agent write-endpoints are in scope for US3? → A: **Create + version + PATCH** (create / get / list / `POST` version / `PATCH`), so idempotency (FR-014) and `If-Match`/`conflict.stale` optimistic concurrency (FR-015) are real and tested; **DELETE and duplicate are deferred**.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Invoke an agent over HTTP as a stream, through the one shared runtime (Priority: P1)

A caller sends a request to the invoke endpoint for a specific agent version and receives a **Server-Sent
Events** stream — `run_start`, `text_delta`/`reasoning_delta` (and `tool_call_*` when present), a terminal
`usage`, and `done` — rendered identically to how the playground will render it. The HTTP layer does **not**
reimplement any execution logic: it resolves the frozen version snapshot, calls the **same**
`runtime.invoke`, and re-encodes the normalized events it yields as SSE frames. Every invocation produces an
honest terminal frame (`done` with a status, or a typed `error` frame) and is attributed to a workspace.

**Why this priority**: This is the entire point of the slice and of the walking skeleton — proving the
load-bearing invariant (one `runtime.invoke` shared by all callers, P6) over a real wire. With it, the
already-built runtime becomes usable from outside the process; without it the backend is still headless.

**Independent Test**: With `runtime.invoke` driven by a mocked gateway (no live call), POST to the invoke
endpoint and assert the response is `text/event-stream`, the frames arrive in order (`run_start` → deltas →
`usage` → `done`), the payloads match the typed frame contract, and a provider error surfaces as a typed
`error` frame then `done`, never a raw 500.

**Acceptance Scenarios**:

1. **Given** a published agent version in the caller's workspace, **When** they POST valid inputs to the
   invoke endpoint, **Then** they receive an ordered SSE stream ending in a `done` frame with a terminal
   status, and the bytes are `text/event-stream`.
2. **Given** the runtime yields a normalized `error` event (provider failure), **When** invoke runs, **Then**
   the stream carries a typed `error` frame (code + message, no secrets, no stack) followed by `done`.
3. **Given** inputs that violate the version's input contract, **When** invoke runs, **Then** the caller gets
   a typed validation rejection and **no** provider call is made (fail closed, zero spend).
4. **Given** an invocation, **When** it completes, **Then** exactly one run record is produced carrying the
   workspace, version, usage, cost, latency, and outcome (observability, P8) — never fabricated on failure.

---

### User Story 2 — Fail-closed tenant isolation on every request (Priority: P1)

Every API request is bound to exactly one workspace, and the database session for that request runs as the
non-privileged application role with the workspace scope pinned, so the row-level-security policies filter
every tenant-owned table. A caller who names a workspace they have no claim for is refused; a caller who
reaches for another tenant's resource sees "not found," never another tenant's data. An unset or malformed
workspace scope yields **zero rows**, not an error-leak or a cross-tenant read.

**Why this priority**: Workspace separation is a security boundary from release #1 (P5), even in a
single-org pilot. It is load-bearing for every other endpoint and must exist before any data is served — a
tenancy hole discovered later invalidates everything built on top.

**Independent Test**: Seed two workspaces with one agent each; as a caller scoped to workspace A, request
workspace B's agent → 404 (not a 200 with B's data); request with no/invalid scope → zero rows; verify the
request DB session connects as the app role (not the owner/bypass role). Negative cross-tenant assertions are
mandatory.

**Acceptance Scenarios**:

1. **Given** a caller scoped to workspace A, **When** they request a resource owned by workspace B by id,
   **Then** the response is `404 not_found` — B's data is never returned.
2. **Given** a request with a missing or malformed workspace scope, **When** it reads any tenant table,
   **Then** it sees zero rows (fail closed), not a 500 or a cross-tenant row.
3. **Given** a caller whose session holds no role claim for the requested workspace, **When** they select it,
   **Then** the response is `403 access.denied` — selection cannot be forged into a leak.
4. **Given** any request on the data path, **When** its DB session is opened, **Then** it connects as the
   non-privileged application role (RLS applies), never the migration/owner role (which bypasses RLS).

---

### User Story 3 — Create, read, and version an agent through typed contracts (Priority: P2)

A builder creates a draft agent, lists and reads agents in their workspace, and snapshots an immutable
version — all through request/response shapes that match the frontend's existing types and error envelope.
Creates accept an idempotency key so a retried create does not duplicate; edits use optimistic concurrency
(an `If-Match`/ETag) so a stale write is rejected rather than silently clobbering a concurrent edit.

**Why this priority**: Create→version is the front half of the end-to-end skeleton and the first proof that
the typed contract is drop-in for the frontend. It depends on tenancy (US2) but is independently testable.

**Independent Test**: Create an agent (assert the response matches the typed shape and lands in the caller's
workspace); list/get it; POST a version and assert an immutable snapshot is produced; retry a create with the
same idempotency key and assert no duplicate; PATCH with a stale `If-Match` and assert `409 conflict.stale`.

**Acceptance Scenarios**:

1. **Given** a builder in a workspace, **When** they create an agent with valid input, **Then** the response
   matches the typed agent shape and the agent is scoped to that workspace.
2. **Given** an agent, **When** they POST a version, **Then** an immutable version snapshot is produced and
   returned in the typed shape.
3. **Given** a create retried with the same idempotency key while the first is in flight, **When** it
   arrives, **Then** it is refused with `409 idempotency.in_progress` (no duplicate).
4. **Given** a PATCH carrying a stale `If-Match` ETag, **When** it is applied, **Then** it is refused with
   `409 conflict.stale` and the record is unchanged.

---

### User Story 4 — Run an eval and publish under the advisory gate (Priority: P3)

A builder triggers an eval run for a version (which executes the M3 eval engine over the same
`runtime.invoke`) and receives a verdict. They then publish the version: the publish endpoint returns the
eval verdict but, in **advisory mode**, does **not** block publishing even without a PASS — a deliberate,
temporary divergence from the frontend's expectation of a `409 publish.eval_gate`, in force until the
κ-calibrated gate activates (owner decision #3).

**Why this priority**: It completes the end-to-end skeleton (eval → publish → invoke) and exercises the M3
engine through HTTP, but the advisory posture means it gates nothing yet, so it is the lowest-risk slice to
land last.

**Independent Test**: POST an eval run for a version (with the runtime/evals mocked) and assert a verdict is
returned; POST publish with no passing eval and assert the version publishes **and** the verdict is reported
(not a `409`); confirm the advisory divergence is explicit in the response contract.

**Acceptance Scenarios**:

1. **Given** a version, **When** a builder triggers an eval run, **Then** the eval engine runs through the
   shared runtime and a verdict is returned in the typed shape (mocked; no live call).
2. **Given** a version with no passing eval, **When** the builder publishes it, **Then** the publish
   **succeeds** and the response reports the (advisory) verdict — it is **not** blocked with
   `409 publish.eval_gate`.
3. **Given** advisory mode, **When** any publish occurs, **Then** the divergence from the frontend's
   expected gate behavior is documented and discoverable in the response/contract.

---

### Edge Cases

- **Client disconnects mid-stream**: the server cancels the runtime task (frees the provider call / any
  reservation) and still records one honest run; the stream simply ends.
- **Workspace scope present but caller lacks the claim**: `403 access.denied` (not a leak, not a 404).
- **Unset/empty/malformed workspace scope**: zero rows (fail closed), never a 500 from a bad cast.
- **Invoke of a non-existent or cross-tenant version id**: `404 not_found`.
- **Idempotency key reused after the first completed**: the stored result/metadata is replayed (or a
  `result_expired` marker after TTL), not re-executed.
- **Unhandled server error**: returns the generic `500 internal` envelope — never a stack trace or secret.
- **Runtime/evals purity**: importing the API must not make `services/runtime` or `services/evals` depend on
  the web framework — the purity guards stay green.

## Requirements *(mandatory)*

### Functional Requirements

**Invoke / SSE (US1)**

- **FR-001**: The system MUST expose an HTTP invoke endpoint for an agent version that streams results as
  Server-Sent Events (`text/event-stream`).
- **FR-002**: The invoke endpoint MUST call the **same** `runtime.invoke` the playground and (future)
  published/eval callers use — it MUST NOT reimplement validation, the pipeline, cost, or event handling
  (P6). The runtime-purity guard MUST stay green (only the API layer imports the web framework).
- **FR-003**: The SSE encoding MUST map the runtime's normalized events to typed frames — at minimum
  `run_start`, `text_delta`, `reasoning_delta`, `tool_call_start`/`_delta`/`_end`, `usage`, `error`, `done`
  — one JSON object per `data:` line with `event:` set to the frame type (finding F9).
- **FR-004**: A provider/runtime failure MUST surface as a typed `error` frame (code + message, api-key
  scrubbed, no stack) followed by a terminal `done`, never as an unframed 500 mid-stream.
- **FR-005**: Inputs violating the version's input contract MUST be rejected before any provider call (zero
  spend), surfaced via the typed error envelope.
- **FR-006**: Each invocation MUST produce exactly one run record carrying workspace, version, usage, cost,
  latency, outcome, and correlation id (P8); a failed run MUST NOT be recorded as success.

**Tenancy (US2)**

- **FR-007**: Every request MUST be bound to exactly one workspace, taken from the client's selection and
  **authorized against the caller's claim** for that workspace before use (selecting an unheld workspace →
  `403 access.denied`).
- **FR-008**: The request's database session MUST connect as the non-privileged application role and pin the
  workspace scope for the transaction, so the row-level-security policies filter every tenant-owned table.
  The migration/owner role (which bypasses RLS) MUST NEVER serve a request.
- **FR-009**: An unset, empty, or malformed workspace scope MUST yield zero rows (fail closed), never a
  cross-tenant row or a cast error.
- **FR-010**: A cross-tenant resource access MUST return `404 not_found` (indistinguishable from absent),
  never another tenant's data. Negative cross-tenant tests are REQUIRED.

**Contracts & lifecycle (US3)**

- **FR-011**: Request/response/error bodies MUST use typed models that mirror the frontend `data.ts` types
  and the existing error envelope (`{"error": {code, message, detail?}}`); the OpenAPI schema is the source
  of truth for the generated frontend client.
- **FR-012**: The system MUST support creating, reading (get + list), **editing (PATCH)**, and versioning an
  agent, scoped to the caller's workspace; a version MUST be an immutable snapshot. *(Clarified 2026-11-05:
  the write set is create / get / list / PATCH / POST-version; DELETE and duplicate are deferred.)*
- **FR-013**: List endpoints MUST be cursor-paginated (`{items, next_cursor}`) using the existing pagination
  contract.
- **FR-014**: Creates MUST honor an idempotency key: a concurrent retry of an in-flight create MUST be
  refused with `409 idempotency.in_progress` rather than creating a duplicate.
- **FR-015**: Edits (PATCH) MUST use optimistic concurrency: a stale `If-Match`/ETag MUST be refused with
  `409 conflict.stale`, leaving the record unchanged.
- **FR-016**: The system MUST expose a `rate_limit()` seam returning `429 rate.limited` when tripped
  (finding F5; a seam in this slice — enforcement thresholds are a later concern).

**Eval & advisory publish (US4)**

- **FR-017**: The system MUST expose an eval-run endpoint that executes the M3 eval engine over the shared
  runtime and returns a typed verdict (mocked in this slice; no live/paid call).
- **FR-018**: The publish endpoint MUST, in advisory mode, return the eval verdict but **allow** publishing
  even without a PASS — it MUST NOT raise `409 publish.eval_gate`. This divergence MUST be explicit and
  documented, and reversible when the calibrated gate activates (owner decision #3).

**Auth & cross-cutting**

- **FR-019**: The system MUST resolve a caller identity (a minimal session principal with a workspace role
  claim) sufficient to drive tenancy authorization; a request with no resolvable identity on a protected
  route MUST return `401 auth.required`.
- **FR-020**: No live/paid provider call may occur in this feature — invoke and eval run against a mocked
  runtime/gateway (mirrors M2a/M3 SC-008); the first live call is deferred to Phase 5 under the paid-call
  protocol.

### Key Entities *(include if feature involves data)*

- **API session principal**: the resolved caller — identity + the per-workspace role claim used to authorize
  the selected workspace. (Minimal stub in this slice; real IdP later.)
- **Agent / AgentVersion (API projections)**: the typed request/response shapes for create/get/list/version,
  mirroring the frontend `Agent` / version types; a version is an immutable snapshot.
- **SSE frame**: a typed streaming event (`run_start`, `text_delta`, `reasoning_delta`, `tool_call_*`,
  `usage`, `error`, `done`) — the re-encoding of a runtime normalized event onto the wire.
- **Eval-run request/verdict (API projection)**: the typed shape mapping to the M3 `EvalRunResult`.
- **Error envelope**: `{"error": {code, message, detail?}}` with the typed code set (`input.invalid`,
  `auth.required`, `access.denied`, `not_found`, `conflict.stale`, `idempotency.in_progress`, `rate.limited`,
  `publish.eval_gate` (advisory: not raised here), `internal`, …).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A caller can invoke a published agent version and receive an ordered SSE stream ending in a
  terminal `done` (or typed `error`) frame — the walking-skeleton invoke works over the wire (mocked).
- **SC-002**: 100% of cross-tenant resource requests return `404` (never another tenant's data); 100% of
  unset/malformed-scope reads return zero rows — proven by negative cross-tenant tests.
- **SC-003**: Every request's data-path DB session connects as the non-privileged app role (RLS applies);
  zero requests use the RLS-bypassing owner role.
- **SC-004**: The generated OpenAPI schema produces a frontend client whose types match the existing
  `data.ts` shapes with no drift (contract fidelity).
- **SC-005**: A retried create with the same idempotency key produces no duplicate; a stale `If-Match` PATCH
  is refused — in 100% of trials.
- **SC-006**: In advisory mode, publishing a version without a passing eval succeeds while still reporting
  the verdict — `publish.eval_gate` is raised in 0% of publishes.
- **SC-007**: No live/paid model call occurs across the entire test suite for this feature.
- **SC-008**: Importing the API leaves `services/runtime` and `services/evals` pure — the purity guards stay
  green (no web-framework import leaks into the libraries).

## Assumptions

- **Scope decisions resolved in `/speckit-clarify` (Session 2026-11-05):**
  - **Invoke auth plane**: the invoke endpoint runs under the **console-session plane** (the playground
    `send()` preview path), not the caller-bearer-token plane — token mint/custody is deferred. Both planes
    later share one body + one `runtime.invoke` (08 §2).
  - **Eval execution**: eval-run executes **inline** (synchronously) in the request; the async worker +
    `GET /jobs/:id` polling shape is named but the deployable worker is deferred.
  - **Write-endpoint set**: create / get / list / **PATCH** / POST-version (DELETE + duplicate deferred), so
    idempotency (FR-014) and `If-Match` concurrency (FR-015) are exercised.
- **Informed guess (not clarified — low uncertainty):** idempotency dedup uses the database (the create/`runs`
  unique constraint) plus a minimal in-process in-flight guard; the short-TTL Redis idempotency store (08) is
  deferred.
- The compose Postgres with migrations `0001`–`0004` (RLS policies `0002`, `ac_app` role `0003`) is the test
  database; integration tests that exercise RLS need it running (mirrors M0).
- A minimal session principal is derived from the request (e.g. a dev/test session + `X-Workspace-Id`
  selection) — the real OIDC/IdP adapter (doc 18) is deferred.
- `runtime.invoke`/`invoke_result` (003) and the `evals` engine (004) are available and are the single
  shared execution/scoring paths the API wraps.
- Test strategy mirrors M2a/M3: genuine behavioral RED before GREEN (P9); everything mocked at the provider
  boundary; no live/paid calls.
- Governance: viability gate 🔴 GATE-OPEN; built under the 2026-11-03 advisory-gate authorization (publish
  held advisory-only). This feature does not change that posture.

## Out of Scope *(deferred to later M4-remainder / M5 / Phase 5)*

- The full **253-seam CRUD** (knowledge, tokens, provider keys, workspaces, members, access-policy, runs,
  metrics dashboards) — this slice is agents/versions/eval/publish/invoke only.
- The **caller bearer-token plane** + token mint/custody, and the **real OIDC/IdP adapter** (doc 18).
- **Real Vault Transit** for provider/tool/channel credentials.
- The full **4-role RBAC** + per-resource access policy and the **org-admin plane** (`/api/v1/org/*`).
- The **async worker deployable** (arq/RQ + Redis) and full job-status streaming — eval runs inline here.
- The **real publish gate** (`assert_publishable` / `409 publish.eval_gate` enforcement) — advisory until κ
  calibration (owner decision #3).
- **ClickHouse** analytics, Langfuse spans, the Redis idempotency store, and real rate-limit thresholds.
- Tools / RAG / guardrails / sub-agent SSE frames (their runtime features are later milestones).
- The **first live/paid invoke** — deferred to Phase 5 under the paid-call protocol.
- Wiring the actual frontend hooks to the generated client (that is the Phase-5 / M12 contract-proof step).
