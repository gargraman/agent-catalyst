# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

**Agent Catalyst** — an internal platform to **build → configure → test → publish** LLM agents, with
guardrails, provider-agnosticism, and a drift-aware evaluation core. The defensible engineering lives
in exactly two places (per `docs/scope.md`): the **provider abstraction / runtime** and the
**eval + drift-detection** core. The other ~90% (dashboard, config CRUD, versioning, token mint,
cURL export) is deliberately plain software with no model in it.

## Current state — read this first

- **Only `frontend/` is implemented.** It is a **mock-only** Next.js console: reads come from seed
  fixtures, writes are in-memory store mutations, and auth/tenancy/runtime-token checks are all
  simulated client-side. There is no running backend, database, or real LLM call anywhere in the repo.
- **The backend is fully *designed* but not built.** `docs/backend/00-overview.md … 11-*.md` specify
  modules, DDL, and interfaces for a `services/` tree (`api`, `runtime`, `evals`, `ingestion`, `mcp`)
  that **does not exist on disk yet**. Don't assume any `services/` path is real — check first.
- The root is set up to become a monorepo (`frontend/` + future `services/`), but today it is just
  `frontend/` + `docs/`.

## Commands

All dev work happens in `frontend/` (there is no root-level package manager or build).

```bash
cd frontend
npm install
npm run dev     # Next.js dev server → http://localhost:3000
npm run build   # production build
npm run lint    # eslint (flat config, eslint.config.mjs)
```

There is **no test suite** (`npm test` does not exist). The two-layer testing approach (deterministic
tests + a versioned-prompt AI eval set) is designed in the docs but not implemented — do not claim
tests pass; there are none to run.

## ⚠️ Next.js version caveat

`frontend/AGENTS.md` (imported by `frontend/CLAUDE.md`) asserts this is a **modified Next.js 16.3.8**
with breaking changes vs. training-data knowledge, and instructs reading the bundled docs at
`frontend/node_modules/next/dist/docs/` **before writing any Next.js code**. Treat that as the
authoritative instruction for this repo. Verify API shapes against the installed package rather than
from memory — the App Router conventions here may differ from what you expect.

## Frontend architecture

Next.js App Router + React 19 + TypeScript + Tailwind v4 + `lucide-react`. Path alias `@/*` → `src/*`.

- **Routing**: `src/app/(app)/` is the authenticated console (route group); each resource is a route
  — `dashboard`, `agents` (`/new`, `/[id]`), `playground`, `knowledge`, `tools` (+ `tools/mcp/new`),
  `channels` (`/new`, `/[id]`), `evaluations`, `runs`, `settings`. `(app)/layout.tsx` → `AppBoundary` enforces the session/no-access
  gate. Nav lives in `components/app-shell.tsx`.
- **State**: a hand-rolled global store in `src/lib/store.ts` built on `useSyncExternalStore` (no
  Redux/Zustand). It is seeded at module load from `src/lib/data.ts` and exposes workspace-scoped
  `useX()` hooks + mutation functions. `useSelector` caches by shallow-equality — selectors that build
  new arrays **must** go through it or `useSyncExternalStore` loops forever.
- **Auth / tenancy / RBAC (all mocked)**:
  - `src/lib/auth.tsx` — `useAuth` / `useWorkspace` / `usePrincipal`; sources identity from
    `MOCK_PROFILE`. A dev-only role/team override (in the store) lets you preview other roles.
  - `src/lib/iam.ts` — workspaces, teams, memberships. **Workspace = tenant**; all list hooks filter
    by `activeWorkspaceId`.
  - `src/lib/permissions.ts` — 4 roles (`viewer < builder < publisher < admin`) → permission sets;
    `hasPermission()` and the `<Gate>` / `GateButton` components gate UI.
  - `src/lib/access-policy.ts` — per-resource visibility policy (public/restricted by role+team);
    `src/lib/runtime-access.ts` — a deterministic client-side token *decode* that previews what the
    real server-side invoke-time access resolution will return.
- **Domain data**: `src/lib/data.ts` holds every seed array **and** the exported TypeScript types that
  define the intended API response shapes. `connectors.ts` (datasource connector registry),
  `channels.ts` (messaging-channel registry) + `channel-credentials.ts` (multi-secret capture), and
  `secrets.ts` (credential-ref masking) model config/credential capture ahead of the backend.

## The `@backend` contract (how mock maps to the real backend)

Every integration point is marked inline in code with a `@backend` comment at the data layer (one per
store hook/action/lib mock) and in a header block per page. To find all of them:

```bash
grep -rn "@backend" frontend/src
```

Authoritative mapping and contracts live in:
- `docs/backend-integration-map.md` — index of code location → endpoint/contract.
- `docs/backend-integration-plan.md` — endpoints, sequence diagrams, deployment detail.
- `docs/frontend-auth-access-plan.md` — the auth/access model (OIDC + RBAC + per-resource policy).

When wiring real calls: keep the hook/function **shapes** (`useAuth`, `RunResolution`, the exported
types in `data.ts`) identical and swap only the source — the mocks were built to be drop-in replaced.
Markers tagged `@backend remove` (seed arrays, dev role overrides, `tagResource` normalization) are
meant to be deleted once the server owns that data.

## Backend design (for when you build `services/`)

The load-bearing idea: **playground, published invoke, and every eval case all call the same
`runtime.invoke(snapshot, inputs, ctx)`** — if they diverged, drift reports would evaluate code the
endpoint never runs. `runtime/` is a pure library (no FastAPI/HTTP imports); three thin callers wrap
it. `invoke()` reads an immutable `VersionSnapshot`, never a live agent row — that's how
"editing an agent changes nothing live until an eval-gated re-point" is enforced in code.

Designed stack: FastAPI `api` + arq/RQ `worker` + Next.js `web`, over Postgres+pgvector, Redis, Vault,
Langfuse, MinIO; LangGraph for orchestration; LiteLLM for transport only (cost is computed from our
own capability profiles, never trusted from LiteLLM). See `docs/backend/00-overview.md` for the module
layout and the argued deltas from the spec.

## Where to read for intent

Read in this order: `docs/scope.md` (why/what, decision log) → `docs/spec.md` (locked requirements,
phased P1/P2 build) → `docs/backend/` (implementation-level design). The decision log in `scope.md` and
the delta table in `backend/00-overview.md` record *why* things are the way they are — check them
before reversing a choice.
