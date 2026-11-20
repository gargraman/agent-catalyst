# Feature Specification: Per-Resource Access-Policy Enforcement

**Feature Branch**: `feat/backend-m0-foundation` (no per-feature branch; shared M-series branch)

**Created**: 2026-11-09

**Status**: Draft

**Input**: User description: "Per-resource access-policy enforcement (M5 remainder) — port the authoritative `access-policy.ts` contract to the backend and enforce it as a deterministic, fail-closed control on top of the already-built RBAC role-gate (008), membership-sourced authZ (012), and RLS tenancy, with no resource-existence leakage."

## Context & reconciliation note *(read first)*

The frontend (`frontend/src/lib/access-policy.ts`) already defines the complete, authoritative access-policy
contract and previews it client-side. The backend does **not** enforce it. Two backend-reality gaps were found
while scoping this feature and MUST be reconciled (they change the feature's size):

- **The `access_policy` column exists only on `knowledge_bases`** (`services/api/models.py`). Agents, tools,
  MCP servers, MCP tools, and skills — all of which the console gates with `access-policy.ts` — do **not** carry
  the column. Enforcing across those families requires adding the column (a migration) per family.
- **A workspace member has no team membership in the backend.** `members` stores `subject` + `console_role`
  only. The contract is role **AND** team (and an execute-only caller is gated on **team alone**). The team
  dimension cannot be enforced until member teams exist in the backend.

These two gaps are captured as the only `[NEEDS CLARIFICATION]` items below. Everything else uses informed
defaults recorded under Assumptions.

## Clarifications

### Session 2026-11-09

- Q: Which resource families enforce access policy in this feature (FR-015)? → A: **Full gated set** — agents,
  tools, MCP servers, MCP tools, skills, knowledge bases. One generic enforcement spine + a single migration
  adding the `access_policy` column to the families that lack it; uniform behavior, no partial-enforcement gaps.
- Q: How is a principal's team membership sourced (FR-016)? → A: **Teams on the member record, editable now.**
  Add a `teams` list to the workspace member (authoritative in the `members` store under RLS, exactly as role is
  since feature 012); admin/console edits set it now; IdP group→member team sync is deferred to the OIDC-login
  feature. This enables the full contract (role AND team; executer team-only) immediately.
- Q: Does teams-on-member require frontend changes, and are they in scope? → A: **Yes, FE contract-sync is in
  scope** (mock/type level, build-verified — FE remains 0/253 wired): the `Member` type + members CRUD payloads
  gain `teams`; the members admin UI gains team assignment; `iam.ts`/auth seams are documented as server-owned
  for authZ with `MOCK_PROFILE.teams` as the pre-wiring preview; `openapi.json` is regenerated. `access-policy.ts`
  stays the shared source-of-truth contract (unchanged). Real endpoint wiring and IdP team sync stay out of scope.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Restricted resource is invisible and unusable to those outside its policy (Priority: P1)

A workspace has a resource whose owner marked it **restricted** so that only a specific set of members may use
it. A member who does not match the policy must not be able to see the resource in listings, read it directly,
or use it — and must not even be able to tell that it exists. A member who matches the policy uses it normally.
A workspace **admin** can always see and use it (so an admin can never be locked out of their own workspace).

**Why this priority**: This is the headline value and the core constitutional obligation — a deterministic,
fail-closed visibility control that does not leak the existence of resources a caller is not entitled to
(Principle 5). It is independently shippable and demonstrable on its own.

**Independent Test**: Seed one restricted resource; with the existing integration harness, assert a
non-matching member gets an absent-resource response (404) on list/read/use, a matching member succeeds, and an
admin always succeeds — no 403 that would confirm existence to the non-matching member.

**Acceptance Scenarios**:

1. **Given** a resource restricted to role R / team T, **When** a member who is neither queried the list, **Then** the resource is omitted from the list.
2. **Given** that same resource, **When** the non-matching member reads it by id, **Then** the response is indistinguishable from the resource not existing (404), never a 403.
3. **Given** that same resource, **When** a member who matches the policy reads or uses it, **Then** the request succeeds.
4. **Given** that same resource, **When** a workspace admin reads or uses it, **Then** the request succeeds regardless of the policy.
5. **Given** a restricted policy that names **neither** a role **nor** a team, **When** any non-admin member accesses it, **Then** access is denied (fail closed — an empty restriction grants no one).

---

### User Story 2 - Private resource is reachable only by its owner (and admins) (Priority: P2)

An owner marks a resource **private**. Only that owner (by their identity) may use or manage it. Workspace
admins retain access (to fix or reassign). Everyone else — including execute-only callers that carry no owner
identity — is denied.

**Why this priority**: Completes the visibility model; smaller and depends on the same enforcement spine as US1.

**Independent Test**: Seed a private resource owned by subject O; assert O succeeds, a different member gets 404,
an admin succeeds, and an execute-only caller is denied.

**Acceptance Scenarios**:

1. **Given** a private resource owned by O, **When** O reads/uses/manages it, **Then** the request succeeds.
2. **Given** a private resource owned by O, **When** a different non-admin member accesses it, **Then** the response is 404 (no existence leak).
3. **Given** a private resource, **When** a workspace admin accesses it, **Then** the request succeeds.
4. **Given** a private resource, **When** an execute-only caller (no owner identity) attempts to use it, **Then** access is denied.

---

### User Story 3 - Managing a resource is gated separately from using it (Priority: P2)

Using a resource (invoke/bind) and managing it (edit/publish/archive) are distinct capabilities. A member who
is permitted to **use** a restricted resource is not thereby permitted to **manage** it; manage has its own
role/team set. A member who can see/use a resource but is denied management receives an explicit
permission-denied response (the existence is already known to them, so no 404 masking is required). Admins and
the private-resource owner may always manage.

**Why this priority**: Required for correctness of edit/publish/archive flows; builds directly on US1/US2.

**Acceptance Scenarios**:

1. **Given** a restricted resource a member may use but not manage, **When** the member edits/publishes/archives it, **Then** access is denied with a permission-denied response (403), not a 404.
2. **Given** a resource a member cannot even use (invisible to them), **When** the member attempts to manage it, **Then** the response is 404 (still no existence leak).
3. **Given** any restricted or private resource, **When** an admin manages it, **Then** the request succeeds (so an admin can always repair a broken policy).
4. **Given** the route-level role permission is absent (RBAC denies first), **When** the member attempts the operation, **Then** RBAC denial applies before access-policy evaluation.

---

### User Story 4 - Execute-only callers are gated on team alone, fail-closed (Priority: P3)

An execute-only caller (the token/channel plane — an `executer`) has no console role and no owner identity. It
is evaluated against a resource's policy on **team membership alone**: a policy that names only roles (no team)
denies the caller; a policy naming a team the caller belongs to permits use; private resources deny all callers.

**Why this priority**: Defines how the (future) token/channel plane is evaluated against policies. This feature
only defines and tests the *evaluation rule* for an `executer`; it does **not** build token issuance (next
feature).

**Acceptance Scenarios**:

1. **Given** a restricted resource with a role-only policy (no team), **When** an executer caller uses it, **Then** access is denied.
2. **Given** a restricted resource naming team T, **When** an executer caller in team T uses it, **Then** access is permitted.
3. **Given** a restricted resource naming team T, **When** an executer caller not in team T uses it, **Then** access is denied.
4. **Given** a private resource, **When** any executer caller uses it, **Then** access is denied.

---

### Edge Cases

- A resource with **no policy / absent policy** is treated as `workspace` visibility (every member may use & manage) — the documented default.
- An **empty restricted** policy (no roles, no teams, on the use or manage side) grants **no one** on that side (fail closed), except admins/owner.
- A **private** resource whose stored owner is missing/blank is reachable by admins only (no non-admin can satisfy owner match) — fail closed.
- An **unknown/garbage visibility value** is treated as the most restrictive interpretation (deny non-admins), never as open.
- Access-policy evaluation runs **after** tenancy (RLS) and **after** the route's RBAC permission check — all three must pass; a cross-tenant resource is already invisible via RLS and never reaches policy evaluation.
- The existence-leak rule is anchored on **USE visibility**: if a principal cannot use (see) a resource, every denial about it is a 404; if they can see it but not manage it, management denial is a 403.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST treat `workspace` visibility (and any absent policy) as "every workspace member may use and manage," preserving today's behavior for ungoverned resources.
- **FR-002**: The system MUST permit **use** of a `restricted` resource only to a console principal whose role is allowed (role list empty = unconstrained on role) **and** whose team is allowed (team list empty = unconstrained on team), evaluated as a logical AND.
- **FR-003**: The system MUST gate **manage** (edit/publish/archive) of a `restricted` resource with its own, separate role/team lists, using the same AND rule as FR-002.
- **FR-004**: The system MUST deny all non-admin/non-owner principals when a `restricted` policy names neither a role nor a team on the evaluated side (fail closed; an empty restriction is never a no-op open grant).
- **FR-005**: The system MUST permit use and manage of a `private` resource only to the resource's owner (by identity) and to workspace admins.
- **FR-006**: The system MUST allow a workspace **admin** to use and manage any resource in their workspace regardless of its access policy.
- **FR-007**: The system MUST evaluate an execute-only caller (`executer`, no console role, no owner identity) against a policy on **team membership alone**: deny on a role-only policy, permit on a matching team, and deny all `executer` callers for `private` resources.
- **FR-008**: The system MUST NOT leak the existence of a resource a principal cannot use: list omission, and a 404 (indistinguishable from absent) on direct read and on management attempts for resources the principal cannot use.
- **FR-009**: The system MUST return a permission-denied (403) — not a 404 — when a principal can use (see) a resource but is denied management of it.
- **FR-010**: The system MUST deny **use** on the invoke/bind path fail-closed when the policy does not permit the caller, before any provider call or side effect.
- **FR-011**: Access-policy enforcement MUST compose with, not replace, the existing controls: RLS tenancy applies first, the route's RBAC permission check applies next, then access policy — all three must pass (logical AND). Admin bypass applies to the access-policy layer only (RBAC already grants admins all permissions).
- **FR-012**: When a resource is created with `private` visibility, the system MUST record the creating principal's identity as the owner (default owner assignment), so a private resource is never ownerless at creation.
- **FR-013**: The backend MUST become the authoritative evaluator of the access-policy contract; the frontend preview (`access-policy.ts`) MUST produce the same allow/deny decision for the same policy + principal (contract parity), so the console preview never contradicts the server.
- **FR-014**: Access decisions (allow/deny, capability, visibility tier, resource id, principal subject) MUST be observable for audit without logging any secret or token material.
- **FR-015**: The system MUST enforce access policy on the read/list/use/manage paths of the **full gated set** of resource families: agents, tools, MCP servers, MCP tools, skills, and knowledge bases. Families that do not yet carry an `access_policy` field MUST gain one (default `workspace`) so enforcement is uniform; no in-scope family is left ungoverned.
- **FR-016**: A principal's team membership (required by FR-002/FR-003/FR-007) MUST be sourced from the workspace **member record** under RLS (a `teams` list on the member, authoritative exactly as `console_role` is since feature 012), settable via admin/console member edits. IdP group→member team synchronization is out of scope (deferred to the OIDC-login feature); until then teams are administered directly.
- **FR-017**: The frontend contract MUST stay aligned with the server (mock/type level, build-verified — FE remains unwired) **without contradicting the console's existing IdP-managed, read-only identity model** (`settings/page.tsx` states the roster is "Managed in {IdP}", read-only; `iam.ts` states "NO app-managed roster"). Concretely: the console `Member` type and the backend `MemberOut`/create/update schemas MUST carry `teams`; the read-only roster MAY display teams; `iam.ts`/auth seams MUST document teams as **server-owned for authZ** (with `MOCK_PROFILE.teams` as the pre-wiring preview); and the emitted OpenAPI document MUST include the member `teams` field. Teams are administered via the members **API** (admin/backend/seed) now and IdP group→team sync later — **this feature MUST NOT add an in-console team-editing UI** (that would reverse the deliberate read-only, IdP-managed design; a future owner decision could change it). The shared `access-policy.ts` contract remains the single decision source the backend mirrors (field names included — see FR-018), changed only if a genuine divergence is found.
- **FR-018**: The persisted and API-exposed `access_policy` object MUST use the **exact field names and shape** of `access-policy.ts` `AccessPolicy` (`visibility`, `useRoles`, `useTeams`, `manageRoles`, `manageTeams`, `owner`) so the frontend and backend share one shape with no translation layer (parity, FR-013). The backend MUST expose it as a typed contract (not an opaque object) so the generated client is typed.

### Key Entities *(include if feature involves data)*

- **Access Policy** (embedded on a resource): a visibility tier (`workspace` | `restricted` | `private`); for `restricted`, separate use and manage role/team allow-lists; for `private`, an owner identity. Default is `workspace`.
- **Console Principal**: an authenticated workspace member — identity (subject), a console role, and team memberships. Used for console reads/manage.
- **Execute-only caller (`executer`)**: a runtime caller with team memberships but no console role and no owner identity. Used on the invoke/bind path. (Token issuance for this plane is out of scope — next feature.)
- **Member**: the existing workspace roster entry (subject, console role) — extended with a **teams** list (server-authoritative under RLS, per FR-016), administered via member edits.
- **Governed resource**: a resource in any of the in-scope families (agents, tools, MCP servers, MCP tools, skills, knowledge bases — FR-015) that carries an access policy and whose read/list/use/manage paths enforce it.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For every in-scope resource family, 100% of restricted/private resources that a principal is not entitled to use are absent from that principal's list results and return an absent-resource response (404) on direct access — zero existence leaks (no 403 that confirms existence).
- **SC-002**: A workspace admin can use and manage 100% of resources in their workspace, including ones with an empty or malformed restricted policy (admin can always repair).
- **SC-003**: Every allow/deny decision made by the backend matches the frontend preview's decision for the same policy and principal across the full decision table (workspace/restricted/private × use/manage × console/executer), verified by a shared decision-table test.
- **SC-004**: 100% of invoke/bind attempts a caller is not entitled to make are denied before any provider call or state change (fail closed), with no partial side effects.
- **SC-005**: All negative cross-principal scenarios in User Stories 1–4 pass under the existing integration harness via named commands, and the runtime import-purity invariant (`runtime/` imports no HTTP/web framework) remains green.
- **SC-006**: The frontend builds and lints clean with the `teams`-bearing `Member` contract and the members team-assignment UI, and the emitted OpenAPI document includes the member `teams` field (contract parity, FR-017).

## Assumptions

- **Owner on create**: a resource created as `private` takes the creating principal's subject as owner (FR-012). Changing visibility to `private` later without an explicit owner defaults the owner to the editor's subject.
- **Admin definition**: "admin" is the existing console admin role (RBAC B2 role model); admin bypass is scoped to the workspace (never cross-tenant — RLS still binds).
- **No new HTTP surface for policy editing in this feature**: resources already accept their `access_policy` on create/update via their existing endpoints (knowledge bases today; others once they carry the column). This feature enforces policy; it does not add a dedicated policy-management API.
- **`executer` evaluation is defined and tested here, but no token is issued** — the token/channel plane (mint + deny-set) is the next feature; tests exercise the evaluation rule with a synthesized executer principal.
- **Reuse existing infrastructure**: enforcement reuses the current per-request principal/membership resolution (deps), RLS tenant session, RBAC permission dependency, and the integration test harness (ASGI client + tenant session).
- **Contract source of truth**: the three-tier semantics, fail-closed matching, executer team-only rule, private-owner rule, and admin bypass are taken verbatim from `frontend/src/lib/access-policy.ts`.
- **Teams are per-workspace**: a member row is workspace-scoped (RLS), so a member's `teams` are their teams *within that workspace*; a user in two workspaces has two member rows with independent teams. Team names mirror the console's functional groups (`iam.ts` `TEAMS`).
- **Column rollout is mechanical**: adding `access_policy` (default `workspace`) to the families that lack it is a backfill-safe migration; existing rows become `workspace` (unchanged behavior).
- **Non-goal — bind-time/runtime sub-resource gating**: this feature enforces access policy on each family's own API surface (list/read/manage) and on the agent **use** paths (**invoke and eval-run**, which both call `runtime.invoke`). It does NOT yet evaluate the agent-as-caller against a *bound* tool/MCP-tool/knowledge-base policy at configure or runtime; that is deferred (the `executer` rule defined here is the hook for it).
- **Owner on edit-to-private**: flipping a resource to `private` via edit with no explicit owner defaults the owner to the editor's subject (same rule as create — FR-012), so an edit can never produce an ownerless-and-unreachable (non-admin) resource.
