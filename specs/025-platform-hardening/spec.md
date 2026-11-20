# Feature Specification: Platform Hardening — Provider Resilience + Binding Integrity

**Feature Branch**: `feat/backend-m0-foundation` (spec dir `specs/025-platform-hardening`)
**Created**: 2026-11-15
**Status**: Draft
**Input**: User description: "next milestone. verify old milestones are good" → owner decision 2026-11-15: "lets start
with recommendations and lets give priority to MUST tier" (close M7 at the Must tier, then fix the orphaned gaps the
2026-11-15 milestone audit found before starting M8).

## Context

The 2026-11-15 code-evidence audit of M0–M6 (recorded under the module status table in
`docs/implementation-plan.md`) found two high-risk gaps that no milestone or spec owns:

1. **M1 — provider calls have no resilience.** A provider error is labelled "retryable" and then handed straight to
   the caller: there is no retry, no backoff, no circuit breaker, and nothing stops evaluation traffic from tripping
   failures that starve live traffic (plan findings F8, F10, F11).
2. **M0 — a frozen agent version's bindings can drift.** Skill and tool bindings point at catalog entries without any
   referential check, binding rows can be edited after their version is frozen, and the integrity checks the plan
   promised (F17) do not exist. The snapshot that evaluations certified can therefore differ from what actually runs.

This feature closes both at the **Must tier**; everything else the audit found is listed under *Scope* as Nice/deferred.

## Scope (Must vs Nice)

| # | Item | Tier | Why / impact | Size |
|---|------|------|--------------|------|
| 1 | Bounded retry with backoff + jitter on transient provider failures, only before the first streamed token (F10) | **Must** | One provider 429/5xx currently fails the run | M |
| 2 | Honour provider `Retry-After` but cap the total wait inside the run deadline (F11) | **Must** | An adversarial/huge wait must not hang a request | S |
| 3 | Circuit breaker per provider+model, shared across API instances, closed → open → half-open (single trial) | **Must** | Stops hammering a down provider; fast, honest failure | M |
| 4 | Bulkhead: evaluation traffic has its own breaker scope and concurrency cap so it cannot open the breaker for live traffic (F8) | **Must** | Eval fan-out is the most likely trigger | S |
| 5 | Binding references must exist (skills, tools, MCP tools, knowledge bases) | **Must** | Dangling bindings = broken or wrong agent at run time | S |
| 6 | Bindings of a frozen version are immutable (edits rejected loudly, not silently dropped — F15 parity) | **Must** | The certified snapshot must equal what runs | S |
| 7 | Integrity checks (F17): a tool binding names exactly one target for its kind; an agent's active version belongs to that agent; chunk vector size matches the knowledge base's declared size | **Must** | Schema-level guarantees, not convention | S |
| 8 | Fallback to an alternate model with re-priced cost recorded as a substitution | Nice | Needs a per-agent fallback policy + drift trigger | M |
| 9 | Durable (cross-instance) idempotency replay for creates (M4 F14) | Nice | Only matters once >1 API replica (M11) | M |
| 10 | `agent_versions` status / created_by / note / unique label (M0 a) | Nice | Audit trail + duplicate-label guard | S |
| 11 | Snapshot hash computed from the validated model (F16) | Nice | Current canonicalization already stable for numbers | S |
| 12 | Eval budget, spend reservation + TTL reaper, 80/90% alerts (M6 F7) | Nice → own feature | Distinct domain (spend), not resilience | L |
| 13 | Eval persistence / verdict cache / async worker (M3) | Nice → own feature | Prerequisite for a real publish gate | L |

Items 8–13 are **out of scope** for this feature unless the owner pulls them in.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A brief provider hiccup does not fail the user's run (Priority: P1)

An end user (playground, published endpoint, or channel) sends a message while the model provider returns a
transient error (rate limit or temporary server error) for a few seconds. The platform quietly retries and the user
gets a normal answer; nothing streamed to them is duplicated or garbled.

**Why this priority**: Today every transient provider error is a failed run visible to the end user. This is the
single most common production failure for an LLM platform.

**Independent Test**: With a fake provider that fails the first N attempts with a transient error and then succeeds,
invoke an agent and observe one successful answer, the retry count recorded on the run, and a cost that reflects only
billed attempts.

**Acceptance Scenarios**:

1. **Given** the provider fails twice with a transient error before sending any output, **When** a user invokes an
   agent, **Then** the run succeeds, the answer is delivered once, and the run record shows 2 retries.
2. **Given** the provider fails with a non-transient error (bad request, auth failure, unknown model), **When** a
   user invokes an agent, **Then** the platform does not retry and returns the existing typed error immediately.
3. **Given** the provider has already streamed part of an answer and then fails, **When** the failure occurs,
   **Then** the platform does not retry that turn, the user receives no duplicated text, and the run ends with the
   existing provider-error outcome (the text already delivered is not re-sent).
4. **Given** the provider answers a retry with a "retry after 10 minutes" instruction, **When** the run deadline is
   30 seconds, **Then** the platform stops waiting within the deadline and fails the run with a timeout/provider error
   rather than hanging.
5. **Given** every retry attempt fails, **When** the retry budget is exhausted, **Then** the run fails with the
   provider error and the total number of attempts never exceeds the configured maximum.

---

### User Story 2 - A down provider fails fast instead of piling up (Priority: P1)

A platform operator sees a provider/model go hard down. Instead of every request waiting for timeouts and retrying,
the platform detects the outage after a small number of consecutive failures, immediately refuses further calls to
that provider+model with a clear "provider unavailable" error, probes with a single trial call after a cool-down,
and resumes normal traffic as soon as the trial succeeds — consistently across all API instances.

**Why this priority**: Without a breaker, an outage multiplies load (retries) and latency for every caller and burns
spend on doomed calls.

**Independent Test**: With a fake provider that always fails, send requests until the breaker opens; confirm the next
request fails immediately without contacting the provider, that after the cool-down exactly one trial call is made,
and that a successful trial closes the breaker for all instances.

**Acceptance Scenarios**:

1. **Given** a provider+model has failed transiently N consecutive times (default 5), **When** the next request
   arrives, **Then** it is refused immediately with a typed "provider unavailable" error and the provider is not
   contacted.
2. **Given** the breaker is open and the cool-down has elapsed, **When** several requests arrive at once, **Then**
   exactly one is allowed through as a trial and the rest are refused until the trial resolves.
3. **Given** the trial succeeds, **When** subsequent requests arrive on any API instance, **Then** they flow normally.
4. **Given** the breaker for model A is open, **When** a request targets model B on the same provider, **Then** it is
   unaffected.
5. **Given** the shared breaker state is temporarily unreachable, **When** requests arrive, **Then** each instance
   falls back to its own local breaker (availability control — fails open, consistent with the 021 degraded-mode
   record) and an alert metric is incremented.

---

### User Story 3 - Evaluation load cannot take down live traffic (Priority: P1)

A builder runs a large evaluation suite against the same model that serves production. Eval failures and eval
concurrency are contained: they can open the *evaluation* breaker and queue eval calls, but live traffic on the same
model keeps its own breaker and capacity.

**Why this priority**: Eval fan-out is the most likely trigger of provider rate limits; letting it open the
production breaker turns a test run into a customer outage (plan finding F8).

**Independent Test**: Drive eval-sourced calls into a failing fake provider until the eval breaker opens, then invoke
the same model from the playground/published plane and observe it is still attempted; and observe that eval calls
never exceed their concurrency cap.

**Acceptance Scenarios**:

1. **Given** eval traffic has opened the breaker for model A, **When** a live (playground/published/channel) request
   targets model A, **Then** it is attempted normally.
2. **Given** an eval run with 50 cases, **When** it executes, **Then** at most the configured number of eval provider
   calls are in flight at once (default a small fixed cap) and the rest wait rather than fail.
3. **Given** live traffic opens the breaker for model A, **When** an eval case targets model A, **Then** the eval
   case is also refused (a genuinely down provider is down for everyone).

---

### User Story 4 - What an evaluation certified is exactly what runs (Priority: P1)

A builder publishes an agent version that passed evaluation. No later edit to the catalog or to binding rows can
silently change what that version uses: references to skills, tools, MCP tools and knowledge bases always point at
something that exists, and any attempt to edit a frozen version's bindings is rejected with a clear error.

**Why this priority**: Reproducibility is constitution principle 6; the drift engine is only meaningful if the
snapshot it evaluated is the one that runs.

**Independent Test**: Try to insert a binding to a non-existent skill, edit a binding row of a frozen version, and
create a tool binding naming two targets; each is rejected at the data layer with a typed, user-readable error.

**Acceptance Scenarios**:

1. **Given** an agent version, **When** a binding references a skill, tool, MCP tool or knowledge base that does not
   exist in the same workspace, **Then** creation is rejected (API: 400/404 envelope; data layer: refused).
2. **Given** any committed version (every version is frozen from insert — F15), **When** anyone updates or deletes one of its binding rows,
   **Then** the change is rejected loudly (never silently ignored) and the version's snapshot hash is unchanged.
3. **Given** a tool binding of a given kind, **When** it names zero or more than one target, **Then** it is rejected.
4. **Given** an agent, **When** its active version is set to a version belonging to another agent, **Then** it is
   rejected.
5. **Given** a knowledge base declaring a vector size, **When** a chunk with a different vector size is stored,
   **Then** it is rejected.
6. **Given** a catalog entry (skill/tool) referenced by any version, **When** someone tries to hard-delete it,
   **Then** it is refused; archiving (the existing soft path) still works.

### Edge Cases

- A retry succeeds after failed attempts → run cost is computed only from usage the provider *reported*; a failed
  attempt that reported no usage is unpriced (the platform cannot observe whether the provider billed it). This is
  disclosed, not hidden: the run carries its retry count and the cost field is documented as "reported usage only"
  (constitution 7 — uncertain cost is labelled, never presented as the exact charge).
- An ingestion embedding refused by an open breaker fails the ingestion job as retryable (typed), never as a crash or
  a silent success.
- Retries must respect the overall run deadline: retry waits count against it.
- A provider returns `Retry-After` as an HTTP date in the past, zero, negative, or non-numeric → treated as "no
  hint" (use the backoff schedule), never as an infinite or negative wait.
- The breaker counts only transient/provider-side failures; caller errors (invalid input, context overflow, auth to
  the provider, unknown model) never open it.
- Judge calls (eval scoring) count as evaluation traffic for the bulkhead.
- Embedding calls (ingestion, query embedding) use the same retry + breaker policy, keyed by the embedding model,
  with ingestion classed with evaluation (batch) traffic, not live traffic.
- Existing data that violates the new integrity rules (e.g. dangling bindings in a dev database): the upgrade
  reports the offending rows and refuses to apply rather than deleting data silently.
- Concurrent breaker updates from many instances must not lose failure counts.
- A request refused by an open breaker creates a run record with a distinct outcome and zero cost (visible in run
  metrics), and is counted in a metric.

## Requirements *(mandatory)*

### Functional Requirements

**Provider resilience (US1–US3)**

- **FR-001**: The platform MUST retry a provider call that fails with a transient error (rate limit, temporary
  server error, timeout/connection error) **only if no output has been delivered to the caller for that turn**.
- **FR-002**: Retries MUST use exponential backoff with jitter, a configurable maximum attempt count (default 3 total
  attempts), and a per-wait ceiling.
- **FR-003**: A provider `Retry-After` hint MUST be honoured when it is valid, but every individual wait MUST be
  capped, and the cumulative wait MUST never exceed the remaining run deadline; malformed hints fall back to backoff.
- **FR-004**: Non-transient failures (invalid request, provider authentication, unknown/retired model, content
  policy refusal) MUST NOT be retried.
- **FR-005**: A failure after output has started streaming MUST NOT be retried and MUST NOT produce duplicated
  output; the run ends with the existing provider-error outcome.
- **FR-006**: The run record MUST carry the number of retries and the outcome MUST distinguish "refused: provider
  unavailable (breaker open)" from other provider errors.
- **FR-007**: The platform MUST maintain a circuit breaker per (provider, model, traffic class) with states closed →
  open → half-open; it opens after N consecutive transient failures (default 5), stays open for a cool-down
  (default 30 s), then admits exactly one trial call; a successful trial closes it, a failed trial re-opens it.
- **FR-008**: Breaker state MUST be shared across API instances and MUST NOT lose counts under concurrent updates.
- **FR-009**: If the shared breaker state is unreachable, each instance MUST fall back to a local in-process breaker
  (fail open for availability) and MUST increment a degraded-mode metric; this behaviour MUST be added to the
  degraded-mode decision record (`docs/backend/degraded-mode.md`).
- **FR-010**: Traffic MUST be classed as **live** (playground, published, channel) or **batch** (evaluation cases,
  judge, ingestion embeddings); each class has its own breaker scope, and batch traffic MUST have a configurable
  concurrency cap per (provider, model) (default 4) that queues rather than fails.
- **FR-011**: A breaker opened by live traffic MUST also refuse batch traffic for that provider+model; a breaker
  opened by batch traffic MUST NOT refuse live traffic.
- **FR-012**: The same retry + breaker policy MUST apply to embedding calls.
- **FR-013**: The platform MUST expose metrics for retries, breaker state transitions, breaker refusals, and batch
  queue waits, with no tenant content in labels.
- **FR-014**: All resilience logic MUST live behind the single shared invocation path so that playground, published
  invoke, channels, and eval cases behave identically (constitution P6 parity); the runtime stays free of web-framework
  dependencies, with shared-state access injected.
- **FR-015**: Cost MUST be computed only from usage the provider reported (every successful attempt, plus any failed
  attempt that reported usage); failed attempts with no reported usage are unpriced and MUST be disclosed via the run's
  retry count and a documented "reported usage only" cost basis — never presented as the exact provider charge.

**Binding integrity (US4)**

- **FR-016**: Every skill, tool, MCP-tool and knowledge-base binding MUST reference an existing entry in the same
  workspace, enforced at the data layer (not only in the API).
- **FR-017**: Catalog entries referenced by any binding MUST NOT be hard-deletable; the existing archive path remains.
- **FR-018**: Binding rows belonging to a frozen version MUST be immutable: update and delete attempts MUST be
  rejected with an error (never silently dropped), matching the existing frozen-version behaviour (F15).
- **FR-019**: A tool binding MUST name exactly one target appropriate to its kind.
- **FR-020**: An agent's active version MUST belong to that agent.
- **FR-021**: A stored chunk's vector size MUST equal its knowledge base's declared vector size, and creating a
  knowledge base with a vector size the platform cannot store (today: anything other than 1024) MUST be rejected at
  creation with a typed input error.
- **FR-022**: The upgrade that introduces FR-016–FR-021 MUST detect pre-existing violations, report them, and refuse
  to apply rather than delete or rewrite data.
- **FR-023**: The data-layer integrity violations introduced by FR-016–FR-021, when surfaced through the API, MUST map
  to the existing typed error envelope (400/404/409), never a raw 500. Constraint violations outside this feature keep
  the existing unhandled-error path (an honest 500, never a guessed 4xx).

### Key Entities

- **Breaker state**: per (provider, model, traffic class): state, consecutive-failure count, opened-at, trial-in-flight
  marker. Shared across instances; contains no tenant data.
- **Retry policy**: max attempts, backoff base/ceiling, jitter, per-wait cap — deployment configuration.
- **Batch concurrency limit**: per (provider, model) cap for batch traffic — deployment configuration.
- **Run record** (existing): gains a retry count and a "breaker refused" outcome.
- **Bindings** (existing: skill, tool, retrieval): gain enforced references and frozen-version immutability.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With a simulated provider that fails transiently on the first 2 attempts, 100% of invocations succeed
  and 0 duplicated output fragments reach the caller (fake-provider test across all four callers).
- **SC-002**: With an adversarial "retry after" of 1 hour, every request completes or fails within its run deadline
  plus 1 second.
- **SC-003**: With a provider+model hard down, after the breaker opens, ≥ 99% of further requests fail in under
  50 ms without contacting the provider, and exactly 1 trial call is made per cool-down window across 3 concurrent
  instances.
- **SC-004**: Under an eval run that drives the batch breaker open, 100% of concurrent live requests to the same model
  are still attempted, and batch in-flight calls never exceed the configured cap.
- **SC-005**: 100% of the integrity violations listed in US4 (dangling reference, frozen-binding edit, multi-target
  tool binding, cross-agent active version, wrong vector size, hard-delete of referenced entry) are rejected at the
  data layer in tests, each with a typed API error where reachable through the API.
- **SC-006**: Zero regressions: the full existing deterministic and integration suites stay green.

## Assumptions

- No live or paid provider calls: all resilience behaviour is proven against fake providers / fault-injecting
  transport stubs (consistent with every prior feature).
- Shared breaker state uses the existing shared cache already used by rate limiting and the deny-set; its outage
  behaviour follows the documented degraded-mode policy (availability controls fail open + alert).
- Default thresholds (3 attempts, 5 consecutive failures, 30 s cool-down, batch cap 4) are conservative starting
  values, tunable by configuration; they are not yet validated against real provider traffic.
- "Frozen" means the existing frozen-version definition already enforced on `agent_versions` (F15); in the built code
  that is every committed version (research G3), so binding immutability applies to all committed versions.
- There is no sub-agent catalog yet, so sub-agent binding references stay unconstrained until that catalog exists.
- The pilot runs a single API replica; cross-instance behaviour is proven in tests with multiple simulated instances.
- Fallback-to-alternate-model, durable idempotency, eval budget, and eval persistence are explicitly out of scope
  (Scope items 8–13).
