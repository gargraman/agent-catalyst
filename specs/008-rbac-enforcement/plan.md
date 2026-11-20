# Implementation Plan: RBAC Enforcement Slice (M5)

**Branch**: `feat/backend-m0-foundation` (shared; no per-feature branch hook) | **Date**: 2026-11-06 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/008-rbac-enforcement/spec.md`

## Summary

Add a server-side authorization layer to the thin API so the workspace role→permission model is enforced deterministically and fail-closed (constitution P3). Today `deps.resolve_workspace` authorizes membership but ignores the claimed role, so every role can do everything. This feature: (1) mirrors the authoritative `frontend/src/lib/permissions.ts` into a **generated** server permission map (+ a drift test); (2) adds a `require_permission(...)` FastAPI dependency and applies it to **all 47 workspace-scoped routes**; (3) enforces **deny-by-default** via a coverage test (any unannotated `/api/v1` route fails the test); (4) checks the role **before resource load** (403 before 404, no existence leak). Role is sourced from the resolved principal claim; per-resource access policy, OIDC/member-sourced roles, and Vault key custody stay deferred.

## Technical Context

**Language/Version**: Python 3.12.13

**Primary Dependencies**: existing only — FastAPI (dependencies), the `api/errors.py` envelope (`AccessDenied`=403), `api/deps.py` (`session_principal`, `resolve_workspace`). **No new dependencies.**

**Storage**: none new — **no migration**. Roles are carried in the principal claim; the members table (006) is untouched by this slice (member-sourced roles deferred).

**Source of truth**: `frontend/src/lib/permissions.ts` (`ROLE_PERMISSIONS`, 4 roles × 19 permissions). The server map is **generated** from it (Python parser, no Node) → a committed data file loaded at import; a drift test re-runs the generator and asserts equality.

**Testing**: `pytest` + `pytest-asyncio`; httpx `ASGITransport` in-process client; `dev_headers(claims={ws: role})` sets the caller role per test. Integration tests on compose Postgres (RLS still applies); the generator/parity/coverage tests are pure (no DB).

**Target Platform**: Linux server (FastAPI `api`). **Project Type**: backend service — API-layer-only change.

**Performance/Constraints**: permission check is an in-memory set lookup on the hot path (no DB). Deny-by-default by construction (P3); 403 before resource load (no existence leak, P5); tenancy/RLS preserved and composed with the role check (P5); `runtime/` + `evals/` untouched (purity, P6).

**Scale/Scope**: 47 routes annotated; internal pilot.

## Constitution Check

*GATE: Must pass before Phase 0. Re-checked after Phase 1.*

| Principle | Gate | Status |
|---|---|---|
| **P1 Honesty** | Deferrals documented, no mock-as-real | ✅ access-policy / OIDC-member-roles / Vault `key.manage` endpoint explicitly deferred; `<Gate>` remains a preview. |
| **P2 Scope/phases** | Fits phased plan | ✅ First bounded M5 slice (RBAC enforcement) ahead of OIDC/Vault/access-policy. |
| **P3 Deterministic fail-closed controls** | Authz in code, fail-closed | ✅ **This is the feature.** Deny-by-default + coverage test; unknown role → zero perms → deny. |
| **P4 Judge/evidence** | n/a | ✅ No eval/judge change (eval.run/eval.govern are just gated). |
| **P5 Tenancy/data** | Isolation preserved, no leak | ✅ Role check composes with membership + RLS; 403 before 404 (no existence leak). |
| **P6 Pure runtime** | runtime/ web-free | ✅ Change is entirely in `api/`; `runtime/`+`evals/` untouched; purity test stays green. |
| **P7 Cost honesty** | n/a | ✅ No cost path change. |
| **P8 Observable failure** | Honest errors | ✅ Standard `403 access.denied` envelope naming the required permission; no sensitive leak. |
| **P9 Genuine RED before GREEN** | Behavioral RED | ✅ Negative authz matrix written RED-first (deny expected, currently allowed). |

**No violations.** Complexity Tracking omitted.

## Project Structure

### Documentation (this feature)

```text
specs/008-rbac-enforcement/
├── spec.md            # specify + clarify
├── plan.md            # this file
├── research.md        # Phase 0 — decisions D1–D7
├── data-model.md      # Phase 1 — Role/Permission/map + operation→permission table + generator contract
├── quickstart.md      # Phase 1 — run/verify
├── contracts/
│   └── rbac.md        # the authorization contract (operation→permission, 403 semantics, deny-by-default)
├── checklists/requirements.md
└── tasks.md           # (/speckit-tasks)
```

### Source Code (repository root)

```text
services/
├── api/
│   ├── permissions.py          # NEW: Role, Permission, ROLE_PERMISSIONS (loaded from generated data), has_permission()
│   ├── rbac_permissions.json   # NEW (generated): the role→permission data, generated from permissions.ts
│   ├── deps.py                 # MODIFIED: + require_permission(permission) dependency factory (reads principal+ws, no DB)
│   └── routers/                # MODIFIED (all 10): add dependencies=[Depends(require_permission("<perm>"))] per route
│       ├── agents.py  invoke.py  evals.py  tools.py  skills.py
│       └── knowledge.py  workspaces.py  members.py  runs.py
├── scripts/
│   └── gen_rbac.py             # NEW: parse frontend/src/lib/permissions.ts → emit rbac_permissions.json (no Node)
└── tests/
    ├── test_rbac_model.py          # NEW: generator + parity/drift vs permissions.ts; has_permission unit
    ├── test_api_rbac_matrix.py     # NEW: per-tier allow/deny matrix across representative endpoints (US1/US2)
    ├── test_api_rbac_coverage.py   # NEW: deny-by-default — every /api/v1 route declares a permission
    ├── _api_helpers.py             # MODIFIED: dev_headers already supports claims; add role helpers if needed
    └── test_api_{evals_publish,members,workspaces,...}.py  # MODIFIED: use publisher/admin claims for higher-tier ops
```

**Structure Decision**: API-layer-only. The permission *model* lives in `api/permissions.py` (data generated from the frontend source); the *enforcement* is one `require_permission` dependency applied per route. No change to `runtime/`, `evals/`, the DB schema, or dependencies.

## Enforcement design (key decisions — see research.md)

- **`require_permission(permission)`** returns a dependency that depends on `session_principal` + `resolve_workspace` (both header-only, no DB), reads `principal.workspace_claims[ws]` → role, and raises `AccessDenied` (403) unless `has_permission(role, permission)`. Because it resolves as a route dependency **before the handler body**, the handler's resource load/mutation never runs on denial (FR-015: 403 before 404, no existence leak). The returned callable carries a `required_permission` attribute for coverage introspection.
- **Deny-by-default coverage**: a test walks `app.routes`; for every route whose path starts with `/api/v1`, it asserts a `require_permission` dependency is present (via the `required_permission` marker). A new route without one fails the test (FR-014/SC-007).
- **Generated map (no drift)**: `gen_rbac.py` parses `permissions.ts` — strips `//` comments, captures each `const BUILDER|PUBLISHER|ADMIN: Permission[] = [...]`, and the `ROLE_PERMISSIONS = {…}` object, resolving `...SPREAD` references — and writes `rbac_permissions.json`. `permissions.py` loads it. `test_rbac_model.py` re-runs the parse in-memory and asserts the committed JSON matches (FR-010/SC-004).
- **Operation→permission map**: the full 47-route assignment is in data-model.md; summary — GET/list → `view`; invoke → `playground.use`; eval-runs → `eval.run`; agent create → `agent.create`, patch/version → `agent.edit`, publish → `agent.publish`; tools/mcp/skills/knowledge mutations → their `*.manage`; members → `member.manage`; workspace settings/rename → `settings.manage`.

## Phased delivery (maps to spec stories; each TDD RED→GREEN)

- **Foundational** — `permissions.py` model + `gen_rbac.py` generator + `rbac_permissions.json` + `require_permission` dep (RED: parity/has_permission tests).
- **US1 (P1)** — annotate all mutating/privileged routes; negative matrix (viewer↛write, builder↛publish/delete/members/settings, publisher↛members/settings, admin→allowed). RED first (current code allows → fail), then annotate → GREEN. Update existing higher-tier tests to publisher/admin claims.
- **US2 (P1)** — annotate read/invoke routes with `view`/`playground.use`; tests: viewer reads/plays OK; unknown role denied everything (fail-closed).
- **US3 (P2)** — deny-by-default coverage test + drift test green; wire `gen_rbac` into CI check (doc).
- **Polish** — full regression (updated claims), purity, docs reconcile (`docs/backend/09` + implementation-plan M5 banner).

## Complexity Tracking

*No constitution violations — intentionally empty.*
