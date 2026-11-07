# Tasks: Frontend Contract Proof — Agents Family (011)

**Feature**: `specs/011-fe-contract-proof` | **Branch**: `feat/backend-m0-foundation` | **Date**: 2026-11-07

**Verification** (no FE test suite): `nvm use 24` then `npm run lint` + `npm run build` (type/drift gate) +
a **live E2E smoke** against `uvicorn api.main:app` on compose Postgres. Respect the modified Next.js
caveat — read `node_modules/next/dist/docs` before writing FE code.

## Phase 1: Setup / de-risk
- [x] T001 Confirm toolchain + backend: `nvm use 24` (Node 24), compose Postgres healthy, `uvicorn` importable.
  Read the relevant `node_modules/next/dist/docs` page on client-component data fetching (FR-007). No change.
- [x] T002 Generate the typed client: `npm run gen:api` → `src/lib/api/schema.ts`; confirm `npm run build`
  still passes with the generated file present (SC-001 — no drift). If `openapi.json` lacks the agents paths,
  stop and flag (regenerating the emit pipeline is out of scope).
- [x] T003 Live baseline smoke (RED-equivalent): start `uvicorn api.main:app`; `curl` `GET /api/v1/agents`
  with the dev-auth headers the bridge will send → confirm a 200 + tenant-scoped JSON; seed one agent and
  confirm it appears. Pins the exact `AgentOut` shape for the adapter.

## Phase 2: US1 — read real agents
- [x] T004 [US1] `src/lib/api/client.ts`: `authHeaders()` (mock identity → `X-Dev-Principal` +
  `X-Workspace-Id`), `apiFetch` (base URL from `NEXT_PUBLIC_API_BASE`, error-surfacing), and
  `listAgents(ws)` / `getAgent(ws, id)` typed against `schema.ts`.
- [x] T005 [US1] `src/lib/api/adapters.ts`: map `AgentOut` → `data.ts` `Agent` (pin fields vs T003 output).
- [ ] T006 [US1] `store.ts`: `loadAgents()` async (client → adapter → `set({agents})`); keep `useAgents` /
  `useAgent` selector signatures unchanged. Trigger `loadAgents()` from a client-component effect on
  `activeWorkspaceId` change (agents page or app boundary).
- [x] T007 [US1] `npm run lint` + `npm run build` green; live smoke: list returns the active workspace's DB
  agents and excludes another workspace's (SC-002, tenant isolation).

## Phase 3: US2 — create round-trips
- [ ] T008 [US2] `client.ts`: `createAgent(ws, body)` typed (`POST /api/v1/agents`). `store.ts` `addAgent()`
  calls it then updates the store; signature unchanged.
- [ ] T009 [US2] `build` green; live smoke: create via the real path → the new agent (server id) appears in a
  refetched list (SC-003 round-trip).

## Phase 4: Polish
- [ ] T010 Honesty + docs: `npm run lint` + `npm run build` final green; confirm unswapped families still
  build on the mock (SC-004). Update `docs/backend-integration-status.md` — agents family **FE wired → REAL**
  (verification named: build + live smoke); note the auth bridge + `NEXT_PUBLIC_API_BASE`. Leave all other
  seams MOCK. Record the exact smoke commands in `specs/011-fe-contract-proof/` (quickstart note).

## Dependencies
Setup (T001→T003) → US1 (T004→T007) → US2 (T008→T009) → Polish (T010). T005 depends on T003's shape output.

## Notes
- Drop-in rule: never change an exported hook/function signature — only the data source (FR-006/SC-005).
- No silent mock fallback on error — surface it (the proof needs the real path).
- Invoke-SSE + the other 250 seams are out of scope (follow-up slices).
