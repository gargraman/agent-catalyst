# Feature Specification: Provider-key custody via a pluggable secrets backend

**Feature Branch**: `feat/backend-m0-foundation` (feature 014, M5 remainder — secret custody slice)

**Created**: 2026-11-11

**Status**: Draft

**Input**: User description: "Provider-key custody via a pluggable secrets backend (M5 remainder). Build real provider-key custody as a deterministic, fail-closed control on top of RBAC + membership + RLS, with no plaintext leakage, and resolve the provider key at invoke time from the custody service instead of a single environment variable. The deployment target is an AWS account, so the secrets backend is AWS Secrets Manager (emulated locally by LocalStack), and access to the secrets manager must be pluggable so the cloud provider can be swapped later. Scope is PROVIDER KEYS ONLY, end-to-end; other credential families are designed-for but not built."

## User Scenarios & Testing *(mandatory)*

**Primary user**: a workspace **administrator** (holds `key.manage`) who supplies and manages the provider API keys the workspace's agents use. **Secondary beneficiary**: any **caller** (console playground user, eval runner) whose invocation now draws on a securely stored key instead of a shared environment variable.

**The job**: "As the person responsible for a workspace, I can give the platform our provider API keys once, see them only as masked references afterward, rotate or revoke them, and trust that every agent run uses the right stored key — never a leaked or stale one — with no key value ever appearing in a response, log, trace, or export."

### User Story 1 - Capture and list provider keys (write-once) (Priority: P1)

An administrator adds a provider key (e.g. an Anthropic or OpenAI key) by submitting it once. The system stores the raw value in the secrets backend and keeps only a masked display value plus an opaque reference. The raw value is never returned on capture or on any later read. The administrator can list the workspace's keys and sees provider, label, masked value, region, status, and timestamps — never the secret.

**Why this priority**: Without secure capture there is nothing to resolve at invoke time; this is the foundation of the slice and the first thing an operator must do to onboard a workspace. It delivers value on its own (keys are safely held and visible as a managed roster) even before resolution is wired.

**Independent Test**: Capture a key as an admin, then list keys; assert the response contains the masked value and metadata but never the raw secret, and that a second actor in a different workspace cannot see it.

**Acceptance Scenarios**:

1. **Given** an admin with `key.manage` in workspace A, **When** they capture a key `{provider, label, value, region?}`, **Then** the response returns `{id, provider, label, masked, region?, added, status: active}` and NOT the raw value, and the stored record holds a masked value plus an opaque reference (never the plaintext in the application database).
2. **Given** a captured key, **When** anyone lists or reads provider keys, **Then** only the masked value and metadata are returned; the raw secret is never retrievable through any read endpoint.
3. **Given** an admin in workspace A and a key captured in workspace B, **When** the admin lists keys, **Then** workspace B's key is absent (tenant isolation; no existence leak across workspaces).
4. **Given** a non-admin caller (lacking `key.manage`), **When** they attempt to capture or list provider keys, **Then** the request is denied by the role gate.
5. **Given** an admin, **When** they capture a second key with a label already used in the same workspace, **Then** the request is rejected as a conflict (labels are unique per workspace).

---

### User Story 2 - Resolve the stored key at invoke and eval time (Priority: P1)

When an agent is invoked (playground invoke) or evaluated (eval-run), the platform resolves the workspace's active key for that agent's provider from the custody service, fetches the plaintext just-in-time for the provider call, and uses it. The single shared environment-variable key is no longer the production source of truth. If no active key exists for the provider, the run fails closed with a clear configuration error before any provider call is attempted.

**Why this priority**: This is the consumer that makes stored keys meaningful. Until invoke draws on the stored key, custody is inert plumbing. It is the behavioral change that retires the shared-env-var approach.

**Independent Test**: With a key stored for a provider, invoke an agent on that provider and assert the resolved stored key is the one used; remove/deactivate it and assert the run fails closed with a configuration error and makes no provider call.

**Acceptance Scenarios**:

1. **Given** workspace A has an active key for the agent's provider, **When** the agent is invoked, **Then** the run uses the stored key resolved just-in-time (not the environment variable).
2. **Given** workspace A has an active key for the agent's provider, **When** an eval-run executes, **Then** it resolves and uses the same stored key through the same mechanism as invoke.
3. **Given** workspace A has NO active key for the agent's provider, **When** the agent is invoked or evaluated, **Then** the run fails closed with a clear configuration error and performs no provider call and creates no partial run that implies success.
4. **Given** workspace A's key and workspace B invoking its own agent, **When** workspace B resolves a key, **Then** it can only ever resolve its own workspace's key (cross-tenant resolution is impossible).

---

### User Story 3 - Rotate a provider key (Priority: P2)

An administrator rotates a key: they supply a new raw value, the system replaces the stored secret, updates the masked display and a "rotated" timestamp, and the previous secret stops being used by subsequent runs. The key's identity (id, label) is preserved so bound agents keep working without reconfiguration.

**Why this priority**: Rotation is standard credential hygiene and is modeled in the console, but the pilot can operate briefly with capture + resolve alone, so it ranks below the P1 foundation.

**Independent Test**: Rotate a stored key, then invoke; assert the new secret is used and a run that would have used the old secret no longer does.

**Acceptance Scenarios**:

1. **Given** an active key, **When** an admin rotates it with a new value, **Then** the stored secret is replaced, the masked value and rotated timestamp update, the id/label are unchanged, and the status remains active.
2. **Given** a rotated key, **When** an agent is invoked afterward, **Then** the run resolves the new secret, and the previous secret is no longer resolvable.
3. **Given** a non-admin, **When** they attempt to rotate a key, **Then** the request is denied.

---

### User Story 4 - Revoke a provider key (Priority: P2)

An administrator revokes a key. The record is kept as a revoked tombstone (not deleted, for audit), and the secret can no longer be resolved. Agents whose provider relies on that key fail closed until a replacement active key exists.

**Why this priority**: Revocation is the safety valve for a compromised key; important but, like rotation, secondary to the capture/resolve foundation.

**Acceptance Scenarios**:

1. **Given** an active key, **When** an admin revokes it, **Then** its status becomes revoked, the record is retained (not deleted), and the secret is no longer resolvable.
2. **Given** a revoked key is the only key for a provider, **When** an agent on that provider is invoked, **Then** the run fails closed with a configuration error.
3. **Given** a non-admin, **When** they attempt to revoke a key, **Then** the request is denied.

---

### Edge Cases

- **No key for provider**: invoke/eval fails closed with a configuration error; no provider call, no misleading partial success (US2/AC3).
- **Revoked or rotated-away secret**: the superseded secret value never resolves again (US3/AC2, US4/AC1).
- **Duplicate label**: rejected as a conflict within the workspace (US1/AC5).
- **Multiple active keys for the same provider in a workspace**: the resolution rule MUST be deterministic (see FR-016) rather than ambiguous.
- **Secrets-backend unavailable at capture**: capture fails with a clear error and persists no half-written record (no row pointing at a nonexistent secret).
- **Secrets-backend unavailable at resolve**: the run fails closed with a configuration/unavailable error; it does not fall back to the shared environment variable in production.
- **Malformed or empty key value**: rejected at capture with an input-validation error; nothing stored.
- **Cross-tenant attempt**: a caller in one workspace can neither see, resolve, rotate, nor revoke another workspace's key.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide a provider-agnostic secrets interface with operations to store (put), fetch plaintext (get), rotate, and disable/delete a secret, behind which concrete backends are interchangeable.
- **FR-002**: The system MUST offer at least two interchangeable secrets backends selected by configuration: a **local** backend (for offline development and automated unit tests) and an **AWS Secrets Manager** backend (for deployed environments, usable locally against an AWS-compatible emulator). Selecting a backend MUST require no change to calling code.
- **FR-003**: The local backend MAY seed from the existing shared provider-key environment variable for development continuity; the deployed (AWS) backend MUST NOT rely on that environment variable as a source of truth.
- **FR-004**: The system MUST persist a per-workspace provider-key record holding: provider, label, masked display value, an opaque reference to the stored secret, optional region, status (active / expiring / revoked), a created timestamp, and a rotated timestamp. The raw secret value MUST NOT be stored in the application database.
- **FR-005**: Provider-key records MUST be workspace-scoped and isolated by the same storage-level tenancy (RLS) as other resources; no caller may read, resolve, or mutate another workspace's key.
- **FR-006**: Labels MUST be unique within a workspace; a duplicate-label capture MUST be rejected as a conflict.
- **FR-007**: Capture MUST be write-once: the raw value is submitted once, written to the selected secrets backend, and NEVER returned by the capture response or any subsequent read.
- **FR-008**: The system MUST compute and store a masked display value (recognizable head/tail, body hidden) mirroring the frontend masking convention, and expose only the masked value to clients.
- **FR-009**: The system MUST expose capture, list, rotate, and revoke operations for provider keys, each gated on the existing `key.manage` permission (administrator), composing with the existing RBAC role gate and tenancy — provider keys are workspace-admin configuration and MUST NOT carry a per-resource access policy.
- **FR-010**: Listing provider keys MUST return masked values and metadata only, scoped to the caller's workspace.
- **FR-011**: Rotation MUST replace the stored secret, update the masked value and rotated timestamp, preserve the record identity (id, label), keep status active, and ensure the superseded secret no longer resolves.
- **FR-012**: Revocation MUST mark the record revoked and retain it as a tombstone (not delete it); a revoked key's secret MUST NOT resolve.
- **FR-013**: Agent invocation AND eval-run MUST resolve the workspace's active provider key for the agent's provider from the custody service and fetch the plaintext just-in-time for the provider call, replacing the shared environment variable as the production source of truth.
- **FR-014**: If no active key exists for the required provider (none captured, or the only one revoked), invocation and eval-run MUST fail closed with a clear configuration error BEFORE any provider call, producing no misleading partial success.
- **FR-015**: The pure runtime library MUST remain free of any secrets-backend or cloud-SDK dependency; it MUST receive only the already-resolved plaintext string from the application layer (the same seam the environment variable uses today). Secrets-backend access lives only in the application layer.
- **FR-016**: When more than one active key exists for the same provider in a workspace, resolution MUST be deterministic and documented (e.g. most-recently-added active key), never arbitrary.
- **FR-017**: Raw secret values MUST NOT appear in logs, traces, error messages/bodies, exports, or any client-visible output at any point (capture, list, resolve, rotate, revoke, or failure paths).
- **FR-018**: The secrets interface and the custody records MUST be designed so other credential families (MCP-server, HTTP-tool, data-source, channel credentials) can reuse the same backend later WITHOUT building their storage or wiring now.
- **FR-019**: The deployed-environment backend MUST be exercisable locally against an AWS-compatible emulator wired by endpoint configuration, so the real backend code path is testable without a cloud account.

### Key Entities

- **Provider Key (record)**: a workspace-scoped, admin-managed reference to a provider API credential. Attributes: id, workspace, provider, label (unique per workspace), masked display value, opaque secret reference, optional region, status (active/expiring/revoked), created timestamp, rotated timestamp. Holds NO raw secret.
- **Secrets Backend (abstraction)**: the interchangeable custody mechanism. Operations: store, fetch plaintext, rotate, disable/delete. Concrete forms: local (dev/test) and AWS Secrets Manager (deployed; emulatable locally). Selected by configuration.
- **Stored Secret**: the raw credential value held only inside the secrets backend, addressed by the opaque reference on the Provider Key record. Never stored in the application database, never returned to clients.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In 100% of capture, list, rotate, revoke, resolve, and failure paths, no raw secret value is present in any response body, log line, trace, export, or error message (verified by assertion and secret scan).
- **SC-002**: An administrator can onboard a workspace's provider key once and thereafter sees only a masked reference; the raw value is unrecoverable through any read path (0 read paths expose it).
- **SC-003**: 100% of agent invocations and eval-runs draw the provider credential from the stored per-workspace key via the custody service rather than the shared environment variable.
- **SC-004**: A run with no active key for the required provider fails closed 100% of the time with a clear configuration error and makes zero provider calls.
- **SC-005**: Cross-tenant access to provider keys is impossible: 0 of N cross-workspace read/resolve/rotate/revoke attempts succeed (negative tenancy tests all pass).
- **SC-006**: Switching the secrets backend between the local and deployed options requires 0 changes to calling code (configuration-only), demonstrated by the same test suite passing against the local backend and the emulator-backed deployed backend.
- **SC-007**: A rotated or revoked secret resolves on 0% of subsequent runs; the new (rotated) secret resolves on 100% of them.

## Security & Data Handling *(mandatory for this feature)*

- **Data classification**: provider API keys are **secrets / highly sensitive credentials**. The masked value and metadata are low-sensitivity; the raw value is the protected asset.
- **Storage boundary**: raw values live only in the secrets backend (AWS Secrets Manager with provider-managed encryption at rest in deployed environments; a local store for dev/test). The application database holds only masked value + opaque reference + metadata.
- **Access**: write and management gated on `key.manage` (admin); read returns masked-only; resolve is an internal server-side operation at call time, never client-exposed. All scoped by workspace tenancy (RLS).
- **Retention/deletion**: revoked keys are retained as tombstones for audit; the underlying secret is disabled/removed from the backend on revoke/rotate so it can no longer resolve.
- **External processing**: the raw value is sent only to the configured provider at call time and to the secrets backend at capture/rotate; it is never placed in prompts, exports, logs, traces, or client-visible errors.

## Out of Scope / Non-Goals

- **Other credential families** (MCP-server, HTTP-tool, data-source, channel credentials): the interface is designed for them, but their storage and wiring are NOT built here (their runtime consumers — tool execution, ingestion, channels — are deferred to P2/M7).
- **Application-level envelope encryption (KMS GenerateDataKey + app-held ciphertext)**: not built; the deployed backend's managed encryption-at-rest suffices behind the seam.
- **AD / IdP user provisioning** (the next M5 feature), **runtime token mint + deny-set**, **tool approvals**, and **console OIDC login**.
- **Secret rotation automation / scheduling** (rotation here is an explicit admin action, not a scheduled job).

## Assumptions

- The deployment target is an AWS account (working assumption); AWS Secrets Manager is the deployed secrets backend, emulated locally by an AWS-compatible emulator.
- The existing RBAC (`key.manage`), membership-sourced authorization, and RLS tenancy from prior features are in place and compose with this feature rather than being replaced.
- The frontend already models the `ProviderKey` shape and the add/rotate/revoke/list seams; the backend contract must be a drop-in match so no frontend rework is required.
- Provider keys are workspace-admin configuration; they are intentionally NOT governed by the per-resource access policy (feature 013).
- An agent's provider is derivable from its published version configuration, so resolution can pick the correct provider's key.
- Live paid provider calls are NOT ordinary tests; resolution behavior is verified with stored test values and mocked provider gateways, and the deployed-backend path is verified against the local emulator (opt-in), consistent with the project's paid-call protocol.
