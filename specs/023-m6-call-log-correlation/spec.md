# Feature Specification: Per-Call Model Log + End-to-End Correlation + Latency SLIs (M6 remainder, slice 1)

**Feature Branch**: `feat/backend-m0-foundation` (slice 023; no per-feature branch)

**Created**: 2026-11-14

**Status**: Draft

**Input**: User description: "let's start on next milestone. check if we can start m6" → readiness check found M6
unblocked (M4/M5 closed; M6 slices 009 spend cap and 010 run metrics + inbound correlation shipped). User selected
the M6-remainder slice: **one per-call model log line (spec A2 contract) + correlation carried end-to-end to the
outbound provider call + deadline-rate and first-token latency SLIs.** Langfuse/ClickHouse tracing and budget
reservation are separate later slices.

## Context

The platform's thesis rests on "test == eval == prod": playground, published invoke, and every eval/judge case run
the same model-calling path. Today a run produces one honest run record (cost, latency, status, correlation id), but
there is **no record per individual model call**, the correlation id **stops at the run record** (the provider call
carries nothing that ties it back), and the two headline latency signals in the observability design — **first-token
latency** (the playground SLO) and the **deadline-cut rate** — are not measured. Product requirement A2 is a hard
contract: *every model call logs model, prompt version, input, output, latency, cost, success/fail.* It is unmet.
This slice closes that contract and the remaining halves of findings F12 (SLIs) and F13 (correlation).

## Clarifications

### Session 2026-11-14

- Q: Should the per-call log line include prompt/output text (redacted + truncated, per the observability design and
  requirement A2), or metadata only (sizes + content hashes), given the constitution's data-minimization principle?
  → A: **Metadata by default; redacted + truncated text only behind an explicit opt-in.** By default a per-call
  record carries, for input and output, a character count and a content fingerprint — never the text. Redacted,
  truncated text is included only when an operator explicitly enables it (the `dev` profile only — not staging or any
  other non-prod profile — or a per-workspace
  debug setting that is off by default). A2's "input, output" is satisfied as "identifiable by default, readable on
  opt-in"; this interpretation is to be recorded in the scope decision log during planning.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Every model call leaves one auditable record (Priority: P1)

An operator investigating a cost spike, a bad answer, or a provider incident needs to see each individual model call
— which model, which prompt version, how long it took, what it cost, and whether it succeeded — regardless of whether
the call came from the playground, a published endpoint, an eval case, or the eval judge. Each record must tie back to
its run, workspace, agent, and version, and its cost must match the billable run record exactly so the two can be
reconciled without rounding arguments.

**Why this priority**: It is the unmet product contract (A2) and the prerequisite for auditing any real provider
spend; without it, cost and quality investigations rely on the run aggregate only.

**Independent Test**: Run one playground invocation, one published invocation, and one eval run with a judge against a
stub provider; confirm exactly one record per model call, all with the same shape, each carrying the required fields,
and that the per-call costs of a run sum exactly to that run's billed cost.

**Acceptance Scenarios**:

1. **Given** an agent invoked from the playground, **When** the run completes, **Then** exactly one per-call record
   exists for each model call in that run, carrying model, prompt version, latency, cost, success, and the run,
   workspace, agent, version, source, and correlation identifiers.
2. **Given** the same agent invoked through the published endpoint and through an eval case (including its judge
   call), **When** those runs complete, **Then** their per-call records have an identical set of fields to the
   playground's — no caller-specific shape.
3. **Given** a run whose model call fails (provider error, timeout, or deadline cut), **When** the run ends, **Then**
   a per-call record still exists for the failed call with success = false and its partial latency and cost.
4. **Given** any completed run, **When** its per-call costs are summed, **Then** the total equals the run's billed cost
   exactly (same precision, no re-derivation).

---

### User Story 2 - One correlation id follows a request all the way to the provider (Priority: P2)

A support engineer receives a user's complaint carrying a correlation id (or a trace id from an upstream system). They
need to find the inbound request, the run, every per-call record, and the outbound provider request for that one
id — without guessing by timestamps.

**Why this priority**: Correlation already reaches the run record (slice 010); extending it to per-call records and
the provider call completes finding F13 and makes provider-side incidents traceable. Valuable, but only once
per-call records (US1) exist.

**Independent Test**: Send a request with a known inbound correlation id; confirm the same id appears on the run
record and every per-call record, and that every outbound request to the (stub) provider carries a platform-generated
id (never the caller's) which is recorded on those same per-call records; repeat without an inbound id and confirm
one generated id is used consistently across all three.

**Acceptance Scenarios**:

1. **Given** a request carrying a valid inbound correlation id, **When** it triggers a run with model calls, **Then**
   the run record and every per-call record carry that same id, and every outbound provider request carries a
   **platform-generated** provider correlation id (never the caller-supplied value) that is also recorded on every
   per-call record of the run.
2. **Given** a request with no or a malformed inbound id, **When** the run executes, **Then** a single generated id is
   used consistently across the run, its per-call records, and its outbound provider requests (the provider
   correlation id equals the generated correlation id).
3. **Given** an eval run with N cases, **When** it executes, **Then** all model calls of that eval run (cases and
   judge) share the eval run's correlation id, so one id retrieves the whole evaluation.

---

### User Story 3 - Operators can see first-token latency and deadline cuts (Priority: P3)

An operator responsible for the playground experience needs two signals the design names as headline SLIs: how fast
the first token arrives (target: 95th percentile under 2 seconds for the playground), and how often runs are cut by the
hard wall-clock deadline (alert when more than 1% of runs). Both must be visible per caller source alongside the
existing run-outcome counters.

**Why this priority**: Completes finding F12; enables the latency SLO and the deadline-regression alert. Lower than
US1/US2 because run-outcome counters (the availability SLI) already exist.

**Independent Test**: Drive a stub provider with a controlled first-token delay and one run that exceeds the deadline;
confirm the operator metrics endpoint reports the first-token observation in the correct latency band and increments
the deadline-cut count for the right source.

**Acceptance Scenarios**:

1. **Given** a streaming run whose first token arrives after a known delay, **When** the run completes, **Then** the
   first-token latency is recorded once for that run's source.
2. **Given** a run cut by the wall-clock deadline, **When** it ends, **Then** the deadline-cut count increments by
   exactly one for that source, and the run's outcome counter records the deadline outcome (no double counting as a
   generic error).
3. **Given** the metrics surface, **When** an operator reads it, **Then** it exposes only aggregate counts and latency
   distributions — no tenant identifiers, prompt content, or user data.

---

### Edge Cases

- **Multi-call runs** (agentic loops / repair retries): one per-call record per model call; their costs still sum to
  the run total.
- **Run with zero model calls** (refused before the provider — e.g. 402 over budget, 403, deadline elapsed before
  start): no per-call record; existing refusal counters still apply.
- **Logging/metrics failure** (log sink unavailable, serialization error, redaction failure): the run still completes
  and its billable record is still written; telemetry failure is counted/alerted, never surfaced as a run failure.
- **Redaction failure**: the affected text field is dropped (replaced by a fixed marker), never written raw.
- **Very large prompts/outputs**: any text carried in the record is truncated to a fixed cap.
- **Credentials or provider keys in prompt text**: never appear in a per-call record or outbound correlation header.
- **Client disconnect mid-stream**: the per-call record is still written with the partial latency/cost and an
  explicit non-success outcome.
- **Inbound correlation id that is syntactically valid but attacker-chosen**: accepted as an opaque label only; it
  never influences authorization, tenancy, or routing, and is **never forwarded to a provider** — providers only ever
  see a platform-generated id (FR-014), so a caller cannot smuggle tenant/user data to a third party through it.
- **Provider without a request-metadata channel**: correlation is still recorded on our side; outbound propagation is
  best-effort per provider and its absence is documented, not an error.

## Requirements *(mandatory)*

### Functional Requirements

**Per-call record (US1)**

- **FR-001**: The system MUST emit exactly one per-call record for every model call made during a run, including
  failed, timed-out, deadline-cut, and client-disconnected calls.
- **FR-002**: Each per-call record MUST carry the A2 contract fields — model identity, prompt version, latency, cost,
  success/fail — and, for input and output, a character count and a content fingerprint (Clarifications Q1).
- **FR-002a**: By default a per-call record MUST NOT contain any prompt or output text.
- **FR-002b**: Redacted, truncated prompt/output text MUST be included only when explicitly enabled — in the
  `dev` profile (exactly — shared non-prod environments such as staging behave like prod), or by a per-workspace debug setting that defaults to off and can only be changed by an
  administrator; enabling or disabling it MUST itself be recorded (who, when, which workspace).
- **FR-002c**: The content fingerprint MUST allow matching identical content across records (e.g. "same prompt
  as before") without revealing the content, and MUST NOT be reversible to the text by guessing short values
  (keyed, not a plain hash).
- **FR-003**: Each per-call record MUST carry the context needed to reconcile it: run id, correlation id, workspace,
  agent, agent version, caller source (playground, published API, eval), and call role (candidate, judge).
- **FR-004**: Per-call records MUST be produced from the single shared model-calling path that all callers use, so
  their shape is identical across playground, published invoke, eval cases, and judge calls; no caller may emit or
  reshape its own.
- **FR-005**: The cost on each per-call record MUST be the same figure (same precision, same value) that contributes to
  the run's billed cost; the per-call costs of a run MUST sum exactly to the run's billed cost.
- **FR-006**: When text is included (FR-002b), it MUST pass through redaction of personal data and
  credential-shaped secrets before it is written, and MUST be truncated to a fixed maximum length.
- **FR-007**: If redaction fails, the affected text MUST be replaced with a fixed marker (fail closed); raw text MUST
  never be written as a fallback.
- **FR-008**: Provider API keys and other credentials MUST never appear in a per-call record, regardless of where they
  occur.
- **FR-009**: Per-call record emission MUST be best-effort: a failure to produce or write a record MUST NOT fail,
  delay beyond a bounded amount (record production + write ≤ 1 ms per call at the median, measured — SC-007 evidence),
  or alter the run, its streamed output, or its billable record; such failures MUST be
  counted so operators can alert on them. Residual risk: the write is synchronous to standard output, so a stalled log
  collector can back-pressure the process — accepted for this slice (same exposure as all existing app logging).
- **FR-010**: The redaction capability introduced here MUST be a single shared primitive designed for reuse by the
  future output guardrails, so "what counts as a secret/PII" has one definition.

**Correlation (US2)**

- **FR-011**: The correlation id resolved at the request edge (existing behavior) MUST be carried onto every per-call
  record of the resulting run.
- **FR-012**: Every outbound model-provider request MUST carry the request's **provider correlation id**, using each
  provider's supported request-metadata mechanism where one exists; where none exists, this MUST be documented per
  provider and MUST NOT error. The provider correlation id MUST be recorded on every per-call record, so the
  correlation id → per-call records → provider correlation id lookup is one step.
- **FR-013**: An eval run MUST use one correlation id (and one provider correlation id) for all of its case and judge
  model calls.
- **FR-014**: Only a **platform-generated** id may be sent to a provider: a random, fixed-format identifier (32
  lowercase hex) carrying no tenant, user, or content information. A caller-supplied correlation or trace id MUST NOT
  be forwarded outbound. When the platform generated the request's correlation id itself, the provider correlation id
  MUST equal it; when the caller supplied one, a fresh platform id MUST be generated per inbound request.

**Latency SLIs (US3)**

- **FR-015**: The system MUST record first-token latency once per streaming run, labeled by caller source.
- **FR-016**: The system MUST count runs cut by the wall-clock deadline, labeled by caller source, exactly once per
  such run, consistent with the existing run-outcome counter.
- **FR-017**: The system SHOULD record end-to-end run latency as a distribution labeled by caller source, so the
  p99 ≤ 30-second deadline SLO can be computed.
- **FR-018**: All new metrics MUST be exposed on the existing operator metrics surface as aggregates only — no tenant
  identifiers, user identifiers, or content.

**Boundaries**

- **FR-019**: This slice MUST NOT introduce new external infrastructure; records go to the service's standard
  structured log output for collection by the platform log pipeline.
- **FR-020**: Existing behavior (run records, run-outcome and refusal counters, inbound correlation resolution, the
  spend cap) MUST be unchanged except where extended above.

### Key Entities

- **Per-call record**: One model call's audit line. Attributes: model identity, prompt version, input/output
  character counts + content fingerprints (redacted text only on opt-in), latency, cost, success flag, run id, correlation id, workspace, agent, agent version, caller source,
  call sequence within the run. Disposable operational record — the billable source of truth remains the run record.
- **Content-logging setting**: Per-workspace debug switch (default off, admin-only, changes audited) that permits
  redacted text in that workspace's per-call records; forced on only in the `dev` profile.
- **Correlation id**: Opaque, bounded identifier resolved at the request edge (inbound trace/correlation header or
  generated); shared by the run and all its per-call records.
- **Provider correlation id**: Platform-generated random id (never caller-supplied) sent on every outbound provider
  request and recorded on every per-call record; equals the correlation id when the platform generated that.
- **Run record** (existing): The authoritative billable record; its cost is the sum of its per-call costs.
- **Latency SLIs**: First-token latency distribution, run-latency distribution, deadline-cut count — each labeled by
  caller source only.

## Data Handling *(constitution principle 5)*

| Data | Classification | Purpose | Access | Retention / deletion | External processing |
|---|---|---|---|---|---|
| **Per-call record — default (metadata)**: ids (run, correlation, workspace, agent, version), model, prompt version, latency, cost, outcome, character counts, content fingerprints | **Internal — operational telemetry.** Tenant-identifying (workspace/agent ids) but contains **no customer content** and no credentials. | Cost and quality investigation, incident diagnosis, reconciling per-call cost to the billed run. | Platform operators only, via the platform log pipeline. Not exposed through any product API or console screen; tenants cannot read other tenants' (or their own) lines in-product. | Disposable; kept for the log pipeline's operational window (14–30 days), then expired by the pipeline. Not a billable record — the run record remains the source of truth and is unaffected. No per-record deletion API; workspace offboarding relies on window expiry (documented limitation). | None. Never sent to providers or third parties. |
| **Per-call record — opt-in text**: redacted, truncated prompt/output text (≤ 2,048 chars/field) | **Customer content — Confidential.** May contain personal data that pattern redaction misses (names, addresses). | Debugging a specific workspace's agent behavior when metadata is insufficient. | Same operator-only access as above. Enabled only by that workspace's administrator (or the development profile); every enable/disable is recorded (who, when, which workspace). | Same 14–30 day window; disabling the switch stops new text immediately but does not purge lines already written (they expire with the window). | None beyond the model call the run already made. |
| **Content fingerprint** | Internal — derived, non-reversible (keyed). | Match identical content within one workspace without storing it. | Same as the record it is in. | Same as the record it is in. | None. |
| **Fingerprint key** | **Secret.** | Keys the fingerprints. | Deployment configuration only (secrets backend / environment); never logged or returned by any API. | Rotation changes future fingerprints only (past lines keep old values until they expire). | None. |
| **Content-logging setting** (per workspace, on/off) | Internal — workspace configuration. | Records whether opt-in text is allowed for that workspace. | Read: workspace members (`view`). Write: workspace administrators only. | Lives with the workspace settings; deleted with the workspace. | None. |
| **Correlation id** (possibly caller-supplied) | Internal — opaque identifier; carries no content by platform design, but a caller-supplied value is untrusted. | Tie the request, run, and per-call records together. | Returned to the caller in a response header; stored on the run record and per-call records. | Follows the run record (run) and the log window (per-call records). | **None — never sent to a provider** (FR-014). |
| **Provider correlation id** | Internal — random platform-generated identifier (32 hex); carries no tenant, user, or content information by construction. | Identify our requests in provider-side logs/support cases and tie them back to per-call records. | Stored on per-call records. | Log window. | **Sent to the model provider** as a request header on each provider call — the only new data that leaves the platform in this feature. |
| **Latency/outcome metrics** | Internal — aggregate, non-tenant. | SLIs and alerting. | Operator metrics surface (aggregate only; no tenant/user/content labels). | Per the metrics store's retention (deployment). | None. |

Minimization summary: by default no customer content leaves the model call path into telemetry; text logging is an
explicit, audited, per-workspace exception; credentials are never recorded under any setting.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of model calls across all four caller types (playground, published, eval case, judge) produce
  exactly one per-call record, verified for success, provider-error, timeout, deadline-cut, and disconnect paths.
- **SC-002**: For 100% of tested runs, the per-call costs sum exactly to the run's billed cost (zero difference at the
  billed precision).
- **SC-003**: Given one correlation id, an operator can retrieve the run, all its per-call records, and identify its
  outbound provider requests (via the provider correlation id on those records) in a single lookup — verified for
  both supplied and generated ids, and with zero caller-supplied ids observed in outbound requests. Provider-side
  identification additionally uses the provider's response request id captured onto each record (wire-verified for
  OpenAI + Anthropic in spike T002); where neither is available (e.g. local models), the record's provider
  correlation id + call sequence + timestamp identify the request on our side.
- **SC-004**: Zero credentials and zero unredacted seeded secret/PII samples appear in per-call records across a
  seeded leakage test suite — with content logging both off (no text at all) and on (redacted text only).
- **SC-008**: With default settings, 0% of per-call records contain prompt or output text.
- **SC-005**: Injected telemetry failures (log sink, serialization, redaction, metrics) cause zero failed or altered
  runs and zero lost billable records.
- **SC-006**: Operators can compute playground first-token p95 and the deadline-cut rate per source from the metrics
  surface, with the deadline-cut count matching the number of deadline-outcome runs exactly.
- **SC-007**: Adding per-call logging adds no more than 5% to median run latency against a stub provider.
  ⚠️ *Measured 2026-11-14 (quickstart Evidence): met against a ~50 ms stub (+0.9% content off / +2.4% on); NOT met
  as literally worded against a zero-latency stub (+27 µs ≈ +74%). Proposed re-wording to an absolute budget
  (≤ 1 ms per call) is pending owner decision — criterion left unchanged.*

## Assumptions

- **Scope chosen by the user** from the M6 remainder; Langfuse/ClickHouse tracing (needs infra), budget reservation /
  TTL reaper / eval budget (F7 remainder), per-agent caps, and burn-rate alert rules are out of scope.
- **Sink**: per-call records are structured lines on standard output, collected by the platform log pipeline;
  retention follows the design's 14–30 day operational horizon and is a deployment concern, not enforced in-app.
- **Single emission point** is the shared model-calling path all callers already use (the eval judge already routes
  through it), so no caller code needs its own logging.
- **Redaction**: no redaction primitive exists today (the guardrails layer is unbuilt); this slice introduces a minimal
  shared one covering credential-shaped secrets and common PII patterns, to be extended by guardrails later.
- **Truncation cap** for opt-in text defaults to 2,048 characters per field (observability design value).
- **Content default (Q1 = A)** deviates from the observability design's "always log redacted text"; the design doc
  and the scope decision log are updated during planning to match.
- **Where the debug switch lives**: alongside existing workspace settings; its exact admin surface (API/console) is
  a planning decision. Console UI for it is out of scope for this slice.
- **Channel caller** (the design's fourth caller) does not exist yet; it inherits this behavior when built because it
  will use the same shared path.
- **Outbound propagation** uses a provider-supported request id/metadata mechanism where available; local/self-hosted
  providers may not support one.
- **Metrics labels** use caller source only (low cardinality); no per-tenant or per-agent labels.
- **Dependencies**: slice 010 (inbound correlation resolution + run-outcome counters + metrics surface) and slice 009
  (billed cost source of truth) are in place and unchanged.
