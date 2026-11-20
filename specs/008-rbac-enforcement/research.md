# Research & Design Decisions — RBAC Enforcement Slice (M5)

Grounded in the current on-disk API (verified 2026-11-06): `services/api/deps.py`, `api/errors.py`, all 10 routers, and `frontend/src/lib/permissions.ts`.

---

### D1 — Enforcement mechanism: a `require_permission` route dependency

**Decision**: A factory `require_permission(permission: str)` returns a FastAPI dependency; routes add it via `dependencies=[Depends(require_permission("<perm>"))]`. It depends on `session_principal` + `resolve_workspace` (header-only; no DB), reads the role from `principal.workspace_claims[ws]`, and raises `AccessDenied` unless `has_permission(role, permission)`.

**Rationale**: FastAPI resolves all route/param dependencies **before** the handler body, so a 403 fires before the handler loads/mutates any resource — satisfying FR-015 (403 before 404, no existence leak) with no special ordering code. The check is an in-memory set lookup (no DB on the hot path). Co-locating with the existing `resolve_workspace`/`session_principal` deps keeps the auth story in one place.

**Alternatives**: global middleware doing path→permission matching (rejected — brittle path matching, harder to read per-route intent); per-handler inline checks (rejected — easy to forget, no deny-by-default guarantee).

---

### D2 — Deny-by-default via a coverage test

**Decision**: `require_permission` attaches a `required_permission` attribute to the dependency callable it returns. A coverage test walks `app.routes` and asserts every route whose path starts with `/api/v1` has a dependency carrying that marker. Unannotated workspace route → test fails.

**Rationale**: FR-014/SC-007 — makes deny-by-default a structural guarantee, not a convention: a future route added without a permission is caught in CI rather than silently reverting to membership-only. Non-`/api/v1` infra routes (health/openapi/docs) are excluded.

---

### D3 — Role source: the resolved principal claim

**Decision**: role = `principal.workspace_claims[ws]` (the value `resolve_workspace` already validated membership against). No members-table read, no OIDC.

**Rationale**: clarify decision — smallest correct slice; the claim already carries the role. Member/IdP-sourced role origin is a later M5 feature. Keeps the hot path DB-free and avoids claim-vs-member precedence questions now.

---

### D4 — Permission model mirrored by generation from `permissions.ts`

**Decision**: `scripts/gen_rbac.py` parses `frontend/src/lib/permissions.ts` and emits `services/api/rbac_permissions.json` = `{role: [permissions...]}`. `api/permissions.py` loads that JSON into `ROLE_PERMISSIONS` and exposes `has_permission(role, perm)`. A drift test re-runs the parse in-memory and asserts the committed JSON matches.

**Parser contract** (no Node): strip `//` line comments; capture each `const NAME: Permission[] = [ ... ];` (NAME ∈ {BUILDER, PUBLISHER, ADMIN}) and the `export const ROLE_PERMISSIONS … = { … };` object; tokens are string literals (`"agent.edit"`) and spread refs (`...BUILDER`); resolve spreads recursively; preserve order, dedupe. Produce the four role arrays. Fail loudly if a role is missing or a referenced const/permission is unknown.

**Rationale**: clarify decision ("generate from permissions.ts"). Gives a single source of truth without a Node toolchain; the drift test guarantees the committed artifact tracks the TS source (FR-010/SC-004).

**Alternatives**: hand-written mirror + parity test (simpler, but the map isn't *derived* — rejected per owner choice); runtime TS parsing on import (rejected — couples the server to the frontend file at runtime, fragile).

---

### D5 — Operation→permission assignment (all 47 routes)

**Decision**: GET/list/get everywhere → `view`; `POST …/invoke` → `playground.use`; `POST …/eval-runs` → `eval.run`; agents `POST ""`→`agent.create`, `PATCH`/`POST …/versions`→`agent.edit`, `POST …/publish`→`agent.publish`; tools→`tool.manage`, mcp→`mcp.manage`, skills→`skill.manage`, knowledge→`knowledge.manage` (create/patch/archive/restore/publish); members mutations→`member.manage`, members GET→`view`; workspace `GET /settings`→`view`, `PATCH /settings`+rename→`settings.manage`; runs/metrics GET→`view`; `GET /ingestion-jobs`→`view`.

**Rationale**: mirrors `permissions.ts` tiers + `docs/backend/09`. Reads are member-wide (`view`); each mutation maps to its resource's `*.manage` or the agent-lifecycle permission; admin-plane (members/settings) is admin-only.

---

### D6 — Existing-test blast radius (expected, part of the feature)

**Decision**: tests that exercise higher-tier ops under the default `builder` claim must move to the right role: **publish** (`test_api_evals_publish`) → `publisher`; **members** (`test_api_members`) → `admin`; **workspace settings/rename** (`test_api_workspaces`) → `admin`. Builder-tier ops (agent create/edit, eval-run, tools/mcp/skills/knowledge manage, invoke, reads) keep the default builder claim. Audit all test files for higher-tier calls during implementation.

**Rationale**: FR-013 — these updates are themselves evidence enforcement works (the old tests passed only because roles were ignored).

---

### D7 — Ordering & tenancy composition

**Decision**: the permission dependency does not open a DB session; it composes with (does not replace) `resolve_workspace` membership authz and the `tenant_db` RLS session. Denial raises during dependency resolution → handler (and its resource load / 404 / mutation) never runs. A cross-workspace attempt is still blocked by membership + RLS regardless of role.

**Rationale**: FR-008/FR-015, P5 — role enforcement is additive defense in depth; it never widens cross-tenant visibility and never leaks resource existence to an under-privileged caller.
