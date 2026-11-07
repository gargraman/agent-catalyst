# Phase 5 Contract Proof — Results & Findings (011)

**Date:** 2026-11-07 · **Toolchain:** `nvm use 24` (Node v24.19.0 / npm 11.17.0).

## What is PROVEN (verified, not asserted)

1. **Toolchain unblocked.** `npm run lint` + `npm run build` pass clean. The session-long "Node unavailable"
   was false — nvm wasn't loaded in the non-interactive shell.
2. **Typed client generated (no drift).** `npm run gen:api` → `src/lib/api/schema.ts` from `openapi.json`;
   `next build` passes with it (SC-001). *(Note: `openapi-typescript` was fetched from the public npm
   registry because the Trellix artifactory mirror returned 502 — a registry outage, not a toolchain gap.)*
3. **Typed client + auth bridge + adapters build-verified** (`src/lib/api/client.ts`, `adapters.ts`):
   `next build` + `lint` green (SC-005 — the adapter type-checks against the real `data.ts` `Agent`).
4. **Live end-to-end round-trip** against a real backend (`uvicorn api.main:app` on compose Postgres),
   driven by the **actual FE client code** (Node type-strip, not curl):
   - `listAgents` → 2 → `createAgent(agentToCreate(draft))` → `agentOutToAgent` → `listAgents` → 3.
   - Tenant-scoped (only the active workspace's agents returned), authed via the mock→dev-auth bridge
     (`X-Dev-Principal` + `X-Workspace-Id`). Create round-trips with a server-assigned id (SC-002/SC-003
     at the client+API level).

## Contract FINDINGS surfaced by the proof (the point of Phase 5)

- **F-A — thin `AgentOut` projection.** The list/by-id response carries only id/name/status/active_version_id/
  workspace_id/created_at/versions. It has **no `provider`/`model` and no 30-day metrics** that the console's
  rich `Agent` card renders. The adapter fills these with placeholders. *Fix path:* enrich `AgentOut`
  (provider/model from the active version; metrics from the metrics endpoints) or have the FE fetch the
  version + metrics. Backend follow-up.
- **F-B — `io_contract.input_schema` required on create.** `POST /agents` rejects a bare body with
  `400 input.invalid`. The adapter now sends a minimal valid `io_contract`.
- **F-C — provider casing mismatch.** The console provider literal is capitalized (`"Anthropic"`); the
  backend capability-profile key is lowercase (`"anthropic"`) — a capitalized value is rejected
  `400 "unknown provider/model"`. The adapter now lowercases provider.
- **F-D (blocks the BROWSER lighting up) — workspace id impedance.** The mock console's workspace registry
  uses **slug ids** (`trellix-xdr`), but the real backend scopes by **workspace UUIDs** (RLS casts
  `app.workspace_id::uuid`). So the `store.ts`/agents-page browser wiring cannot show real data until the
  **workspace registry itself is backed by the real backend** (slug→UUID). This is a separate seam and the
  main dependency for a true in-browser agents proof.

## Status of this slice

- **Built + proven:** `schema.ts`, `client.ts`, `adapters.ts` — the typed client, auth bridge, and both
  adapter directions, verified by build + live runtime round-trip.
- **Deferred (honest):** the `store.ts` source swap (`loadAgents`/`addAgent`) + the agents-page load effect —
  they would build, but **cannot produce real data in the browser** until F-D (workspace registry) is wired.
  Committing them now would be dormant, non-functional "wiring" (P1/P9 — not claimed as wired).
- **Invoke-SSE + the other ~250 seams:** out of scope (later slices).

## Repro (live smoke)
```bash
# backend
cd services && docker compose -f deploy/compose.yaml up -d            # (already up)
AC_PROVIDER_API_KEY=sk-dev-unused .venv/bin/uvicorn api.main:app --port 8000
# frontend contract (needs a REAL workspace UUID in the DB)
cd frontend && nvm use 24 && npm run gen:api && npm run build
# runtime round-trip via the real client code (Node type-strip): listAgents → createAgent → listAgents
```
