# Feature Specification: M4-remainder — Resource CRUD + Frontend Contract Sync

**Feature Branch**: `006-resource-crud-sync`

**Created**: 2026-11-05

**Status**: Draft

**Input**: User description: "m4-remainder/ M5. also make sure the apis that are implemented in backend, the frontend code is also updated. to keep track of remaining implementation"

## Overview

The thin API slice (feature 005 / M4a) made six surfaces real — agents create/get/list/PATCH, version snapshot, advisory publish, inline eval-run, and streaming invoke — all workspace-scoped through row-level-security. Everything else the product console offers (the reusable **tool catalog**, **skills**, **knowledge bases**, **workspace administration**, and **run history / metrics**) is still mock-only in the frontend, backed by seed fixtures and in-memory store mutations.

This feature widens the real backend to cover the remaining **plain create/read/update/archive resources** so a builder can assemble, configure, and operate an agent against real stored data instead of fixtures — and wires a **representative slice of the frontend** to those endpoints so the drop-in contract is proven, with a **living tracker** that records, per resource, what is backed by a real API versus still mock. The security-heavy slice (fine-grained role enforcement, real identity provisioning, secret custody, secret-bearing resources) is explicitly **not** in scope and remains deferred.

## Clarifications

### Session 2026-11-05 (scope decisions, pre-clarify)

- **Scope** = M4-remainder plain CRUD (chosen over the M5 security model): build the remaining non-secret resource endpoints + wire a representative frontend slice. RBAC enforcement, real OIDC/IdP, and Vault secret custody are deferred to a later M5 feature.
- **Frontend depth** = representative hook swaps + a complete mock-vs-real tracker (not a full swap of every hook).

### Session 2026-11-05 (clarify)

- Q: Given RBAC enforcement is deferred to M5, how deep is member management here? → A: Basic workspace-scoped roster only — view / add / remove / set a *stored* console role; no invitation or email flow and no external identity-provider sync. Roles are stored and returned but NOT enforced across endpoints in this feature.
- Q: Does the durable run-record persistence table land in this feature or wait for the M6 observability stack? → A: It lands **here** — a first-class durable run-record store is introduced in this feature (US5) as the source for run history + dashboard metrics; the heavy observability backend (distributed tracing / analytics store) stays deferred to M6 and layers on top.
- Q: What does archiving a resource make visible? → A: Soft-archive **excludes** the record from the default list; it stays retrievable via an explicit "include archived" / status filter, and fetch-by-id still returns an archived record so existing bindings resolve as removable references.
- Q: Exactly which edits flag bound agents for re-evaluation (drift trigger)? → A: Mirror the console's existing trigger set — a tool's auth-mode or OAuth scopes; a skill's instructions, resources, version, or content hash; a knowledge base's content/retrieval configuration. Cosmetic fields (name, description, display label) never trigger a re-eval flag.
- Q: Is skill publication actually signing the skill? → A: No — publication records provenance (who/when) as an explicitly-labeled **unsigned stub**; no cryptographic signature is produced. Real signing is deferred.

### Session 2026-11-05 (analyze remediation)

- Q: (C1/P5) What are the data classification and retention/deletion rules for the new tables? → A: See the new **Data Handling & Privacy** section — members = low-sensitivity PII (name/email), runs = operational telemetry with **no raw prompt/response content**, config tables = non-PII; retention + deletion rules stated there.
- Q: (A1) How are the dashboard metrics defined? → A: Over a rolling **30-day** window in **daily buckets**: spend = Σ cost_usd/bucket; call volume = count of `playground`+`published` runs/bucket (eval runs excluded); eval trend = verdict/pass counts over eval runs. (FR-015 tightened.)
- Q: (U1) Is restore a requirement? → A: Yes — archive is reversible via a restore operation (clears the archived marker). (FR-003 extended.)
- Q: (U2) Are eval-run executions recorded as runs? → A: Yes — recorded with `source='eval'`, distinguishable from playground/published so metrics can include or exclude them per FR-015. (FR-013 extended.)
- Q: (U3) Does the swapped publish UI keep the advisory-publish behavior? → A: Yes — the representative publish/eval UI shows the verdict and does NOT hard-block a non-PASS (M4a advisory divergence preserved until the κ-gate). (FR-006 extended.)
- Q: (U4) What happens when a published skill's content is edited? → A: A content edit reverts the skill to `draft` (must be re-published) and flags bound agents via drift. (FR-004 extended.)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Manage the tool & skill catalog (Priority: P1)

A builder opens the Tools and Skills pages and manages the reusable building blocks that agents bind to: HTTP tools, MCP servers (with their exposed tools), and skills. They create a new tool, edit its configuration, list what exists in their workspace, and archive one they no longer need — all persisted and scoped to their workspace. When they edit a tool or skill that is already bound into one or more published-capable agents in a way that changes the agent's frozen grant/content surface, those agents are flagged as needing re-evaluation.

**Why this priority**: Tools and skills are the composable primitives an agent is built from. Without real CRUD here, the agent builder cannot be driven against real data — it is the highest-leverage, lowest-entanglement slice (no secrets, no retrieval math, no identity model), so it is the MVP that makes the rest demonstrable.

**Independent Test**: Create a tool and a skill in workspace A via the API, list them (they appear), edit one bound to an agent (the agent is flagged for re-eval), archive the other (it disappears from the default list), and confirm a request scoped to workspace B never sees workspace A's catalog.

**Acceptance Scenarios**:

1. **Given** an authenticated builder in workspace A, **When** they create a tool with a valid configuration, **Then** it is persisted, returned with an identity and a concurrency token, and appears in the workspace A catalog list.
2. **Given** an existing tool bound into an agent, **When** the builder edits the tool in a way that changes its grant surface (auth mode / scopes), **Then** the response succeeds and every agent bound to that tool is marked as needing re-evaluation.
3. **Given** a skill that exists in workspace A, **When** a request authenticated for workspace B lists or fetches skills, **Then** workspace A's skill is never returned (fail-closed isolation).
4. **Given** a tool with a stale concurrency token, **When** the builder submits an edit with that token, **Then** the edit is rejected as a stale-conflict and the stored tool is unchanged.
5. **Given** a tool or skill, **When** the builder archives it, **Then** it is soft-archived (not hard-deleted) and excluded from the default catalog listing.

---

### User Story 2 - Sync the frontend to real endpoints + track remaining work (Priority: P1)

A developer regenerates the typed client from the backend's published contract, swaps a representative set of console hooks from mock data to real API calls (the agent, version, publish, eval-run, and invoke surfaces from M4a plus the tool/skill catalog from US1), and maintains a single tracker that lists every integration seam and marks it backed-by-real-API or still-mock. Anyone can open the tracker and know exactly how much of the product is real.

**Why this priority**: This is the explicit second half of the request ("make sure the frontend is updated … to keep track of remaining implementation"). It delivers value immediately and independently — even before US1, the tracker can be produced and the already-built M4a hooks can be wired — and it is the honesty mechanism (P1/P9) that prevents mock surfaces from being mistaken for implemented ones.

**Independent Test**: Regenerate the typed client with no drift against the backend contract; load the console with a representative page pointed at the real backend and confirm it renders real stored data (not fixtures); open the tracker and confirm every seam is classified real-or-mock with the count of each.

**Acceptance Scenarios**:

1. **Given** the backend's published contract, **When** the typed client is regenerated, **Then** generation succeeds with zero drift and the swapped hooks type-check against it.
2. **Given** a representative console page wired to the real backend, **When** a user performs its primary action (e.g. list/create an agent or a tool), **Then** the page reflects real persisted state rather than seed fixtures, with the existing loading/empty/error states honored.
3. **Given** the tracker, **When** a reviewer opens it, **Then** each integration seam is marked backed-by-real-API or still-mock, grouped by resource, with totals — and no seam that is still mock is described as implemented.

---

### User Story 3 - Manage knowledge bases (metadata) (Priority: P2)

A builder creates and manages knowledge-base entries and their data-source configuration (name, description, source wiring, chunking/retrieval settings) so agents can later bind to them. Documents, embeddings, and ingestion runs are **not** processed in this feature — the knowledge base is a managed metadata record with a lifecycle status; actual ingestion is a later capability.

**Why this priority**: Knowledge bases are bindable resources the builder needs to see and manage, but their real value (retrieval) depends on the deferred ingestion/vector work, so metadata CRUD is useful but lower than the tool/skill catalog.

**Independent Test**: Create a knowledge base in workspace A, edit its retrieval configuration, list it, and confirm workspace B cannot see it; confirm the ingestion/job surface is represented as a named-but-not-executing status (honest deferral), not a fabricated "completed" ingestion.

**Acceptance Scenarios**:

1. **Given** a builder in workspace A, **When** they create a knowledge base with name and source configuration, **Then** it is persisted workspace-scoped and listable.
2. **Given** an existing knowledge base bound to an agent, **When** its content-affecting configuration changes, **Then** bound agents are flagged for re-evaluation (drift trigger).
3. **Given** a knowledge base, **When** a user inspects its ingestion state, **Then** the state honestly reflects that ingestion is not yet executed (no fabricated document/chunk counts presented as real processing).

---

### User Story 4 - Administer workspaces & members (Priority: P2)

A workspace administrator edits workspace identity and operating settings (display name, default provider/model, spend limits, concurrency/timeout, and the eval verdict/publish policy thresholds), and manages the member roster (view members, add/remove, set a console role). Fine-grained enforcement of those roles across every endpoint, and automatic provisioning from an external identity provider, are **not** in scope — roles are stored and returned, but org-wide RBAC gating and real identity sync are deferred.

**Why this priority**: Administration makes the platform operable per tenant, but it borders the deferred security model; the plain settings/roster CRUD is valuable now while the enforcement layer waits for the dedicated security feature.

**Independent Test**: Update workspace A's limits and eval policy, add and remove a member, set a member's role, and confirm all changes are workspace-scoped and that workspace B's administration surface is unaffected.

**Acceptance Scenarios**:

1. **Given** an administrator in workspace A, **When** they update spend limits and the eval policy thresholds, **Then** the changes persist and are returned on subsequent reads.
2. **Given** a workspace member roster, **When** the administrator adds a member and sets their console role, **Then** the roster reflects the change, scoped to that workspace.
3. **Given** a workspace, **When** an administrator archives it, **Then** it is soft-archived (not hard-deleted) consistent with the no-hard-delete policy.

---

### User Story 5 - Persist and browse run history + metrics (Priority: P3)

An operator browses the history of agent invocations (who ran what, when, outcome, latency, cost, correlation id) and the roll-up metrics the dashboard shows (spend trend, eval trend, call volume). Invocations performed through the real invoke endpoint are durably recorded instead of discarded, and the history and metrics are read from that record.

**Why this priority**: Run history and metrics are observability surfaces that depend on invocations actually happening; persisting them closes the interim "in-memory sink" shortcut from M4a. It is valuable but downstream of the resources being managed and invoked, so it is last.

**Independent Test**: Perform a (mocked) invoke, confirm a run record is durably persisted with its identity/usage/outcome fields, list run history for the workspace, fetch one run's detail, and confirm the dashboard metrics reflect the recorded runs — all workspace-scoped.

**Acceptance Scenarios**:

1. **Given** an invoke completes, **When** the run is recorded, **Then** a durable run record exists capturing model/version identity, usage, latency, cost, outcome, and correlation id.
2. **Given** recorded runs in workspace A, **When** an operator lists run history, **Then** only workspace A's runs are returned, newest first, with stable pagination.
3. **Given** recorded runs, **When** the dashboard metrics are requested, **Then** the returned aggregates are computed from the real run records for that workspace only.

---

### Edge Cases

- **Cross-tenant access**: every list/get/edit/archive on every new resource MUST return nothing (or not-found) for a workspace the caller is not in — proven by negative cross-tenant tests, never relying on application filtering alone.
- **Malformed or missing identifiers**: a malformed resource id yields a typed validation error, not an unhandled server error; a well-formed but foreign/absent id yields not-found.
- **Concurrent edits**: a second editor using a stale concurrency token is rejected with a stale-conflict and the stored record is unchanged.
- **Duplicate create under one idempotency key**: a create retried with the same idempotency key while the first is in flight is rejected as in-progress rather than double-creating.
- **Binding integrity**: archiving a tool/skill/knowledge base that is bound into agents does not corrupt those agents — the binding resolves as a removable/dangling reference, matching current console behavior.
- **Drift flagging**: editing a bound resource's content/grant surface flags bound agents for re-eval; a cosmetic edit (e.g. description) does not.
- **Deferred-capability honesty**: surfaces whose capability is deferred (knowledge-base ingestion, secret values, role enforcement) report their real state and never present fabricated success.
- **Empty states**: listing a resource type with none present returns an empty page with valid pagination, not an error.

## Requirements *(mandatory)*

### Functional Requirements

**Catalog resources (US1)**

- **FR-001**: System MUST let an authenticated builder create, read, list, update, and archive HTTP tools, MCP servers (and their exposed tools), and skills, each scoped to the caller's workspace.
- **FR-002**: System MUST flag every bound agent for re-evaluation when a bound resource's drift-trigger surface changes — specifically a tool's auth-mode or OAuth scopes; a skill's instructions, resources, version, or content hash; a knowledge base's content/retrieval configuration. Cosmetic fields (name, description, display label) MUST NOT flag agents.
- **FR-003**: System MUST treat resource removal as a soft archive (no hard delete): an archived record is excluded from the default list, remains retrievable via an explicit include-archived/status filter, and is still returned by fetch-by-id so existing bindings resolve as removable references. Archive MUST be **reversible via a restore operation** that clears the archived marker and returns the record to the default list.
- **FR-004**: Skill publication records provenance (who/when) as an explicitly-labeled **unsigned stub** and MUST NOT produce or claim a cryptographic signature; real signing is deferred until that capability exists. Editing the **content** of a published skill (instructions/resources/version) MUST revert it to `draft` (requiring re-publish) and flag bound agents via the drift trigger; a cosmetic edit MUST NOT change publish state.

**Frontend sync + tracker (US2)**

- **FR-005**: System MUST expose a machine-readable API contract from which a typed frontend client can be generated with zero drift.
- **FR-006**: Project MUST swap a representative set of console hooks from mock sources to real API calls — at minimum the M4a surfaces (agents, versions, publish, eval-run, invoke) plus the US1 catalog — preserving the existing hook/function shapes so the swap is drop-in. The swapped publish/eval UI MUST preserve the **advisory-publish behavior** (show the eval verdict, do NOT hard-block a non-PASS), consistent with the M4a temporary divergence until the κ-calibrated gate activates.
- **FR-007**: Project MUST maintain a single tracker that classifies every frontend integration seam as backed-by-real-API or still-mock, grouped by resource, with totals, and MUST keep it accurate to the built state (no mock surface described as implemented).

**Knowledge bases (US3)**

- **FR-008**: System MUST let a builder create, read, list, update, and archive knowledge-base records and their data-source/retrieval configuration, workspace-scoped.
- **FR-009**: System MUST represent ingestion as an honestly-deferred state (named status, no fabricated document/chunk processing); real ingestion is out of scope.

**Workspace & member administration (US4)**

- **FR-010**: System MUST let an administrator read and update workspace identity and operating settings (display name, default provider/model, spend limits, concurrency, request timeout) and the eval verdict/publish policy thresholds, workspace-scoped.
- **FR-011**: System MUST let an administrator view the member roster, add/remove members, and set a member's *stored* console role, workspace-scoped; there is no invitation/email flow and no external identity-provider sync in this feature, and stored roles are not enforced across endpoints here (enforcement is M5).
- **FR-012**: System MUST store and return console roles without implying org-wide role enforcement — fine-grained RBAC gating across endpoints is explicitly deferred and MUST NOT be presented as active.

**Run history & metrics (US5)**

- **FR-013**: System MUST introduce a first-class durable run-record store in this feature and persist a run record for each invocation capturing model/profile identity, version/prompt identity, usage, latency, cost, outcome, correlation id, and a `source` (`playground`/`published`/`eval`); eval-run executions MUST be recorded with `source='eval'` so they are distinguishable from caller invocations. The run record MUST capture **identity + metrics only — no raw prompt or response content**. The heavy observability backend (distributed tracing / analytics store) is deferred to the M6 observability feature and layers on top.
- **FR-014**: System MUST let an operator list run history (newest first, paginated) and fetch a single run's detail, workspace-scoped.
- **FR-015**: System MUST compute dashboard roll-up metrics from the real run records for the caller's workspace only, over a **rolling 30-day window in daily buckets**: **spend trend** = Σ `cost_usd` per bucket; **call volume** = count of `source in (playground, published)` runs per bucket (eval runs excluded to avoid inflating caller traffic); **eval trend** = verdict/pass counts over `source='eval'` runs. An empty window MUST return zero-filled buckets, not an error.

**Cross-cutting (all stories)**

- **FR-016**: Every new resource MUST be isolated per workspace at the storage layer (fail-closed), with negative cross-tenant tests proving a caller cannot read or mutate another workspace's resources.
- **FR-017**: All mutating create endpoints MUST honor an idempotency key (reject an in-flight duplicate) and all edit endpoints MUST honor optimistic concurrency via a concurrency token (reject a stale write with a typed conflict).
- **FR-018**: All responses (success and error) MUST conform to the existing shared error envelope and pagination contract, and mirror the frontend's existing response types so the swap is drop-in.
- **FR-019**: The runtime and evaluation engines MUST remain pure libraries — only the API layer gains these endpoints; the runtime/eval purity checks MUST stay green.
- **FR-020**: Validation MUST be deterministic and fail-closed — malformed input yields a typed validation error, never an unhandled failure or a silent accept.
- **FR-021**: No secret values (provider keys, tool/channel credentials, runtime tokens) are created, stored, or returned by this feature; secret-bearing resources are deferred and MUST NOT be partially implemented in a way that could leak or persist a real secret.

### Key Entities *(include if feature involves data)*

- **Tool (repository)**: a reusable HTTP tool definition a builder manages and agents bind to — identity, name, configuration (endpoint/auth-mode/scopes as non-secret metadata), risk tier, workspace scope, archive state, concurrency token.
- **MCP server / MCP tool**: a managed connector exposing one or more callable tools with risk tiers; workspace-scoped, archivable.
- **Skill**: a reusable instruction/resource bundle with a content identity (hash) and provenance; editing its content is a drift trigger for bound agents; workspace-scoped, archivable.
- **Knowledge base**: a managed metadata record with data-source and retrieval configuration and a lifecycle/ingestion status (ingestion deferred); workspace-scoped, archivable, drift-trigger on content config.
- **Workspace settings / limits / eval policy**: per-workspace operating configuration (defaults, spend/concurrency/timeout limits, verdict/publish thresholds).
- **Member**: a person in a workspace with a console role (stored, not org-enforced here).
- **Run record**: a durable record of one invocation — identity (model/profile/version), usage, latency, cost, outcome, correlation id, workspace scope; source of history and metrics.
- **Integration-seam tracker**: a catalog of every frontend seam classified real-vs-mock, grouped by resource.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A builder can create, edit, list, and archive a tool and a skill end-to-end, with changes persisted across sessions (not reset from fixtures).
- **SC-002**: 100% of new resource types enforce workspace isolation, each proven by at least one negative cross-tenant test that fails closed.
- **SC-003**: The typed frontend client regenerates from the backend contract with zero drift, and the swapped representative pages render real persisted data with no type errors.
- **SC-004**: The remaining-implementation tracker accounts for every integration seam and classifies each as real or mock, with totals, and is consistent with the actual built endpoints (independently verifiable).
- **SC-005**: Editing a bound tool/skill/knowledge-base content or grant surface flags exactly the affected bound agents for re-evaluation; a cosmetic edit flags none.
- **SC-006**: Every mutating endpoint rejects a stale-token edit and an in-flight duplicate create with the correct typed conflict, verified by test.
- **SC-007**: Invocations are durably recorded and run history + dashboard metrics are computed from those records, scoped to the workspace, verified by test.
- **SC-008**: No secret value is persisted or returned by any endpoint in this feature (verified by a secret-scan / absence test), and no deferred capability (ingestion, role enforcement, signing) is presented as active.
- **SC-009**: Run records persist identity + metrics only — a test confirms **no raw prompt/response content** is stored; dashboard metrics return zero-filled 30-day daily buckets on an empty window and reconcile exactly with seeded runs (spend/call-volume/eval-trend per FR-015).

## Data Handling & Privacy (Constitution P5)

Classification, access, retention, and external processing for the data this feature introduces:

| Data | Classification | Access | Retention / Deletion |
|---|---|---|---|
| **Members** (subject, display_name, email, console_role) | Low-sensitivity PII (no special category) | Workspace-scoped (RLS); readable/editable by the active workspace's admins | Retained while the membership exists; **removing a member hard-deletes the roster row**; purged on workspace archival |
| **Runs** (identity, usage, latency, cost, outcome, correlation id, source) | Operational telemetry — **no raw prompt or response content** | Workspace-scoped (RLS) | Retained for the pilot; deleted on workspace archival; a durable retention policy (TTL) is defined with the M6 observability feature |
| **Tools / MCP / Skills / KB metadata / Workspace settings** | Non-PII tenant configuration | Workspace-scoped (RLS) | Retained until soft-archived; archive is reversible (restore); no hard delete |

- **No secret values** (provider keys, tool/channel credentials, runtime tokens) are stored or returned by this feature (FR-021); secret custody is deferred to M5 (Vault).
- **No external processing**: all endpoints are tested against a mocked provider; no customer data is sent to any model provider in this feature (the live invoke + its data-minimization path is the separate Phase-5 step).
- **Secret/PII leakage**: credentials and member PII MUST NOT appear in logs, traces, error bodies, SSE frames, or the mock-vs-real tracker.

## Assumptions

- **Scope boundary**: this feature covers *plain, non-secret* resource CRUD and a representative frontend sync. Deferred to a later **M5 security** feature: fine-grained 4-role RBAC enforcement, per-resource access policy, real OIDC/identity-provider provisioning, real secret custody (Vault), and the secret-bearing resources themselves — **provider keys, runtime API tokens, and tool-approval workflows**. Deferred to **M7**: real knowledge-base ingestion/embeddings and ingestion-job execution. Deferred to **P2/post-v1.0**: messaging **channels**. These are named here only to mark the boundary, not built.
- **Async worker**: the eval-run and (future) ingestion job shapes are named, but the deployable async worker remains deferred; eval continues to execute inline as established in M4a.
- **Tenancy model**: the established workspace-as-tenant, row-level isolation, and the shared error-envelope/pagination/optimistic-concurrency/idempotency patterns from M4a are reused for every new resource.
- **Frontend contract**: the existing console hook/function shapes and response types are the contract; the swap replaces the data source only, and the modified-Next.js caveat is respected when touching frontend code.
- **Members in the mock are group-derived**; because real identity provisioning is deferred, member management here is a basic workspace-scoped roster (view/add/remove/set-role), not a synced directory.
- **Representative, not exhaustive**: per the agreed frontend depth, a representative set of hooks is swapped and the remainder is tracked; a full swap of all ~253 seams is not a goal of this feature.
- **No live/paid provider calls**: all endpoints are tested against mocked runtime/gateway; the real invoke under the paid-call protocol remains a separate, later step.
- **Honesty gate**: consistent with the constitution, completion will report commands run and any unverified requirements; a mock surface is never reported as implemented.
