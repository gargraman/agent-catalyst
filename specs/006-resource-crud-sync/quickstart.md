# Quickstart — Validate M4-remainder Resource CRUD + Frontend Sync

How to prove the feature works. References `contracts/api-surface.md` and `data-model.md`; does not duplicate them.

## Prerequisites

```bash
cd services
docker compose -f ../deploy/compose.yaml up -d        # Postgres (pgvector) on :5433
.venv/bin/alembic upgrade head                         # applies 0005 (registries/members/runs + RLS)
```

## Backend verification (per user story)

Run focused suites (ASGITransport in-process client; gateway/runtime mocked — no live calls):

```bash
cd services
.venv/bin/python -m pytest -q tests/test_migration_0005.py          # 0005 up/down + RLS on every new table
.venv/bin/python -m pytest -q tests/test_api_tools.py tests/test_api_skills.py        # US1 catalog CRUD
.venv/bin/python -m pytest -q tests/test_api_drift.py               # bound edit → needs_reeval; cosmetic → not
.venv/bin/python -m pytest -q tests/test_api_knowledge.py           # US3 KB metadata (ingestion deferred, honest)
.venv/bin/python -m pytest -q tests/test_api_workspaces.py tests/test_api_members.py  # US4 admin/roster
.venv/bin/python -m pytest -q tests/test_api_runs.py tests/test_api_metrics.py        # US5 runs + metrics
.venv/bin/python -m pytest -q -m integration tests/test_api_tenancy.py                # NEGATIVE cross-tenant / new table
.venv/bin/python -m pytest -q tests/test_runtime_purity.py tests/test_evals_purity.py # P6 stays green
.venv/bin/python -m pytest -q                                        # full suite + regressions (M4a green)
```

**Expected (acceptance → evidence):**
- US1: create/list/patch/archive a tool + a skill persist and round-trip; archived excluded from default list,
  present with `?include_archived=true`, still fetchable by id (SC-001, clarify #3).
- Drift: editing a bound tool's auth_mode/scopes (or a skill's content) flips `needs_reeval` on exactly the bound
  agents; a description-only edit flips none (SC-005).
- Tenancy: a workspace-B request never sees workspace-A rows for **every** new table — fail-closed (SC-002, FR-016).
- Concurrency/idempotency: stale `If-Match` → `409 conflict.stale`; in-flight `Idempotency-Key` → `409
  idempotency.in_progress` (SC-006).
- Runs/metrics: a mocked invoke persists a `runs` row (incl. a forced failure path); history lists newest-first;
  dashboard metrics reconcile with the recorded runs (SC-007).
- Secrets: an absence/secret-scan test confirms no secret value is persisted or returned by any endpoint (SC-008).

## Frontend verification (no unit harness exists — type/build + manual)

```bash
cd frontend
# FIRST: read node_modules/next/dist/docs/ before touching FE code (modified Next.js caveat)
npm install
npm run gen:api            # regenerate the typed client from the API's OpenAPI 3.1 — MUST produce zero drift
npm run lint && npm run build   # type/build gate for the swapped hooks (SC-003)
npm run dev               # manually load Agents + Tools pages against the running API → real data, not fixtures
```

**Expected:**
- `gen:api` leaves the committed `src/lib/api/` unchanged (zero drift) (SC-003).
- Swapped pages (agents list/create, tools list/create, playground send, eval-run, publish) render real persisted
  state with loading/empty/error states intact; no type errors.
- `docs/backend-integration-status.md` enumerates every `@backend` seam classified REAL/MOCK with totals that
  reconcile with `grep -rn "@backend" frontend/src` (SC-004); no mock seam labeled implemented.

## Done when

All focused suites + the full regression pass green; negative cross-tenant proven per new table; FE build/lint green
with zero client drift; tracker complete and reconciled; completion report names commands run + any unverified items
(e.g. live invoke still deferred to Phase 5).
