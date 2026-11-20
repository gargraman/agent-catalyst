# Feature Specification: RBAC Enforcement Slice (M5)

**Feature Branch**: `feat/backend-m0-foundation` (shared working branch; no per-feature branch hook)

**Created**: 2026-11-06

**Status**: Draft

**Input**: User description: "M5 RBAC enforcement slice — enforce the workspace role→permission model server-side (constitution P3: deterministic, fail-closed access control in code, not model/UI). Mirror the frontend permission model (generated from permissions.ts), gate every privileged endpoint by the required permission, deny fail-closed, source the role from the resolved principal claim; defer per-resource access policy and real OIDC/member-sourced roles."

## Context & Problem Statement

The thin API (feature 005) authorizes **workspace membership only**: `resolve_workspace` returns 403 if the caller has no claim on the selected workspace, but it **ignores the role value** in the claim. Consequently **every member role — including `viewer` — can currently perform every operation**, including create, publish, delete, member management, and settings changes. For an internal *enterprise* pilot this is the single largest "not actually enterprise-ready" gap: roles are modeled in the frontend (`permissions.ts`) and stored on members (feature 006), but nothing enforces them server-side.

This feature adds the **server-side authorization layer**: a permission model mirrored from the frontend's authoritative `permissions.ts`, and a per-endpoint permission requirement enforced **deterministically and fail-closed** (constitution P3 — access control lives in code, never in model instructions or the UI; the frontend `<Gate>` is only a preview). Workspace tenancy isolation (RLS, feature 005) is preserved and composed with the role check as defense in depth (P5).

**Explicitly out of scope (deferred, owner-confirmed 2026-11-06):**
- **Per-resource access policy** (restrict a specific agent/KB/tool to roles+teams, `access-policy.ts`) — a larger grants/teams model; its own later M5 feature.
- **Real role provisioning** — OIDC-sourced identity and members-table/IdP-sourced roles. This slice enforces the role already resolved into the principal claim (the dev-stub today); wiring the role's *origin* is deferred.
- **Provider-key custody (Vault)** — `key.manage` is in the permission model but no key endpoint exists yet (deferred M5).

## Clarifications

### Session 2026-11-06

- Q: Where does the caller's role come from for this slice? → A: The **resolved principal claim** (`workspace_claims[ws]`, dev-stub header today / OIDC later). Reading the role from the members table or IdP is deferred.
- Q: Is per-resource access policy (public/restricted by role+team) in scope? → A: **No — deferred** to a separate M5 feature. This slice is workspace-level role→permission RBAC only.
- Q: How is the server permission map kept from drifting from `frontend/src/lib/permissions.ts`? → A: The server map is **generated from `permissions.ts`** by a Python generator (parses the `BUILDER`/`PUBLISHER`/`ADMIN` arrays + the `ROLE_PERMISSIONS` literal, resolving the spreads — no Node needed), and a **drift test** fails if the committed server map diverges from the TS source.
- Q: Deny-by-default or opt-in per route? → A: **Deny-by-default.** Every workspace-scoped (`/api/v1`) route MUST declare a required permission, and a **coverage test** fails if any such route is unannotated — fail-closed by construction (a future route without a permission is locked down, never silently membership-only).
- Q: Does the role check (403) run before or after the resource-existence check (404)? → A: **Role check first** — permissions are role-level, so deny on role before loading the resource. An under-privileged caller gets `403` whether or not the resource exists (no existence leak, P5), and denied calls do no wasted DB work.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Privileged operations are gated by role (Priority: P1)

A workspace has members at different roles. A `viewer` must not be able to create or change anything; a `builder` can build/configure/eval but must not publish, delete, manage members, or change workspace settings; a `publisher` adds publishing (and tool approval) but still cannot manage members/settings; an `admin` can do everything. Each privileged API operation requires a specific permission, and a caller whose role lacks it is rejected **before any side effect** with `403 access.denied`.

**Why this priority**: This is the core gap (today every role can do everything) and the biggest enterprise-readiness risk. Deterministic, fail-closed enforcement is constitution P3.

**Independent Test**: With the test principal set to each role in turn, call representative operations at each tier and assert the allow/deny matrix: viewer→create = 403; builder→publish = 403; builder→add-member = 403; publisher→add-member = 403; admin→add-member = 201; etc. No mutation occurs on a denied call.

**Acceptance Scenarios**:

1. **Given** a `viewer` caller, **When** they POST an agent (or any create/edit), **Then** the response is `403 access.denied` and no row is created.
2. **Given** a `builder` caller, **When** they POST `…/publish`, **Then** the response is `403 access.denied` (publish requires `agent.publish`, publisher+); no publish occurs.
3. **Given** a `builder` caller, **When** they add a member or PATCH workspace settings, **Then** `403 access.denied` (admin-only).
4. **Given** a `publisher` caller, **When** they add a member, **Then** `403 access.denied`; **When** they POST `…/publish`, **Then** it succeeds.
5. **Given** an `admin` caller, **When** they perform any operation in the model, **Then** it is authorized (subject to all other rules).
6. **Given** any denied call, **When** it is rejected, **Then** rejection happens before the DB mutation/side effect and the error envelope is the standard `access.denied` naming the required permission (no sensitive leak).

---

### User Story 2 - Least-privilege reads preserved; unknown roles fully denied (Priority: P1)

Enforcement must not over-block legitimate access: any member whose role includes `view` can read workspace resources, and `playground.use` holders can invoke the playground. Conversely, a caller whose claimed role is unrecognized/empty has **no** permissions and is denied everything (fail-closed) — a malformed or future role never silently grants access.

**Why this priority**: A correct RBAC layer is defined as much by what it still allows as by what it blocks; and fail-closed handling of unknown roles is the P3 safety property.

**Independent Test**: viewer can GET/list agents, runs, metrics (200) and POST invoke (`playground.use`); viewer POST-create = 403. A principal with role `"superadmin"` (not in the model) is denied every operation including reads (403), proving fail-closed.

**Acceptance Scenarios**:

1. **Given** a `viewer` caller, **When** they GET/list any readable resource, **Then** it succeeds (200); **When** they use the playground invoke, **Then** it is authorized.
2. **Given** a caller whose claimed role is not in {viewer,builder,publisher,admin}, **When** they call any endpoint (read or write), **Then** `403 access.denied` (fail-closed — unknown role grants nothing).
3. **Given** enforcement is active, **When** a caller has a valid role but acts in a workspace they hold no claim for, **Then** the existing workspace authorization still returns 403 (role enforcement composes with, never replaces, tenancy).

---

### User Story 3 - Server permission model stays in lockstep with the frontend (Priority: P2)

The server's role→permission map must not drift from the authoritative `frontend/src/lib/permissions.ts`. The map is generated from that source, and a check fails if the committed server map diverges — so the frontend `<Gate>` preview and the server enforcement can never silently disagree.

**Why this priority**: Drift between the UI's preview and the server's enforcement is a classic RBAC defect (UI shows a button the server rejects, or hides one the server allows). Keeping a single source of truth upholds P3 honesty. P2 because US1/US2 already enforce correctly for the current map; this guards future changes.

**Independent Test**: A test parses `permissions.ts` (the four role arrays + `ROLE_PERMISSIONS`, resolving spreads) and asserts the server's `ROLE_PERMISSIONS` equals it, role-for-role and permission-for-permission; mutating either side makes the test fail.

**Acceptance Scenarios**:

1. **Given** the generated server map, **When** compared to `permissions.ts`, **Then** every role's permission set matches exactly (no missing/extra permissions).
2. **Given** a change to `permissions.ts` without regenerating, **When** the drift test runs, **Then** it fails with a clear diff.

---

### Edge Cases

- **No role claim for the selected workspace**: unchanged — `403 access.denied` from the existing workspace authorization (membership) before the permission check.
- **Unrecognized / empty / null role string**: zero permissions → denied everything (fail-closed), never treated as a high privilege.
- **Read-only endpoints**: require `view`; a role without `view` (none in the current model, but a future custom role) is denied reads too.
- **Publish vs eval**: running an eval (`eval.run`, builder+) is distinct from publishing (`agent.publish`, publisher+); a builder can eval but not publish.
- **Idempotency/If-Match/validation ordering**: a 403 for insufficient role takes precedence over body validation and must not perform the idempotency-guarded side effect.
- **403 before 404**: an under-privileged caller targeting a specific resource is denied (403) before resource load — the response does not distinguish existing vs missing resources (no existence leak).
- **New route added without a permission**: denied by default (the coverage test fails in CI), never silently falling through to membership-only.
- **Tenancy composition**: a cross-workspace attempt is still blocked by workspace authorization + RLS; the role check never widens cross-tenant visibility.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST define a server-side permission model with the four roles `viewer < builder < publisher < admin` and the permission set per role, mirrored exactly from `frontend/src/lib/permissions.ts`.
- **FR-002**: The system MUST map each privileged API operation to the specific permission it requires (the operation→permission table below is the contract).
- **FR-003**: A request whose caller role lacks the required permission MUST be rejected with `403 access.denied` **before** any state mutation or side effect.
- **FR-004**: Authorization MUST be fail-closed: an unrecognized, empty, or null role resolves to zero permissions and is denied every operation.
- **FR-005**: Read/list/get operations MUST require `view`; the playground/invoke operation MUST require `playground.use`.
- **FR-006**: Build/configure/eval operations MUST require their builder-tier permission (`agent.create`, `agent.edit`, `eval.run`, `knowledge.manage`, `tool.manage`, `mcp.manage`, `skill.manage`).
- **FR-007**: Publishing MUST require `agent.publish` and tool-approval MUST require `tool.approve` (publisher+); delete/keys/members/access/settings/eval-governance MUST require the admin-tier permission (`agent.delete`, `key.manage`, `member.manage`, `access.manage`, `settings.manage`, `eval.govern`).
- **FR-008**: The role MUST be sourced from the resolved principal's workspace claim; the permission check MUST compose with (not replace) the existing workspace membership authorization and RLS tenancy (P5).
- **FR-009**: The `403 access.denied` envelope MUST follow the standard error contract (code, message, and a detail naming the required permission) without leaking cross-tenant or sensitive information.
- **FR-010**: The server permission map MUST be generated from `permissions.ts` (parsing the role arrays + `ROLE_PERMISSIONS`, resolving the `…BUILDER`/`…PUBLISHER` spreads), and a drift check MUST fail if the committed server map diverges from the TS source.
- **FR-011**: Per-resource access policy (role+team visibility) MUST remain out of scope and unimplemented in this feature (documented deferral, not silent omission).
- **FR-012**: Negative authorization tests MUST exist for every tier boundary (viewer↛write, builder↛publish/delete/members/settings, publisher↛members/settings, admin→allowed) — the P3/P5 analogue of the negative cross-tenant tests.
- **FR-013**: Existing tests and callers that relied on the prior membership-only behavior MUST be updated to use role-appropriate claims; the full suite MUST pass under enforcement with no test depending on a lower role performing a higher-tier operation.
- **FR-014**: Enforcement MUST be deny-by-default: every workspace-scoped route MUST declare a required permission, and a coverage test MUST fail if any workspace-scoped route lacks a declaration (fail-closed by construction — a new route without a permission is denied, not membership-only).
- **FR-015**: The role/permission check MUST run before the target resource is loaded, so an under-privileged caller receives `403 access.denied` regardless of whether the resource exists (no existence leak, P5) and denied calls perform no resource read/mutation.

### Operation → required permission (contract)

| Operation (representative) | Permission | Min role |
|---|---|---|
| GET/list agents, tools, mcp, skills, knowledge, runs, metrics, members, workspace settings | `view` | viewer |
| POST agent invoke (SSE playground) | `playground.use` | viewer |
| POST agent eval-runs | `eval.run` | builder |
| POST agent (create) | `agent.create` | builder |
| PATCH agent / POST agent version | `agent.edit` | builder |
| tools/mcp create/patch/archive/restore | `tool.manage` / `mcp.manage` | builder |
| skills create/patch/archive/restore/publish | `skill.manage` | builder |
| knowledge-base create/patch/archive/restore | `knowledge.manage` | builder |
| POST agent publish | `agent.publish` | publisher |
| tool-call approval (when built) | `tool.approve` | publisher |
| members add/patch-role/remove | `member.manage` | admin |
| workspace settings PATCH / workspace rename | `settings.manage` | admin |
| agent delete (when built) | `agent.delete` | admin |

### Key Entities *(include if feature involves data)*

- **Role**: one of `viewer`, `builder`, `publisher`, `admin` (ordered by privilege). Sourced from the principal's workspace claim.
- **Permission**: a named capability (19 total) mirrored from `permissions.ts`; a role maps to a set of permissions.
- **Permission model (ROLE_PERMISSIONS)**: the role→permission-set mapping, generated from `permissions.ts`; the single source of truth shared (by generation) between frontend preview and server enforcement.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of privileged operations are denied (`403 access.denied`) for a caller whose role lacks the required permission, with no state change on denial.
- **SC-002**: 100% of read/playground operations succeed for a role holding `view`/`playground.use` (no over-blocking of legitimate access).
- **SC-003**: A caller with an unrecognized role is denied 100% of operations (fail-closed), including reads.
- **SC-004**: The server permission model matches `permissions.ts` for every role×permission pair (drift check green); a divergence is detected automatically.
- **SC-005**: Tenancy is preserved — an RBAC denial never reveals cross-tenant resource existence; workspace authorization + RLS still hold under enforcement.
- **SC-006**: The full test suite passes under enforcement, with the per-tier negative authorization matrix covered and existing tests updated to role-appropriate claims.
- **SC-007**: Every workspace-scoped route has a declared required permission; the coverage test detects any unannotated route (deny-by-default holds by construction).

## Assumptions

- **Scope decisions (owner-confirmed 2026-11-06):** role from the principal claim (OIDC/member-sourced role deferred); per-resource access policy deferred; server map **generated from `permissions.ts`** + a drift test (Python generator, no Node).
- The permission model and operation→permission map mirror the current `permissions.ts` and the backend design (`docs/backend/09`); where the frontend models a permission for a not-yet-built endpoint (`tool.approve`, `agent.delete`, `key.manage`), the permission exists in the model and will gate that endpoint when it is built.
- Enforcement is a server concern only; the frontend `<Gate>` remains a preview and is not modified by this feature.
- TDD with genuine behavioral RED before GREEN (P9); no new dependencies; no migration (roles are carried in the claim); `runtime/` and `evals/` untouched (purity preserved, P6).
- Work continues on `feat/backend-m0-foundation`; feature pointer `specs/008-rbac-enforcement`.
