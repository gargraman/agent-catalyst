# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

**Agent Platform** — an internal platform to **build → configure → test → publish** LLM agents, with
guardrails, provider-agnosticism, and a drift-aware evaluation core. The defensible engineering lives
in exactly two places (per `docs/scope.md`): the **provider abstraction / runtime** and the
**eval + drift-detection** core. The other ~90% (dashboard, config CRUD, versioning, token mint,
cURL export) is deliberately plain software with no model in it.

## Current state — read this first

This is a **monorepo**: `frontend/` (Next.js console) + `services/` (Python backend) + `docs/` + `specs/`
(spec-kit feature slices) + `deploy/` (local dev stack) + `mvp/` (the viability-gate prototype).

- **Backend (`services/`) is BUILT, not just designed.** ~210 Python files, ~730 pytest tests (TDD).
  Milestones **M0–M5 + M11 are shipped** (provider gateway, the `runtime.invoke` spine, the eval/drift
  engine, thin API + tenancy/RLS, the full M5 security set, and the M11 local dev stack). What exists on
  disk under `services/`: `runtime/` (pure lib) + `runtime/providers/`, `evals/`, `api/` (12 routers +
  12 schemas + `auth/` + `secrets/` + `publish/`), and `migrations/` (10 Alembic revisions).
- **Still unbuilt (designed only):** the orchestration layer — `runtime/graph/` (LangGraph),
  `retrieval/` (RAG), `guardrails/`, `memory/`, `cache/` — plus `ingestion/`, `mcp/`, and the
  `channels`/`adoption`/`org`/`notifications` routers. M6 is only **partially** done (spend cap + run
  observability shipped; orchestration not). Don't assume these paths exist — check first.
- **Frontend (`frontend/`) is MOCK by default; 4/257 seams are live behind a build flag.** By default
  reads come from seed fixtures, writes are in-memory store mutations, auth/tenancy/RBAC are simulated
  client-side. With `NEXT_PUBLIC_API_MODE=live`, **workspace discovery + agents list/detail/create** come
  from the real API (same-origin via the `/api/v1/*` rewrite in `next.config.ts` → `AC_API_ORIGIN`,
  default `:8000`). Live wiring is incremental: a workspace with a `backendId` (from
  `GET /api/v1/workspaces`) sources its *wired* families from the API; everything else stays mock.
  Wiring the next family = a `loadX()` on the store's `load()` convention + an adapter + a `useEnsureX()`
  in its page. Wiring the frontend to the real backend is the **current focus**.
- **⚠️ Source of truth for build status** is `docs/backend-integration-status.md` (per-family BUILT/MOCK
  table, reconciled at each feature) — trust it over this file if they ever disagree. The
  `implementation-plan.md` DoD table tracks milestone completion.

## Commands

### Backend (`services/`) — use the project venv (`services/.venv`)

```bash
cd services
./.venv/bin/python -m pytest -q -m "not integration"   # fast, infra-free suite (~480 tests)
./.venv/bin/python -m pytest -q -m integration          # needs Postgres (via deploy/compose.yaml)
./.venv/bin/python -m pytest -q --collect-only          # ~730 tests collected
alembic upgrade head                                     # run migrations
```

The two-layer testing design is: deterministic tests (built, passing) + a versioned-prompt AI eval set
(the engine exists under `evals/`, but **no authored eval corpus is on disk yet** — do not claim drift
detection is proven; it is unexercised against real prompt/model variation).

### Local dev stack (root `Makefile` → `deploy/compose.yaml`)

```bash
make up                # Postgres + PgBouncer + Redis + LocalStack (secrets+S3) + migrate + api
make down              # stop (volumes kept)
make test-integration  # opt-in LocalStack integration tests (AC_RUN_LOCALSTACK gate, NOT -m integration)
```

Compose publishes Postgres on host **5433** and Redis on **6380** (to avoid clashing with local servers);
CI uses 5432. The stack is **dev-only**: no `worker` or `web` service, no prod IaC (no Terraform/Helm/k8s)
anywhere in the repo, and the `observability` profile (ClickHouse/Langfuse) is a **placeholder**, not
Langfuse-v3-ready.

### Frontend (`frontend/`)

```bash
cd frontend
npm install
npm run dev     # Next.js dev server → http://localhost:3000 (pure mock)
NEXT_PUBLIC_API_MODE=live npm run dev   # live mode: needs `make up` (API on :8000) + a members row for the mock user
npm run build   # production build
npm run lint    # eslint (flat config, eslint.config.mjs)
```

Next 16 needs **Node ≥ 20.9** — the non-interactive shell's default nvm Node is 18; use `nvm use 24`
(or prepend `~/.nvm/versions/node/v24.19.0/bin` to `PATH`). There is no frontend test suite (`npm test`
does not exist).

## CI

`.github/workflows/ci.yml` defines five jobs: `services-unit` (`pytest -m "not integration"` +
runtime-purity), `services-integration` (pgvector service + `alembic upgrade head` + `-m integration`),
`openapi-emit` smoke, `mvp-unit`, and `secret-scan` (gitleaks, pinned). The `services-unit` job assumes
the infra-free suite is genuinely DB-free — a test that touches Postgres must carry
`@pytest.mark.integration` or it will red that job.

## ⚠️ Next.js version caveat

`frontend/AGENTS.md` (imported by `frontend/CLAUDE.md`) asserts this is a **modified Next.js 16.3.8**
with breaking changes vs. training-data knowledge, and instructs reading the bundled docs at
`frontend/node_modules/next/dist/docs/` **before writing any Next.js code**. Treat that as the
authoritative instruction for this repo. Verify API shapes against the installed package rather than
from memory — the App Router conventions here may differ from what you expect.

## Frontend architecture

Next.js App Router + React 19 + TypeScript + Tailwind v4 + `lucide-react`. Path alias `@/*` → `src/*`.

- **Routing**: `src/app/(app)/` is the authenticated console (route group); each resource is a route
  — `dashboard`, `agents` (`/new`, `/[id]`), `playground`, `knowledge` (`/new`, `/[id]`), `tools`
  (+ `tools/mcp/new`, `tools/mcp/[id]`), `skills` (`/new`, `/[id]`), `channels` (`/new`, `/[id]`),
  `evaluations`, `runs`, `approvals`, `settings`. `(app)/layout.tsx` → `AppBoundary` enforces the
  session/no-access gate. Nav lives in `components/app-shell.tsx`.
- **State**: a hand-rolled global store in `src/lib/store.ts` built on `useSyncExternalStore` (no
  Redux/Zustand). It is seeded at module load from `src/lib/data.ts` and exposes workspace-scoped
  `useX()` hooks + mutation functions. `useSelector` caches by shallow-equality — selectors that build
  new arrays **must** go through it or `useSyncExternalStore` loops forever.
- **Real API client (partially wired, live flag)**: `src/lib/api/client.ts` (`LIVE_API`, `apiFetch` —
  the single place dev-auth headers are attached — typed `ApiError`, `API_BASE` same-origin by default),
  `src/lib/api/adapters.ts` (backend DTO ↔ `data.ts` type mapping), and a generated `src/lib/api/schema.ts`
  (OpenAPI types; regenerate with `npm run gen:api` against `frontend/openapi.json`). In `store.ts`, the
  "live API" section holds the `load()` convention (keyed `RemoteStatus`, in-flight de-dup), `loadWorkspaces`,
  `loadAgents`/`loadAgent`, and `useEnsureX()` mount hooks; `components/live-status.tsx` is the shared
  Live/Mock/error strip. Wiring = add a `loadX()` + adapter, keep the hook shape identical.
- **Auth / tenancy / RBAC (all mocked)**:
  - `src/lib/auth.tsx` — `useAuth` / `useWorkspace` / `usePrincipal`; sources identity from
    `MOCK_PROFILE`. A dev-only role/team override (in the store) lets you preview other roles.
  - `src/lib/iam.ts` — workspaces, teams, memberships. **Workspace = tenant**; all list hooks filter
    by `activeWorkspaceId`. The console keys workspaces by **slug** while the backend scopes by **UUID**
    (feature 011 F-D) — bridged by `Workspace.backendId` (UUID, sent as `X-Workspace-Id`) + `backendRole`
    (server-resolved, authoritative for gating), set by live discovery. Once every family is wired, `id`
    becomes the UUID and `backendId` folds away.
  - `src/lib/permissions.ts` — 4 roles (`viewer < builder < publisher < admin`) → permission sets;
    `hasPermission()` and the `<Gate>` / `GateButton` components gate UI.
  - `src/lib/access-policy.ts` — per-resource visibility policy (public/restricted by role+team);
    `src/lib/runtime-access.ts` — a deterministic client-side token *decode* that previews what the
    real server-side invoke-time access resolution will return.
- **Domain data**: `src/lib/data.ts` holds every seed array **and** the exported TypeScript types that
  define the intended API response shapes. `connectors.ts`, `channels.ts` + `channel-credentials.ts`,
  and `secrets.ts` model config/credential capture ahead of the backend.

## The `@backend` contract (how mock maps to the real backend)

Every integration point is marked inline with a `@backend` comment (257 of them; `grep -rn "@backend"
frontend/src`). Authoritative mapping and status:
- `docs/backend-integration-status.md` — **source of truth**: per-family Backend-endpoint (BUILT/DEFERRED)
  × FE-wired (MOCK/REAL) table.
- `docs/backend-integration-map.md` — index of code location → endpoint/contract.
- `docs/backend-integration-plan.md` — endpoints, sequence diagrams, deployment detail.
- `docs/frontend-auth-access-plan.md` — the auth/access model (OIDC + RBAC + per-resource policy).

When wiring real calls: keep the hook/function **shapes** (`useAuth`, `RunResolution`, the exported
types in `data.ts`) identical and swap only the source — the mocks were built to be drop-in replaced.
Expect contract deltas per resource (e.g. backend `AgentOut` is a thin projection vs. the rich FE
`Agent`; `RunOut` is snake_case and metrics-only) — handle them in `adapters.ts`, not by reshaping the
UI. Markers tagged `@backend remove` (seed arrays, dev role overrides, `tagResource` normalization) are
meant to be deleted once the server owns that data.

## Backend architecture (`services/`)

The load-bearing idea, **realized in code**: **playground, published invoke, and every eval case all
call the same `runtime.invoke` / `runtime.invoke_result`** — if they diverged, drift reports would
evaluate code the endpoint never runs. Both API planes route through one `_run_invoke` body
(`api/routers/invoke.py`); `evals/runner.py` + `judge.py` call the same `invoke_result`;
`test_invoke_parity.py` asserts identical RunRecord/cost/output. `runtime/` is a **pure library** (no
FastAPI/HTTP imports — guarded by `test_runtime_purity.py`); thin callers wrap it. `invoke()` reads an
immutable `VersionSnapshot`, never a live agent row — that's how "editing an agent changes nothing live
until an eval-gated re-point" is enforced. Cost is computed from our own capability profiles
(`runtime/providers/cost.py`), never trusted from LiteLLM (transport only).

Running stack (local `deploy/compose.yaml`): FastAPI `api` over Postgres+pgvector, PgBouncer, Redis,
and LocalStack (secretsmanager + S3). Designed-but-unbuilt: arq/RQ `worker`, Next.js `web` in the
stack, LangGraph orchestration, and the RAG/guardrails/cache layers.

**Decision-log reversals (do not re-introduce the old choice):**
- Secrets backend: **AWS Secrets Manager** (LocalStack locally), *not* Vault (feature 014).
- Object storage: **AWS S3 / LocalStack S3**, *not* MinIO (feature 022).
- Some generated diagrams under `docs/diagrams/` and a few doc bodies still say Vault/MinIO — that is
  known debt; the code and `docs/scope.md` decision log are correct.

## Where to read for intent

Read in this order: `docs/scope.md` (why/what, decision log) → `docs/spec.md` (locked requirements,
phased P1/P2 build) → `docs/backend/` (implementation-level design). The decision log in `scope.md` and
the delta table in `backend/00-overview.md` record *why* things are the way they are — check them
before reversing a choice.
