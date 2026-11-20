# Plan: Frontend Contract Proof — Agents Family (011)

**Branch**: `feat/backend-m0-foundation` | **Date**: 2026-11-07 | **Spec**: [spec.md](./spec.md)

## Summary
Prove the mock→real drop-in contract for the agents family. Generate the typed client from `openapi.json`,
add a fetch client + an auth bridge (mock identity → `X-Dev-Principal`/`X-Workspace-Id`), and swap the
agents store source (list/by-id/create) to the real API while keeping every exported hook/function shape
identical. Verify by `lint` + `next build` + a live E2E smoke (uvicorn + compose Postgres).

## Technical Context
- **Frontend**: Next.js **16.3.8 (modified — read `node_modules/next/dist/docs` before writing FE code)**,
  React 19, TS, `useSyncExternalStore` store. Toolchain via `nvm use 24` (Node 24.19.0 / npm 11.17.0).
- **Codegen**: `openapi-typescript` (`npm run gen:api` → `src/lib/api/schema.ts`).
- **Backend**: run `cd services && .venv/bin/uvicorn api.main:app` against compose Postgres (up). Dev-auth
  via `X-Dev-Principal` + `X-Workspace-Id`.
- **No FE test suite** (`npm test` absent) → verification = lint + build + live smoke (not pytest).

## Constitution check
- **P1/P9 honesty**: only agents marked wired; verification named; drift forbidden (build gate). PASS.
- **P5 tenancy**: the bridge sends `X-Workspace-Id`; real API enforces RLS/RBAC — smoke asserts isolation. PASS.
- **P6**: no backend/runtime change. PASS.
- Next.js caveat honored (FR-007). No new backend deps; one FE devDep already present (`openapi-typescript`).

## Approach / key decisions
1. **Auth bridge** (`src/lib/api/client.ts`): a plain function `authHeaders()` reads the mock identity
   (`MOCK_PROFILE` + active workspace + resolved role from the store/iam) and returns
   `{ "X-Dev-Principal": JSON.stringify({subject, workspace_claims:{[ws]: role}}), "X-Workspace-Id": ws }`.
   Base URL from `process.env.NEXT_PUBLIC_API_BASE` (default `http://localhost:8000`).
2. **Typed fetch** using `schema.ts` paths for `GET /api/v1/agents`, `GET /api/v1/agents/{id}`,
   `POST /api/v1/agents`. A small `apiFetch` wraps fetch + headers + error surfacing (throw on non-2xx with
   the error envelope).
3. **Shape mapping** (`src/lib/api/adapters.ts`): map the API `AgentOut` → the `data.ts` `Agent` type
   (resolve any snake_case/camelCase or field deltas) so selectors/components are untouched (drop-in). The
   exact mapping is pinned against the generated types during implement.
4. **Store source swap** (`store.ts`): `loadAgents()` (async) calls the client, maps, `set({agents})`;
   `addAgent()` calls `createAgent` then updates the store. `useAgents`/`useAgent` selectors unchanged.
   A client-component effect (agents page / app boundary) calls `loadAgents()` on `activeWorkspaceId`
   change — the only new wiring; signatures stay identical.
5. **Fallback honesty**: on fetch error, surface it (toast/throw) — no silent mock fallback (the proof
   needs the real path).

## Files
```
frontend/src/lib/api/schema.ts      # NEW (generated — gen:api)
frontend/src/lib/api/client.ts      # NEW — apiFetch + authHeaders + listAgents/getAgent/createAgent
frontend/src/lib/api/adapters.ts    # NEW — AgentOut ↔ data.ts Agent mapping
frontend/src/lib/store.ts           # EDIT — loadAgents()/addAgent() call the client; selectors unchanged
frontend/src/app/(app)/agents/page.tsx (or app boundary)  # EDIT — effect to loadAgents() on ws change
frontend/.env.local or README note  # NEXT_PUBLIC_API_BASE default documented
docs/backend-integration-status.md  # EDIT — agents family FE wired → REAL (verification named)
```

## Phase 0/1 outputs
See tasks.md. research/data-model/contracts folded into this plan + the generated `schema.ts` (the contract
is the OpenAPI itself).
