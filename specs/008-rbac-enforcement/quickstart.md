# Quickstart — Verify RBAC Enforcement (M5)

Run and validate feature 008. Permission/parity/coverage tests are pure (no DB); the authz matrix uses the compose Postgres (RLS still applies). No live/paid calls.

## Prerequisites

- `cd services`; venv at `services/.venv`.
- Compose Postgres (host port 5433) up for the integration matrix + full regression.
- `frontend/src/lib/permissions.ts` present (the permission source).

## Regenerate the permission map (when permissions.ts changes)

```bash
cd services
.venv/bin/python scripts/gen_rbac.py          # parses ../frontend/src/lib/permissions.ts → api/rbac_permissions.json
```

## Run the feature's tests

```bash
cd services
# Foundational: generator + parity/drift + has_permission (pure)
.venv/bin/python -m pytest tests/test_rbac_model.py -q
# Deny-by-default coverage (pure — introspects app.routes)
.venv/bin/python -m pytest tests/test_api_rbac_coverage.py -q
# Per-tier allow/deny matrix (compose Postgres)
.venv/bin/python -m pytest tests/test_api_rbac_matrix.py -q
# Purity must stay green; full regression (updated role claims)
.venv/bin/python -m pytest tests/test_runtime_purity.py -q
.venv/bin/python -m pytest -q
```

## What each check proves (acceptance → verification)

| Story | Check | Expected |
|---|---|---|
| US1 | viewer POST create / builder POST publish / builder add-member / publisher add-member | `403 access.denied`; no mutation (AC1–AC4) |
| US1 | admin add-member / publisher publish | success (AC4–AC5) |
| US1 | denied call | 403 before resource load; no row created/changed (AC6, FR-015) |
| US2 | viewer GET/list + playground invoke | success (AC1) |
| US2 | role `"superadmin"` (unknown) on any endpoint | `403` — fail-closed (AC2, SC-003) |
| US2 | valid role, unheld workspace | `403` membership (AC3) — tenancy composes |
| US3 | server `ROLE_PERMISSIONS` vs fresh `permissions.ts` parse | exact match (SC-004) |
| US3 | every `/api/v1` route | has a declared permission (SC-007, deny-by-default) |
| Polish | full suite with updated claims | green; purity green (P6) |

## TDD note (P9)

Negative authz is genuine behavioral RED: e.g. assert a `viewer` POSTing an agent gets `403` — this **fails today** (membership-only lets any role create), proving a real behavioral gap, then passes once `require_permission` is wired. Likewise `builder`→publish `403` fails until publish is annotated `agent.publish`.
