# Feature Specification: Frontend Contract Proof — Agents Family (Phase 5)

**Feature Branch**: `feat/backend-m0-foundation` (slice 011) | **Created**: 2026-11-07 | **Status**: Draft

**Input**: "Phase 5 skeleton contract proof — now unblocked (Node available via `nvm use 24`). Generate the
typed client from the emitted OpenAPI, add a fetch client + an auth bridge (mock console identity →
backend dev-auth headers), and swap the **agents family** hooks (read, read-by-id, create) mock→real
against the running backend, keeping hook shapes identical. Prove the drop-in contract end-to-end before
scaling to the other 253 seams. Invoke-SSE deferred to a follow-up slice."

## Clarifications

### Session 2026-11-07
- Q: Toolchain availability? → Node v24.19.0 / npm 11.17.0 via `nvm use 24`; `node_modules` installed;
  `lint`+`build` pass. FE wiring is **no longer blocked** (the earlier "Node unavailable" was nvm not
  loaded in the non-interactive shell).
- Q: Scope of this proof slice? → **Agents family: read + create + by-id.** Invoke-SSE deferred.
- Q: Verification (frontend has no test suite — `npm test` does not exist)? → `npm run lint` + `npm run
  build` (typed-client drift + type check) **plus a live E2E smoke** against the real backend (uvicorn +
  compose Postgres): a created agent round-trips via the real GET, tenant-scoped.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Console reads real agents (Priority: P1)
With the backend running, the agents list (and an agent's detail) in the console reflects the **real
database** for the active workspace — not the in-memory seed — authenticated as the mock profile's
identity and scoped to the selected workspace.

**Independent Test**: seed an agent in the DB for workspace W; load the console pointed at W; the agent
list contains it (and only W's agents). Verifiable via the live API path the hook now uses.

**Acceptance Scenarios**:
1. **Given** the backend is up and workspace W has agents in the DB, **When** `useAgents()` resolves,
   **Then** it returns W's real agents (store populated from `GET /api/v1/agents`), hook signature
   unchanged.
2. **Given** a second workspace W2 with its own agents, **When** the active workspace is W, **Then**
   W2's agents are not returned (tenant isolation via the bridged `X-Workspace-Id` + RLS).
3. **Given** an agent id, **When** `useAgent(id)` resolves, **Then** it returns that agent from the real
   source.

### User Story 2 — Creating an agent hits the real API and round-trips (Priority: P1)
Creating an agent in the console issues a real `POST /api/v1/agents`; the created agent is then returned
by the real `GET`, proving the write contract (typed request/response, auth, tenancy) end-to-end.

**Independent Test**: call the real create path with a new name; then the real list includes it with a
server-assigned id.

**Acceptance Scenarios**:
1. **Given** the backend is up, **When** `addAgent(...)` runs, **Then** it issues `POST /api/v1/agents`
   and the response (server id) is reflected in the store; the function signature is unchanged.
2. **Given** a freshly created agent, **When** the list is refetched, **Then** it appears (round-trip).

### Edge Cases
- Backend unreachable → the client surfaces a clear error (not a silent mock fallback); the proof
  requires the real backend up.
- Unauthorized workspace (no role claim) → the real API returns 403; the client surfaces it (proves the
  bridge carries identity, composing with RBAC/008 + RLS).
- Unwired families (tools, runs, etc.) remain on the mock store — unchanged (no false "wired" claim).

## Requirements *(mandatory)*

- **FR-001**: Generate a typed client from the emitted `openapi.json` (`npm run gen:api` →
  `src/lib/api/schema.ts`); `npm run build` MUST pass with zero type errors / zero client drift.
- **FR-002**: Provide a fetch client with a configurable base URL (env `NEXT_PUBLIC_API_BASE`, default
  `http://localhost:8000`) that injects, on every request, the backend dev-auth headers derived from the
  mock identity: `X-Dev-Principal` (`{subject, workspace_claims:{<activeWs>:<role>}}`) and
  `X-Workspace-Id` (active workspace).
- **FR-003**: `useAgents()` MUST return the active workspace's agents sourced from `GET /api/v1/agents`,
  with its exported signature unchanged (store populated from the API; selectors untouched).
- **FR-004**: `useAgent(id)` MUST resolve from the real source, signature unchanged.
- **FR-005**: `addAgent(...)` MUST issue `POST /api/v1/agents` and reflect the server response in the
  store, signature unchanged.
- **FR-006**: Drop-in fidelity — only the **data source** changes; exported hook/function shapes and the
  `data.ts` types stay identical (so unswapped call sites keep compiling).
- **FR-007**: FE code MUST respect the repo's modified Next.js 16.3.8 conventions (read
  `node_modules/next/dist/docs` before writing Next.js code; client-component data fetching).
- **FR-008**: The agents family MUST be verified by `lint` + `build` **and** a live E2E smoke (uvicorn +
  compose Postgres): a created agent round-trips via the real GET; a cross-workspace agent is excluded.
- **FR-009**: Honesty (P1/P9) — only the agents family may be marked wired; all other seams stay MOCK;
  the integration-status tracker is updated to REAL only for agents, with the verification named.

## Success Criteria *(mandatory)*
- **SC-001**: `gen:api` produces `schema.ts` and `next build` passes with it (no drift, no type errors).
- **SC-002**: With the backend up, the agents list in the real data path returns exactly the active
  workspace's DB agents (tenant-correct).
- **SC-003**: A create round-trips: POST then GET shows the new agent with a server id.
- **SC-004**: Unswapped families still build and run on the mock (no regression to the console build).
- **SC-005**: No hook/function signature in `store.ts` changed for the swapped agents hooks (drop-in).

## Assumptions
- Backend dev-auth stub (`X-Dev-Principal`) is the Phase-5 identity bridge; real OIDC is later-P1. The
  bridge maps `MOCK_PROFILE` + active workspace + resolved role → the two headers.
- `openapi.json` is current enough for the agents paths; if drift is found, regenerate it from the app
  (out of scope to rebuild the whole emit pipeline here).
- The live smoke runs against compose Postgres (already up) + a locally-run uvicorn; no live/paid LLM
  calls are involved (agents CRUD only).
- Invoke-SSE, and the remaining 250 seams, are explicitly out of scope for this slice.
