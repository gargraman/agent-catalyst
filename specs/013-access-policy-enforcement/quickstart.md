# Quickstart / Validation: Per-Resource Access-Policy Enforcement

Prerequisites: the dev compose stack up (Postgres on host 5433), migrations applied, services venv.

```bash
# from repo root
docker compose -f deploy/compose.yaml up -d postgres redis
cd services
.venv/bin/python -m alembic upgrade head        # applies 0006 (access_policy + member teams)
```

## 1. Unit — the decision table mirrors access-policy.ts (fast, no DB)

```bash
cd services && .venv/bin/python -m pytest -q tests/test_access_policy_eval.py
```
Asserts `can_use`/`can_manage` against every row of `contracts/access-policy-evaluation.md` (admin bypass,
empty-restriction fail-closed, executer team-only, private-owner, malformed-visibility). This is the FR-013/SC-003
parity artifact.

## 2. Integration — negative cross-principal, no existence leak (needs DB)

```bash
cd services && .venv/bin/python -m pytest -q -m integration tests/test_api_access_policy.py
```
Covers, across the in-scope families (agents, tools, mcp, skills, knowledge bases):
- restricted resource **omitted** from a non-matching member's list; **404** on direct GET; **404** on PATCH —
  never 403 (SC-001, no leak);
- matching member → 200/success;
- admin → success even on an empty/malformed policy (SC-002);
- private resource reachable only by owner + admin;
- invoke denied with **404** and **no Run row / no provider call** for a non-matching caller (SC-004);
- visible-but-unmanageable member → GET 200, PATCH **403**.

## 3. Members teams round-trip + RLS

```bash
cd services && .venv/bin/python -m pytest -q -m integration tests/test_api_members_teams.py
```
Create/patch a member with `teams`, read it back; confirm teams are workspace-scoped (RLS) and drive restricted
access.

## 4. Purity + full regression

```bash
cd services && .venv/bin/python -m pytest -q tests/test_runtime_purity.py      # runtime/ imports no web framework (P6)
cd services && .venv/bin/python -m pytest -q                                   # full suite, no regressions
```

## 5. Frontend contract-sync (build-verified; FE stays unwired)

```bash
cd frontend
nvm use 24 && npm install
npm run gen:api            # regenerate typed client incl. member.teams (after openapi.json is re-emitted)
npm run lint && npm run build   # zero type errors with the teams-bearing Member contract + members UI (SC-006)
```
> Re-emit `frontend/openapi.json` from the running app first: `services/.venv/bin/python scripts/emit_openapi.py`.
> Read `frontend/node_modules/next/dist/docs/` before editing any Next.js code (modified Next.js 16.3.8).

## Expected outcome

All pytest commands green; FE build/lint clean; no resource-existence leak in any negative scenario; `runtime/`
purity intact.
