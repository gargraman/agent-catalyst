# Feature Specification: M5 Security / Tenancy — Milestone Completion

**Feature Branch**: `feat/backend-m0-foundation` (feature dir `015-m5-security-completion`; shared M-series branch)

**Created**: 2026-11-11

**Status**: Draft

**Input**: User description: "complete m5" (scope confirmed 2026-11-11: the full M5 remainder **plus** OIDC console-login and IdP group→member/team sync; tool approvals carved out — see Out of Scope)

> **⚠️ This is the M5-completion UMBRELLA. It has been SPLIT into six implementable feature folders (2026-11-11).**
> M5 (Security / tenancy) already shipped three bounded features — **012** (pluggable IdP bearer authN), **013**
> (per-resource access policy), **014** (provider-key custody). This folder captures the **open M5 remainder** +
> the shared design (plan/research/data-model/contracts). The actual TDD build happens in these six child
> folders, in dependency order:
>
> | Child | Slice | Pri | Depends on | User story here |
> |-------|-------|-----|-----------|-----------------|
> | [`016-m5-rate-limiting`](../016-m5-rate-limiting/spec.md) | Rate limiting (F5) | P1 | — | US1 |
> | [`017-m5-ssrf-egress-guard`](../017-m5-ssrf-egress-guard/spec.md) | SSRF/egress guard (F19) | P1 | — | US2 |
> | [`018-m5-caller-token-denyset`](../018-m5-caller-token-denyset/spec.md) | Caller-token plane + deny-set | P1 | 016 | US3 |
> | [`019-m5-oidc-console-login`](../019-m5-oidc-console-login/spec.md) | OIDC login + session + CSRF | P2 | 018 | US4 |
> | [`020-m5-idp-group-sync`](../020-m5-idp-group-sync/spec.md) | Group→members-store sync | P2 | 019 | US5 |
> | [`021-m5-redis-degraded-pgbouncer`](../021-m5-redis-degraded-pgbouncer/spec.md) | Redis degraded + PgBouncer | P3 | 016, 018 | US6 |
>
> Run `/speckit-plan` → `/speckit-tasks` → `/speckit-implement` **per child folder**, starting at 016. The user
> stories, FRs, and SCs below remain the reconciled source of truth; each child restates its slice with the
> original IDs preserved for traceability.
>
> **Secret custody (feature 014, 2026-11-11):** every "Vault" reference in the M5 design source is superseded
> by the pluggable secrets backend (AWS Secrets Manager / LocalStack / `local`). This spec does not re-open
> secret custody; it is listed here only as an already-closed M5 slice.

## Clarifications

### Session 2026-11-11

- Q: When Redis is down, how should rate limiting specifically behave? → A: Fail-open + alert (rate limiting is an availability/cost control; deny-set, spend reservation, and auth still fail closed).
- Q: Is workspace role/team authority the members store or live IdP group claims? → A: Members store is the authority (per 012/013, under RLS); IdP groups **sync into** the members store and feed the notification projection — no live per-request group→role resolution.
- Q: What happens to an in-flight streaming invoke when its token/subject is revoked? → A: Cut the stream immediately on detected revocation.
- Q: What is the maximum console session / access-token lifetime? → A: ≤ 1 hour (so the deny-set TTL floor is ≥ 1 hour).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Published endpoints and the console can't be hammered or abused (Priority: P1)

An application team publishes an agent and hands a caller token to a downstream service. A bug (or abuse) in
that service sends a burst of invoke calls; separately, a builder in the console triggers many expensive
operations (playground runs, eval batches, ingestion) in quick succession. The platform must throttle both
surfaces predictably so one caller cannot exhaust shared provider quota, run up unbounded spend, or starve
other tenants — and every throttled caller must be told clearly when to retry.

**Why this priority**: This closes finding **F5**, an un-built **High**-severity hole. The console API triggers
the most expensive operations in the system and today has *no* rate limiting; `rate_limit()` is referenced in
the design but never defined. Without it, a single misbehaving caller is an availability and cost incident.

**Independent Test**: Drive a published invoke token and a console route past their configured thresholds in a
fixed window and assert the over-limit requests are rejected with the standard throttle response and retry
guidance, while a second distinct caller/token on the same route is unaffected.

**Acceptance Scenarios**:

1. **Given** a caller token with a per-token limit of N requests per window, **When** it makes N+1 invoke
   requests inside one window, **Then** the (N+1)th is rejected with a "too many requests" outcome that
   includes a retry-after hint and remaining-quota information, and no provider call is made for it.
2. **Given** a builder calling an expensive console route (playground/eval/ingestion), **When** they exceed
   that route's per-principal limit, **Then** the request is rejected with the same throttle contract before
   any model or ingestion work starts.
3. **Given** two distinct tokens on the same endpoint, **When** one exceeds its limit, **Then** the other
   token's requests continue to succeed (limits are per-identity, not global).
4. **Given** a caller that was throttled, **When** the window elapses, **Then** its requests succeed again
   without manual intervention.

---

### User Story 2 - A prompt-injected agent cannot become an SSRF / exfiltration primitive (Priority: P1)

A knowledge-base document or tool result contains a hidden instruction that steers an agent to call an HTTP
tool against an internal address (cloud metadata endpoint, internal admin service, private IP). The platform
must refuse any tool egress that resolves to a private, link-local, or loopback destination, and must refuse a
redirect from an allowed public domain to an internal host — before the P2 egress sandbox exists.

**Why this priority**: This closes finding **F19**, an un-built **High**-severity hole, and directly upholds
Constitution P3 ("keep a prompt-injected agent from becoming an SSRF/exfiltration primitive"). It is the
single control that contains the highest-impact attack class on an agent platform during the P1 pilot.

**Independent Test**: Configure an HTTP tool with a domain allow-list, then attempt egress to (a) a private/
internal IP, (b) an allowed domain that redirects to an internal host, and (c) a hostname that resolves to a
private IP (DNS rebinding), and assert each is refused with a safe error before any outbound request leaves.

**Acceptance Scenarios**:

1. **Given** an HTTP tool configured with a public-domain allow-list, **When** the agent attempts a call to a
   private, loopback, or link-local address (including cloud metadata), **Then** the call is refused with a
   non-leaking error and nothing is sent.
2. **Given** a request to an allowed public domain, **When** that domain responds with a redirect to an
   internal host, **Then** the redirect is not followed and the call is refused.
3. **Given** a hostname on the allow-list, **When** it resolves (or re-resolves) to a private IP, **Then** the
   connection is refused (resolution is validated against the address actually used to connect).
4. **Given** a refused egress attempt, **When** it is logged, **Then** the log/trace records the refusal
   without exposing secrets or full internal target detail to a client-visible error.

---

### User Story 3 - Publishers mint revocable caller tokens, and revocation takes effect immediately (Priority: P1)

A publisher mints a caller token so an external service can invoke a published agent. The secret is shown
exactly once. Later, a token is leaked or a user is offboarded during an incident; an admin must be able to
cut that access *now* — killing both active console sessions and caller tokens on their next call — not on the
next IdP login.

**Why this priority**: The published-invoke auth plane (the "token plane") is currently **deferred** yet is a
prerequisite for safely exposing invoke to external callers. Immediate revocation (the Redis deny-set) is the
designed answer to a leaked token / disabled user and must fail closed.

**Independent Test**: Mint a token (capture the one-time secret), invoke successfully, revoke it, and confirm
the next invoke is rejected; separately, add a subject to the deny-set and confirm an active console session
and a live caller token for that subject are both rejected on their next request.

**Acceptance Scenarios**:

1. **Given** a publisher mints a caller token, **When** the mint succeeds, **Then** the full secret is
   returned exactly once, only a non-reversible hash and a short display prefix are stored, and the secret
   never appears in logs, traces, or exports.
2. **Given** a valid caller token, **When** it is revoked by a publisher or admin, **Then** its next invoke is
   rejected as unauthorized.
3. **Given** an admin issues a "revoke-user" / "force-logout" for a subject, **When** that subject's active
   console session makes its next request **or** one of its caller tokens makes its next call, **Then** both
   are rejected immediately (not on next login), for at least as long as any outstanding access token could
   live.
4. **Given** the immediate-revocation store is unavailable, **When** an auth decision must be made, **Then**
   the request fails closed (is rejected) rather than being allowed through unchecked.
5. **Given** a token has an optional expiry, **When** it is used after expiry, **Then** it is rejected.

---

### User Story 4 - Console users sign in through the organization's identity provider (Priority: P2)

A builder opens the console. Instead of a mocked profile, they are redirected to the organization's IdP, sign
in, and return to an authenticated session. Every subsequent console request validates that session, and
state-changing requests are protected against cross-site request forgery.

**Why this priority**: Real console login (OIDC authorization-code flow + session) replaces the mocked
identity and is the gate to using the console for real. It is P2 because the bearer-token authN path (012) and
server-side authZ (008/013) are already enforced; this adds the interactive human-login front door.

**Independent Test**: Complete an authorization-code login against a test IdP, confirm an authenticated
session is established and validated on a protected route, confirm a state-changing request without a valid
anti-CSRF token is rejected, and confirm logout ends the session.

**Acceptance Scenarios**:

1. **Given** an unauthenticated user hits a protected console route, **When** the request is processed,
   **Then** they are sent through the IdP authorization-code login and returned to an authenticated session.
2. **Given** an authenticated session, **When** any console request is made, **Then** the session/token is
   validated server-side (including issuer/audience/expiry) and the deny-set is checked before the request
   proceeds.
3. **Given** an authenticated session, **When** a state-changing console request arrives without a valid
   anti-CSRF token, **Then** it is rejected.
4. **Given** an authenticated user, **When** they log out, **Then** the session is ended and a subsequent
   protected request requires re-login.

---

### User Story 5 - Access and notifications reflect IdP group membership (Priority: P2)

An admin maps the organization's IdP groups to workspace roles and teams. When a user signs in, that mapping
**syncs their role and team into the members store** — which remains the authority for workspace role/team
under RLS (per features 012/013). The platform never hard-codes a vendor's group-naming convention. For owner
notifications and recipient fan-out, the platform also maintains a best-effort projection of group membership
populated from whatever the active IdP exposes.

> **Clarified 2026-11-11:** authority is the **members store**, not live IdP group claims. This reconciles with
> 012/013 (role sourced from the members store under RLS) and with the pilot's Trellix-IAM token, which exposes
> no AC role/group claims. US5 is therefore a **group→members-store sync** (at login, and on a scheduled
> refresh where the IdP supports enumeration), not a per-request group→role resolution.

**Why this priority**: This makes authZ and notification fan-out track real org membership across heterogeneous
IdPs (name-convention, object-GUID, SAML attribute). It is P2 because it builds on the login flow (US4) and the
adapter seam (012/18).

**Independent Test**: Configure a workspace's role→group mapping, sign in a user whose IdP groups match a role
and a team, and confirm their members-store role and team are updated to match; confirm a user whose groups
match no mapping gets no role/team; confirm the membership projection is updated on login and (where the IdP
supports enumeration) on a scheduled refresh.

**Acceptance Scenarios**:

1. **Given** a per-workspace role→group and team→group mapping, **When** a user signs in with IdP groups that
   match, **Then** their members-store role and team for that workspace are set/updated to the mapped values.
2. **Given** a user whose IdP groups match no mapping for a workspace, **When** the sync runs, **Then** they
   receive no role and no team there (fail-closed default); live per-request group→role resolution is not used.
3. **Given** an IdP that exposes group-member enumeration, **When** a scheduled refresh runs, **Then** the
   membership projection for fan-out is updated; **Given** an IdP that exposes none, **When** users log in,
   **Then** the projection is upserted from observed logins and is clearly marked as best-effort (not an
   authorization source of record).
4. **Given** the mapping uses a non-name-based reference (object GUID or SAML attribute value), **When**
   resolution runs, **Then** it works without assuming a group-name prefix convention.

---

### User Story 6 - The platform degrades safely and predictably when its shared cache is down (Priority: P3)

The shared cache/limit store (Redis) backs rate limiting, the deny-set, idempotency, and spend reservations —
it is a single point of failure for several auth and safety controls. Operators need a documented blast radius
and a deliberate, tested degraded-mode decision for each dependent control, so an outage produces known,
fail-closed behavior rather than silent weakening of security.

**Why this priority**: This is a resilience/operational hardening item (Constitution P8). It is P3 because it
documents and tests behavior of controls delivered in US1–US3 rather than adding new user-facing capability,
but it must land before the milestone is "complete".

**Independent Test**: With the shared store made unavailable, exercise each dependent control (rate limit,
deny-set check, idempotency, spend reservation) and confirm each exhibits its documented degraded behavior
(fail-closed for auth/safety controls), and confirm the blast radius and recovery path are documented.

**Acceptance Scenarios**:

1. **Given** the shared store is unavailable, **When** a security control that depends on it is exercised,
   **Then** the deny-set and spend reservation fail closed (reject) and idempotency fails closed with a
   retryable error, while **rate limiting fails open and raises an alert** (availability control).
2. **Given** a documented degraded-mode decision per dependent control, **When** reviewers read it, **Then**
   each control (rate limit, deny-set, idempotency, spend reservation) has an explicit, tested behavior and a
   stated recovery path.
3. **Given** per-request database connections run through transaction-mode pooling, **When** a request sets
   its tenant context, **Then** that context does not leak across pooled connections and workspace isolation
   holds under the pooled configuration.

### Edge Cases

- A caller alternates between two tokens to evade a per-token limit — limits are per-identity; combined
  platform/provider-quota protection must still bound total expensive work (document the interaction with
  spend caps and provider circuit breakers, F7/F8).
- An allow-listed domain resolves to *multiple* A records, one public and one private — connection must be
  validated against the address actually dialed, not just the first resolved record.
- A token is revoked while a long streaming invoke is already in flight — the stream is **cut immediately** on
  detected revocation (clarified 2026-11-11), not allowed to run to completion.
- An admin revokes a subject whose access-token TTL is longer than the deny-set TTL — deny-set TTL must be ≥
  max access-token TTL so a revoked subject cannot outlive the set.
- A user's IdP groups change between logins — resolved role/team must reflect the latest login; the projection
  must not grant access it no longer should.
- The IdP is briefly unreachable during login — define user-visible failure and retry (do not fail open).

## Requirements *(mandatory)*

### Functional Requirements

**Rate limiting (US1 / F5)**

- **FR-001**: The system MUST enforce a per-identity request rate limit on published invoke tokens using a
  sliding window, rejecting over-limit requests before any provider call.
- **FR-002**: The system MUST enforce per-principal rate limits on expensive console/builder routes
  (at minimum playground, eval, and ingestion triggers), rejecting over-limit requests before expensive work
  begins.
- **FR-003**: The system MUST maintain a reviewable limits table defining the window and threshold per
  protected surface, with conservative defaults (see Assumptions).
- **FR-004**: A throttled request MUST return a standard "too many requests" outcome that communicates when to
  retry and the caller's remaining quota.
- **FR-005**: Rate limits MUST be scoped per identity so one caller's throttling does not affect another's.

**Egress / SSRF guard (US2 / F19)**

- **FR-006**: The system MUST refuse outbound HTTP tool egress to private, loopback, link-local, and
  cloud-metadata addresses, validated against the address actually connected to.
- **FR-007**: The system MUST NOT follow a redirect from an allowed domain to an internal/disallowed host.
- **FR-008**: The system MUST validate the resolved IP (not only the hostname) to defeat DNS-rebinding, and
  enforce the configured per-tool domain allow-list.
- **FR-009**: Refused egress MUST be recorded for diagnostics without leaking secrets or sensitive internal
  target detail to client-visible errors.

**Caller token plane + immediate revocation (US3)**

- **FR-010**: Publishers (and admins) MUST be able to mint caller tokens; the secret MUST be shown exactly
  once and only a non-reversible hash plus a display prefix persisted.
- **FR-011**: Token secrets MUST never appear in logs, traces, or exports.
- **FR-012**: Revoking a token MUST reject its subsequent use immediately (authoritative revocation recorded
  durably, checked on every call). An **in-flight streaming invoke MUST be cut** as soon as revocation (token
  revoke or subject deny-set) is detected, not allowed to run to completion.
- **FR-013**: The system MUST provide an admin action that immediately revokes a subject's access, rejecting
  both active console sessions and live caller tokens for that subject on their next request via a transient
  deny-set checked on the per-request auth path.
- **FR-014**: The maximum console session / access-token lifetime MUST be ≤ 1 hour, and the deny-set entry TTL
  MUST be at least that maximum lifetime (≥ 1 hour) so a revoked subject cannot outlive the set.
- **FR-015**: Tokens MUST support an optional expiry, after which use is rejected.
- **FR-016**: Caller-token mint/revoke MUST require at least publisher authority; subject revoke/force-logout
  MUST require admin authority.

**OIDC console login (US4)**

- **FR-017**: The system MUST authenticate console users via an OIDC authorization-code flow and establish a
  server-validated session.
- **FR-018**: Every console request MUST validate the session/token server-side (issuer, audience, expiry) and
  check the deny-set before proceeding.
- **FR-019**: State-changing console requests MUST require a valid anti-CSRF token.
- **FR-020**: Users MUST be able to log out, ending the session.

**IdP group → role/team mapping + projection (US5)**

- **FR-021**: The system MUST sync a user's workspace roles and teams **into the members store** (the authority
  under RLS, per 012/013) from their IdP groups using a per-workspace mapping, without assuming any
  vendor-specific group-naming convention (support name, object GUID, and attribute-value references). Role/team
  authority MUST NOT be resolved live from IdP group claims per request.
- **FR-022**: A user whose IdP groups match no mapping for a workspace MUST receive no role and no team there
  (fail-closed default) after the sync.
- **FR-023**: The system MUST maintain a best-effort group-membership projection for notification fan-out,
  populated from group-member enumeration where the IdP supports it and from observed logins otherwise, and
  MUST NOT treat this projection as an authorization source of record.

**Resilience / tenancy hardening (US6)**

- **FR-024**: When the shared cache is unavailable, security controls that depend on it — the **deny-set** and
  the **spend reservation** — MUST fail closed (reject). **Rate limiting MUST fail open and raise an alert**
  (it is an availability/cost control; failing it closed would turn a cache blip into a self-inflicted outage).
  Idempotency MUST fail closed with a retryable error. Each control's degraded behavior MUST be explicit and
  tested.
- **FR-025**: The shared-store blast radius and a per-control degraded-mode decision MUST be documented and
  verified by test.
- **FR-026**: Per-request database connections MUST run through transaction-mode pooling without leaking
  tenant context across pooled connections; workspace isolation MUST hold under the pooled configuration.

**Cross-cutting**

- **FR-027**: All new authorization and safety decisions MUST be enforced in deterministic server-side code
  (not model instructions) and MUST fail closed on error (Constitution P3).
- **FR-028**: No credential, token secret, or session secret may leak into prompts, exports, logs, traces, or
  client-visible errors (Constitution P5).

### Key Entities *(include if feature involves data)*

- **Caller token**: a minted credential authorizing execute-only invoke of a published agent; stored as a
  non-reversible hash + display prefix, bound to a workspace, with optional expiry and a revoked marker.
- **Deny-set entry**: a transient revocation record keyed by subject and/or token id, with a TTL ≥ max token
  lifetime (≥ 1 hour, since session/token lifetime ≤ 1 hour); presence rejects the matching session/token
  immediately, including cutting an in-flight stream.
- **Rate-limit counter**: a per-identity, per-surface sliding-window count used to decide throttling.
- **Console session**: a server-validated interactive user session established via OIDC authorization-code
  login, carrying the authenticated subject.
- **Workspace IdP binding**: the per-workspace mapping from workspace roles and teams to native IdP group
  references (name, GUID, or attribute value).
- **Group-membership projection**: a best-effort, cache-only read model of group→subject membership for
  notification fan-out; explicitly not an authorization source of record.
- **Egress allow-list / policy**: the per-tool set of permitted destinations and the rules used to refuse
  private/internal targets and unsafe redirects.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of requests exceeding a configured limit on any protected surface are rejected with retry
  guidance before any provider or ingestion work begins.
- **SC-002**: A single caller cannot consume more than its configured share of a protected surface in a
  window; a second caller on the same surface is unaffected in 100% of tested cases.
- **SC-003**: 100% of attempted tool egress to private/internal/metadata addresses (direct, via redirect, and
  via DNS-rebinding) is refused, with zero bytes sent to the blocked target.
- **SC-004**: A revoked token or a deny-set-listed subject is rejected on the very next request (0 successful
  requests after revocation), and revocation takes effect without waiting for the next IdP login.
- **SC-005**: A one-time-shown token secret never appears in any log, trace, or export (verified by secret
  scan across those sinks).
- **SC-006**: Console users complete an IdP authorization-code login and reach an authenticated session; every
  protected route rejects unauthenticated/CSRF-missing state-changing requests in 100% of tested cases.
- **SC-007**: Workspace role/team sync from IdP groups into the members store is correct for every mapping case
  tested (name/GUID/attribute) and grants nothing when no mapping matches; no live per-request group→role
  resolution path exists.
- **SC-008**: With the shared store unavailable, the deny-set and spend reservation fail closed (0 requests
  allowed through unchecked) while rate limiting fails open with an alert raised; tenant isolation holds under
  transaction-mode pooling (0 cross-tenant reads in negative tests).

## Out of Scope

- **Tool approval gate (deferred).** Human-in-the-loop approval for high-risk tool calls
  (`tool_approvals` lifecycle, the `tool_approval_gate`, `approval_required` surfacing, and the
  `approval_requested` notification) was considered for this milestone and **carved out on 2026-11-11**. It
  depends on real tool **execution**, which is itself P2/deferred — the runtime today *surfaces* a valid tool
  call but does not execute it, so the gate cannot be exercised end to end. It returns as its own feature once
  tool execution lands. This keeps the capability deferred (its prior state), not silently dropped
  (Constitution P2); no `docs/scope.md` phase change is implied.
- **Egress sandbox / gVisor (P2).** US2 delivers the P1 *app-level* egress guard only; the network sandbox
  remains P2.
- **Console user provisioning beyond login + group mapping** (e.g. IdP-pushed deprovisioning / SCIM) stays
  deferred; the deny-set covers admin-triggered immediate revocation.

## Assumptions

- **Decomposition at plan time.** This epic is expected to be split by `/speckit-plan` into dependency-ordered
  sub-features. A reasonable candidate split, by priority and dependency: **(a)** rate limiting (US1/F5);
  **(b)** egress/SSRF guard (US2/F19); **(c)** caller-token plane + deny-set + immediate revocation (US3);
  **(d)** OIDC console login + session + CSRF (US4); **(e)** IdP group→role/team mapping + projection (US5);
  **(f)** Redis-SPOF degraded-mode + PgBouncer txn-mode (US6). US1–US3 are the un-built High-severity P1
  slices and should land first.
- **Rate-limit defaults.** Exact thresholds are not frozen in the design. Assume conservative per-token and
  per-principal defaults (overridable via the limits table) and tune against pilot traffic; the gate is that
  limits exist, are enforced before expensive work, and are reviewable — not a specific number.
- **Shared cache = Redis** as designed; it backs rate limiting, deny-set, idempotency, and spend reservations.
  Its single-point-of-failure status is accepted for the P1 pilot provided each dependent control has an
  explicit, tested degraded behavior (deny-set + spend reservation fail closed; idempotency fails closed with a
  retryable error; rate limiting fails open with an alert) and the blast radius is documented (US6).
- **IdP adapters.** The portable baseline is generic OIDC plus the already-built dev and Trellix-IAM
  providers; the per-workspace binding carries the group mapping. SAML and additional vendors reuse the same
  adapter seam and are not required for this milestone unless an owner decision adds one.
- **Secret custody is already closed** (feature 014, pluggable secrets backend); this spec does not re-open
  it and all "Vault" references in M5 source are read as "the secrets backend".
- **Reuse over rebuild.** Existing enforced layers — RLS, RBAC (008), per-resource access policy (013), IdP
  bearer authN (012) — are the substrate; this feature composes with them and does not replace them.
- Design sources: `docs/backend/09-security-tenancy.md` (+ `09.lld`), `docs/backend/18-identity-adapters.md`
  (+ `18.lld`), `docs/backend/01-data-model.md` (`api_tokens`, `group_members`), and findings F5/F7/F8/F19 in
  `docs/implementation-plan.md`.
