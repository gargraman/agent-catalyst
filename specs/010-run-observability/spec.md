# Feature Specification: Run Observability — Correlation + Run-Status Metrics

**Feature Branch**: `feat/backend-m0-foundation` (slice 010; no per-feature branch)

**Created**: 2026-11-07

**Status**: Draft

**Input**: User description: "M6 slice — Run observability: end-to-end correlation (F13) + run-status metrics (F12). Ingest an inbound correlation id at the API edge and thread it to the run record; expose run-outcome counters for availability/deadline SLIs. Heavy telemetry stack (Langfuse/ClickHouse) is infra-blocked and out of scope."

## Clarifications

### Session 2026-11-07

- Q: Auth posture of the metrics scrape endpoint? → A: **Unauthenticated operational endpoint** (like the
  health check), outside the versioned tenant API namespace. It exposes only aggregate counts; network
  restriction is a deploy concern (noted as deferred).
- Q: Also count admission refusals that never become a run? → A: **Yes** — add a small refusals counter
  labeled by error code (402 `budget.exceeded` from feature 009 and 403 `access.denied` from RBAC/008), so
  cap-bite and denial rates are visible even though refusals create no run.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Correlate a request to its run (Priority: P1)

An operator (or an upstream service/gateway) sends a correlation identifier with an invoke or eval
request. When that run is later inspected — in the run history or logs — it carries the *same*
identifier the caller supplied, so a user-reported failure can be traced to the exact run that produced
it. The caller also gets the resolved identifier back on the response, so they can record it even when
they didn't supply one.

**Why this priority**: The single most valuable debugging capability for a pilot. Today the server
always invents a fresh correlation id and throws away whatever the caller sent, so there is no way to
join "the request a user complained about" to "the run the system recorded." This is the highest-value,
lowest-cost observability fix.

**Independent Test**: Send an invoke (and an eval) request carrying a known correlation identifier;
confirm the recorded run carries that same identifier and the response echoes it. Send one with no
identifier; confirm a generated one is recorded and echoed (today's behavior preserved).

**Acceptance Scenarios**:

1. **Given** an invoke request carrying a valid inbound correlation identifier, **When** the run is
   recorded, **Then** the run's correlation id equals the inbound identifier, and the response echoes it.
2. **Given** an eval-run request carrying a valid inbound correlation identifier, **When** its candidate
   runs are recorded, **Then** they carry that identifier.
3. **Given** a request with no correlation identifier, **When** the run is recorded, **Then** a generated
   identifier is used and echoed (unchanged from today).
4. **Given** a request whose correlation identifier is malformed, **When** the run is recorded, **Then**
   the malformed value is ignored and a generated identifier is used (never trusted blindly).

---

### User Story 2 - See run health at a glance (Priority: P1)

An operator (or a monitoring system) can read a count of runs broken down by outcome and source, so
availability and failure/timeout rates can be computed and alerted on — without querying the database or
reading individual runs. A burst of timeouts or provider errors becomes visible as a rising counter.

**Why this priority**: Without outcome counts there is no way to know the service is actually healthy
(success vs error vs timeout) or to alert when it degrades — table stakes for operating a pilot. Pairs
with US1 as the two infra-light halves of observability.

**Independent Test**: Drive a few runs of different outcomes (success, a failure) and read the metrics
surface; confirm the per-outcome counts increased accordingly and are exposed in a standard scrape
format.

**Acceptance Scenarios**:

1. **Given** a completed successful run, **When** the metrics surface is read, **Then** the success
   count for that run's source has increased by one.
2. **Given** a run that ended in a failure outcome (e.g. a provider error or timeout), **When** the
   metrics surface is read, **Then** the count for that outcome has increased by one.
3. **Given** the metrics surface, **When** it is read, **Then** it reports only aggregate counts — no
   tenant identifiers, no prompt/response content, no per-user data.
4. **Given** a transient failure in the telemetry path, **When** a run executes, **Then** the run still
   completes and is still persisted (telemetry is best-effort, never load-bearing).

---

### Edge Cases

- **Malformed inbound correlation id** → ignored; a generated id is used (US1/AC4). The system never
  propagates an untrusted inbound value without validating its shape.
- **Both `traceparent` and `X-Correlation-Id` present** → a defined precedence picks one deterministically.
- **Correlation id length/charset** → an inbound value is accepted only if it matches an allowed shape;
  otherwise it is treated as absent (bounds what can land in logs/DB).
- **Metrics backend/telemetry error** → swallowed; it must never fail a run or its persistence (US2/AC4).
- **Eval fan-out** → each candidate run counts once; the run-status counter reflects every recorded run.
- **No content leakage** → neither the metrics surface nor any new log line emits prompt/response content
  or secrets (P5/SC-009).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST read an inbound correlation identifier from the request at the API edge and
  use it as the run's correlation id for both the invoke and eval-run paths, so the recorded run can be
  joined to the originating request.
- **FR-002**: The system MUST support the W3C `traceparent` header (using its trace-id component) and a
  plain `X-Correlation-Id` header, with a defined, deterministic precedence when both are present.
- **FR-003**: When no inbound identifier is present, the system MUST fall back to a generated identifier
  (preserving today's behavior); a run always has a correlation id.
- **FR-004**: The system MUST validate the shape of an inbound identifier and ignore (treat as absent) a
  malformed value — an untrusted inbound string is never stored/propagated unchecked.
- **FR-005**: The system MUST echo the resolved correlation id back to the caller on the response so the
  caller can record it.
- **FR-006**: The system MUST expose a count of completed runs labeled by outcome status and source,
  incremented once per recorded run, so availability and failure/timeout rates can be computed.
- **FR-007**: The run-outcome counts MUST be exposed via a conventional metrics scrape surface in a
  standard text format, at an operational endpoint that is NOT tenant-scoped and NOT under the versioned
  tenant API namespace.
- **FR-008**: The metrics surface and any new log lines MUST contain only aggregate/operational data — no
  tenant identifiers tied to content, no prompt/response content, no secrets (P5/SC-009).
- **FR-009**: Telemetry MUST be best-effort: a failure in the metrics or correlation path MUST NOT fail a
  run, its streamed output, or its persistence.
- **FR-010**: The control/telemetry MUST live in the API layer; the pure runtime library MUST remain free
  of HTTP/metrics imports (P6). The runtime already emits correlation id + outcome on its run record; this
  slice ingests the inbound id and observes the emitted outcome at the API boundary.
- **FR-011**: The scrape endpoint MUST be an **unauthenticated operational endpoint** (like the health
  check), outside the versioned tenant API namespace; it exposes only aggregate counts (network
  restriction is a deploy-time concern, out of scope). *(Clarified 2026-11-07.)*
- **FR-012**: The system MUST also count **admission refusals that never become a run**, as a separate
  counter labeled by error code — at minimum `402 budget.exceeded` (feature 009) and `403 access.denied`
  (RBAC/008) — so cap-bite and denial rates are observable. *(Clarified 2026-11-07.)*

### Key Entities *(include if feature involves data)*

- **Correlation identifier**: an opaque, shape-validated string that ties an inbound request to the run
  it produced; already a field on the run record (no new storage).
- **Run-outcome counter**: an in-process, monotonically increasing count of completed runs, labeled by
  outcome status and source; exposed for scraping, not persisted in the database.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of runs produced by a request carrying a valid inbound correlation id are recorded with
  that exact id (no server-side overwrite).
- **SC-002**: 100% of runs still receive a correlation id when none/an invalid one is supplied (no run
  without a correlation id).
- **SC-003**: The resolved correlation id is returned to the caller on every invoke and eval response.
- **SC-004**: After N runs of known outcomes, the exposed per-outcome/source counts reflect exactly those
  N runs (counts are accurate and complete).
- **SC-005**: The metrics surface exposes zero tenant-identifying or content data (verifiable by
  inspection of its output).
- **SC-006**: An induced telemetry failure does not change run success or persistence (0 runs lost to a
  telemetry error).
- **SC-007**: The pure runtime library remains import-clean of HTTP/metrics after the change (P6 check
  stays green).

## Assumptions

- **Correlation shape**: an accepted inbound identifier is a bounded hex/alphanumeric token (e.g. the
  32-hex `traceparent` trace-id, or a reasonably bounded `X-Correlation-Id`); anything outside the allowed
  shape is treated as absent. Exact bounds are an implementation detail pinned in the plan/tests.
- **Precedence**: `traceparent` (trace-id) takes precedence over `X-Correlation-Id` when both are present
  (W3C trace context is the more standard upstream signal); either beats a generated id.
- **Metrics are process-local**: counters are per-process and scraped (the aggregation across replicas is
  the scraper's job) — no shared store, no DB counter, no migration.
- **Scope — infra-light only**: the external telemetry stack (distributed tracing spans / Langfuse,
  analytics warehouse / ClickHouse, per-call model-call log) is **out of scope / deferred** (infra not
  available here). This slice is correlation ingestion + run-status counters only.
- **No migration, reuses existing fields**: the run's `correlation_id` already exists and already flows
  runtime → run record → runs API; this slice only changes *what value* it gets and *observes* outcomes.
- **One new dependency** (a Prometheus client) is expected and in-scope for M6; otherwise no new deps.
- **Frontend**: no contract change expected; the runs API already exposes `correlation_id`. Any FE note
  would be contract-level only and build-unverified (Node unavailable).
