# Feature Specification: Core Loop Live — build → test → evaluate → publish → call against the real backend

**Feature Branch**: `feat/backend-m0-foundation` (spec dir `specs/026-core-loop-live`)
**Created**: 2026-11-16
**Status**: Draft
**Input**: User description: "next milestone. verify old milestones are good"

## Context

### Verification of earlier milestones (2026-11-16, run on this branch at `b9221f0`)

| Check | Result |
|---|---|
| Infra-free suite (`pytest -m "not integration"`) | **924 passed, 1 failed, 1 skipped**. The one failure is the known, owner-gated `test_heldout_gate_sc004` (no held-out injection corpus yet), so it is not a regression |
| Integration suite (`pytest -m integration`, compose Postgres) | **365 passed, 0 failed, 4 skipped**. All 4 skips are opt-in gates |
| PgBouncer pooler path (`AC_PGBOUNCER_URL` set) | 6/6 passed |
| LocalStack secrets + S3 (`make test-integration`) | 4/4 passed **only when** `PYTEST="./.venv/bin/python -m pytest"` is passed. The Makefile default (`python -m pytest`) uses the system interpreter, fails with `ModuleNotFoundError: prometheus_client`, and errors 4/4. This is a harness defect, not a code defect |
| Frontend `npm run lint` + `npm run build` (Node 24) | Both pass |

**What is still open from earlier milestones (not regressions, but unfinished):**
- **Feature 025 is ~60% done.** US1–US3 are shipped: retry, shared breaker, bulkhead. **US4 binding integrity
  (T032–T040) and the polish/DoD phase (T041–T048) are open.** The v1 release plan treats both as Must (rows 7–8).
  **This feature depends on 025 US4 landing first**, because the version-create and publish flows built here must
  render the integrity errors 025 introduces (`version.frozen` 409, `not_found` 404 on a dangling binding,
  `input.invalid` 400 on a foreign active version).
- The 2026-11-15 audit gaps no spec owns are unchanged. These are spend reservation + reaper + eval budget (M6),
  multi-replica idempotency (M4), and `agent_versions` status/created_by/note/unique label (M0). **Audit gap #5 is
  M3 eval persistence. This feature picks up the slice of it that the v1 eval UI cannot function without** (see US4).

### Why this is the next milestone

`docs/release-plan.md` names three v1 blockers: M12 frontend wiring (4/257 seams live), production deployment, and
025. 025 is in flight. Production deployment needs owner decisions on account, region, and runtime platform, while
M12 is fully unblocked and is the repo's stated current focus. Within M12, the **core loop** carries the most weight:
release-plan rows 1–4 (configure/version, playground, publish + tokens + cURL, eval + drift). It is the
product's one-sentence pitch, and today a live workspace can only *list and create* agents. Every other edit is
"local only".

### Backend facts that shape the scope (verified in code)

- No list endpoint exists for **agent versions** or **eval runs**. Only `POST` exists, so the console cannot show
  version or eval history from the server.
- `POST /agents/:id/eval-runs` takes **the test cases and the baseline numbers from the request body**. The verdict
  compares the candidate against whatever baseline the caller sends, and only per-case run rows are persisted, not
  the aggregate verdict. Eval suites exist only in browser memory, so they are lost on reload in live mode.
- The agent read model is thin (finding F-A): description, model, and draft config render as placeholders.
- There is no duplicate or hard-delete endpoint for agents. Archive goes through the status field.

## Scope (Must vs Nice)

| # | Item | Tier | Why / impact | Size |
|---|------|------|--------------|------|
| 1 | Agent read model carries what the editor shows (description, model, draft config, active version, bound knowledge) | **Must** | Without it the live editor shows placeholders (F-A) | S |
| 2 | Edit agent metadata + draft config and persist it, with stale-edit protection | **Must** | Today edits to a live agent are silently local-only | S |
| 3 | Create a version snapshot, list versions, re-point the active version (rollback) | **Must** | Reproducible + reversible versions is the platform contract (constitution §6) | M |
| 4 | Archive / restore an agent | **Must** | Basic lifecycle; already supported through status | S |
| 5 | Playground streams a live answer, with citations, cost/latency, and every refusal state shown honestly | **Must** | "Test" step of the loop | M |
| 6 | Eval suites + cases stored server-side, per agent | **Must** | Cases held in browser memory make eval unusable in live mode | M |
| 7 | Eval runs persisted with their verdict; per-agent and workspace eval history lists | **Must** | The drift verdict is the defensible core. A verdict that disappears after the response is not evidence | M |
| 8 | Verdict compares against a **server-held baseline**, never a caller-supplied one | **Must** | A client-supplied baseline lets any caller manufacture a "pass" | M |
| 9 | Publish shows the advisory divergence verdict; mint / list / revoke caller tokens; copyable cURL for the published endpoint | **Must** | "Publish → call" step; release-plan row 3 | S |
| 10 | Duplicate agent | Nice | No backend endpoint; client can create-from-copy later | S |
| 11 | Hard delete agent | Nice | Archive covers v1; hard delete is an offboarding concern (release-plan row 20) | S |
| 12 | Async eval jobs (queue + poll) instead of a single synchronous request | Nice | Synchronous is acceptable at starter-suite sizes (≤ 25 cases) | L |
| 13 | Enforcing publish gate (block publish on a failing verdict) | Nice / owner-gated | Gated on judge calibration + the viability gate; stays advisory | M |
| 14 | Starter eval set content | Separate item | Release-plan row 4 content task; this feature provides the place to put it | M |
| 15 | Real OIDC login in the console | Separate M12 slice | Live mode keeps the dev identity bridge for this feature | M |

Items 10–15 are **out of scope** unless the owner pulls them in.

## Clarifications

### Session 2026-11-16

- Q: US4-8 asks where the server-held baseline comes from. The options were (a) automatic, the latest completed
  run of the active version; (b) explicit, a publisher freezes a run (audited); (c) both. → A: **(c) both.**
  Automatic is the default and an audited publisher freeze overrides it. *The owner delegated this decision in the
  `/speckit-implement` instruction "answer US4-8". The owner can overturn it at plan review.*
  - Rationale: (a) alone lets the baseline creep. A slowly degrading active version keeps re-baselining itself,
    and drift is never flagged. This is the exact failure a drift core exists to catch. (b) alone means no verdict
    until someone remembers to freeze, so every early run is "insufficient evidence" and the loop feels broken.
    (c) gives a useful verdict on day one, plus an anchor the owner controls. It also matches the console's
    existing re-freeze seam.
  - Cost accepted: one extra pointer per suite, one audited publisher action, and one more state to explain in the
    UI (show "baseline: automatic · run X" vs "baseline: frozen by Y · run X").

### Session 2026-11-16 (plan review, owner answers)

- Q (O1): run the unsaved draft in the playground, or "try draft = create a version and run it"? → A: **run the
  draft** (transient snapshot through the same runtime path).
- Q (O2): who may freeze or clear a suite's baseline? → A: **admins only** (`eval.govern`). This supersedes
  "publisher" in the session above. Publishers can still publish, and they see the baseline but cannot move it.
- Q (O3): an eval-run request that carries its own baseline, policy or cases? → A: **silently ignore** those fields.
  The verdict is computed only from server-held data, and no error is returned.
- Q (O4): cap on cases per synchronous eval run? → A: **25**.
- Q (O5): real-provider smoke? → A: **run it once** with an approved model and a hard spend cap of **≤ $0.05**.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Configure an agent and freeze a version that persists (Priority: P1)

A builder opens an agent in a live workspace, edits its description, system prompt, model, and model parameters,
saves, reloads the page, and sees their changes. They then cut a version, see it appear in the version list, and
later roll the agent back to an earlier version.

**Why this priority**: Every later step (playground, eval, publish) operates on a saved configuration and a frozen
version. Today these edits are lost on reload.

**Independent Test**: In a live workspace, edit and save an agent, reload, and confirm the values persist. Create
two versions, re-point the active version to the first, and confirm the agent reports the first version as active.

**Acceptance Scenarios**:

1. **Given** a live agent the builder can manage, **When** they change the prompt and save, **Then** a reload shows
   the new prompt and the agent's draft is marked as changed relative to the active version.
2. **Given** two people editing the same agent, **When** the second saves after the first, **Then** the second
   sees a "changed by someone else" conflict with an option to reload. Their edit does not silently overwrite the
   first.
3. **Given** a saved draft, **When** the builder creates a version with a label, **Then** the version appears at
   the top of the version list with its label, creation time, and an identifier that never changes.
4. **Given** versions v1 and v2 with v2 active, **When** a publisher re-points the agent to v1, **Then** subsequent
   playground and published calls run v1's frozen configuration.
5. **Given** a version whose bound knowledge base was removed after the draft was saved, **When** the builder
   creates a version, **Then** they see a clear "a bound resource no longer exists" message naming the binding,
   not a generic error.
6. **Given** a viewer, **When** they open the agent, **Then** the edit, version, and lifecycle controls are hidden
   or disabled per the server-resolved role.
7. **Given** an agent, **When** a publisher archives it, **Then** it disappears from the default list, its
   published endpoint stops answering, and restoring it brings both back.

---

### User Story 2 - Try the agent in the playground and see a real streamed answer (Priority: P1)

A builder types a message in the playground for a live agent and watches the answer stream in. If the agent has
knowledge bound, they see citations. Each turn shows cost and latency. If the call is refused (budget, rate limit,
access, provider down), they see why.

**Why this priority**: "Test" is the most-used screen while building. It is also the first place a customer
judges the product.

**Independent Test**: Invoke a live agent from the playground. A streamed answer appears token by token, the run
appears in run history with the same cost, and each refusal condition renders its own message.

**Acceptance Scenarios**:

1. **Given** a live agent with an active version, **When** the builder sends a message, **Then** the answer
   streams incrementally and the final turn shows cost and latency matching the run recorded in run history.
2. **Given** an agent with a bound knowledge base, **When** the answer uses retrieved content, **Then** citations
   are shown with the source document names.
3. **Given** a workspace over its monthly spend cap, **When** the builder sends a message, **Then** they see a
   "budget exceeded" message with the cap and month-to-date spend, and no answer is attempted.
4. **Given** the caller is rate limited, **When** they send a message, **Then** they see when they may retry.
5. **Given** the provider is unavailable (breaker open), **When** they send a message, **Then** they see a
   "provider temporarily unavailable" message, not a hang or a blank turn.
6. **Given** a stream that fails mid-answer, **When** the failure arrives, **Then** the partial answer is kept, marked
   as incomplete, and the builder can retry.
7. **Given** the builder can choose between the draft configuration and the active version, **When** they pick
   one, **Then** the turn runs exactly that configuration and the turn records which one ran.

---

### User Story 3 - Publish the agent and call it from outside (Priority: P1)

A publisher publishes the agent, sees the advisory divergence verdict, mints a caller token (shown once), copies a
ready-to-run cURL command, and calls the agent from a terminal. Later they revoke the token and the next call fails.

**Why this priority**: "Publish → call" is how a customer gets value outside the console. The backend for it is
fully built and only the console is missing.

**Independent Test**: Publish, mint a token, run the copied cURL command successfully, revoke the token, and see
the same command refused.

**Acceptance Scenarios**:

1. **Given** an agent with an active version, **When** a publisher publishes, **Then** they see the advisory
   divergence verdict (pass / drift / insufficient evidence) and the publish completes regardless (advisory only).
2. **Given** a published agent, **When** a publisher mints a token, **Then** the raw token is shown exactly once
   with a copy action and a warning that it cannot be shown again. The token list afterwards shows only a masked
   form, its expiry, and its last use.
3. **Given** a minted token, **When** the publisher copies the cURL snippet, **Then** the snippet targets the
   published endpoint for this agent and runs successfully when the token is substituted in.
4. **Given** a token, **When** it is revoked, **Then** the next call with it is refused, including a stream already
   in flight (cut within the configured re-check interval).
5. **Given** a builder (not publisher), **When** they open the publish panel, **Then** publish and token controls
   are not available to them.

---

### User Story 4 - Evaluate a version against a trusted baseline and keep the evidence (Priority: P2)

A builder authors an eval suite of cases for an agent, runs it against a candidate version, and sees a verdict
(pass / quality drift / cost drift / hard fail / low confidence) with per-dimension deltas and failing cases. The
run is kept: it appears in the agent's eval history and the workspace eval list, and the verdict is computed
against a baseline the server holds, so no one can supply a flattering baseline.

**Why this priority**: The eval and drift core is the product's defensible half. P2 rather than P1 only because it
needs backend additions; US1–US3 are pure wiring of built endpoints.

**Independent Test**: Create a suite with three cases, run it against v2 with v1 as the baseline, reload, and find
the run and its verdict in history. Attempt to submit a run carrying a client-supplied baseline and confirm the
server ignores or rejects it.

**Acceptance Scenarios**:

1. **Given** an agent, **When** a builder creates a suite and adds cases (input, rubric dimensions, optional
   required fields, optional expected output), **Then** the suite and cases survive a reload and are visible to
   other workspace members who can use the agent.
2. **Given** a suite and a candidate version, **When** the builder runs it, **Then** a verdict with quality,
   quality/cost/latency deltas, per-dimension scores, and failing cases is shown, **and** the run is listed in the
   agent's eval history with its verdict, version, suite, cost, and time.
3. **Given** a request that includes its own baseline numbers, **When** it is submitted, **Then** the verdict is
   still computed against the server-held baseline (the supplied numbers have no effect).
4. **Given** no baseline exists yet for the suite, **When** the first run completes, **Then** the verdict is
   "insufficient evidence / no baseline", never a pass.
5. **Given** a suite whose cases changed since the baseline was taken, **When** a run is compared, **Then** the
   comparison is made only on cases common to both, or the verdict states the baseline is out of date. Silently
   comparing different case sets is never acceptable.
6. **Given** a workspace over its spend cap, **When** a builder starts an eval run, **Then** it is refused with the
   budget message and nothing is persisted as a run.
7. **Given** two workspaces, **When** a member of one lists eval suites or runs, **Then** they never see the other's
   (tenant isolation, existence not leaked).
8. **Given** a suite with no frozen baseline, **When** a run is compared, **Then** the baseline is the most recent
   completed run of the agent's currently *active* version on that suite, and the verdict names that run.
   **Given** an admin has frozen a specific completed run as the suite's baseline, **When** a run is compared,
   **Then** the frozen run is used instead of the automatic one, even after the active version moves. The freeze
   (who, when, which run, previous baseline) is in the audit log. Clearing the freeze returns the suite to the
   automatic rule. Builders and publishers cannot freeze or clear. (Decided 2026-11-16, see Clarifications.)

---

### Edge Cases

- A builder saves a draft while a version is being created from the previous draft. The version captures exactly
  the draft that was saved when it was requested.
- Re-pointing to a version of a *different* agent is refused with an input error (enforced by 025 US4).
- Editing a frozen version's bindings is refused with a "version is frozen" conflict and points the builder to
  creating a new version.
- A stream is interrupted by navigation away. No orphaned "running" turn remains in the console, and the server
  run is still recorded.
- The playground is used on an archived agent. Invocation is refused with a clear archived-state message.
- A token minted with an expiry beyond the platform maximum is capped, and the cap is shown at mint time.
- An eval suite with zero cases cannot be run. A suite below the workspace's minimum-cases policy runs but the
  verdict is "insufficient evidence".
- An eval run that exceeds the request deadline partway leaves no half-recorded verdict. Either the whole run is
  persisted with its verdict, or it is recorded as failed with the reason.
- The live API is unreachable. Every core-loop screen shows the shared error strip and never falls back to mock
  data silently.
- Mock mode (no live flag) behaves exactly as today.

## Requirements *(mandatory)*

### Functional Requirements

**Configure + version (US1)**
- **FR-001**: In a live workspace, agent reads MUST return description, model, draft configuration, active version
  identifier, and bound knowledge, so the editor renders real values and never placeholders.
- **FR-002**: Saving agent metadata or draft configuration MUST persist it server-side and MUST be refused with a
  conflict when the agent changed since the editor loaded it. The console MUST offer reload-and-reapply.
- **FR-003**: Users MUST be able to create a version from the saved draft, list an agent's versions newest first
  (label, creator, time, immutable identifier, whether active), and re-point the active version to any version of
  the same agent.
- **FR-004**: Archive and restore MUST be available to roles holding the lifecycle permission. An archived agent
  MUST NOT answer on any invoke plane.
- **FR-005**: Integrity refusals introduced by feature 025 MUST surface as specific, actionable messages: frozen
  version, missing bound resource, version of another agent.

**Playground (US2)**
- **FR-006**: The playground MUST render the answer incrementally as it streams, render citations when present, and
  show per-turn cost and latency equal to the recorded run.
- **FR-007**: Every refusal MUST render a distinct, human-readable message without a provider call being implied:
  budget exceeded (cap + month-to-date), rate limited (retry time), access denied, not found, provider unavailable,
  configuration error (no provider key), and deadline exceeded.
- **FR-008**: A mid-stream failure MUST keep the partial answer, mark it incomplete, and allow retry.
- **FR-009**: Each turn MUST record and display whether it ran the draft or a specific frozen version.

**Publish + call (US3)**
- **FR-010**: Publish MUST display the advisory verdict and MUST NOT be blocked by it.
- **FR-011**: Token mint MUST show the raw secret exactly once. Lists MUST show only masked tokens with expiry and
  last use. Revoke MUST take effect on the next call and on in-flight streams.
- **FR-012**: The console MUST provide a copyable command-line example that calls this agent's published endpoint
  and works verbatim once a token is substituted. The token MUST NOT be pre-filled into anything persisted or logged.

**Evaluate (US4)**
- **FR-013**: Eval suites and their cases MUST be stored server-side, scoped to an agent and workspace, and
  readable/editable per role and per the agent's access policy.
- **FR-014**: Each eval run MUST be persisted with candidate version, suite, verdict, reason, aggregate and
  per-dimension scores, deltas, failing cases, cost, and time. It MUST be listable per agent and per workspace,
  newest first.
- **FR-015**: The verdict MUST be computed against a server-held baseline. Baseline or policy values supplied by the
  caller MUST NOT influence the verdict.
- **FR-016**: With no baseline, the verdict MUST be "insufficient evidence", never pass.
- **FR-017**: When the suite's case set differs from the baseline's, the comparison MUST be limited to common
  cases or the verdict MUST state the baseline is out of date.
- **FR-023**: Baseline origin. By default the baseline MUST be the most recent completed run of the active version
  on the same suite. An admin MAY freeze one completed run of that suite as the baseline, and the freeze
  overrides the automatic rule until it is cleared or replaced. Freeze and clear MUST be audited (actor, time, run,
  prior baseline) and MUST be refused to roles without the eval-governance permission (admin). Every persisted run MUST record which
  baseline run it was compared to and whether that baseline was automatic or frozen.
- **FR-018**: Eval runs MUST keep the existing guarantees: same runtime path as playground and published invoke,
  spend cap, rate limit, access gate before any provider call, tenant isolation.

**Cross-cutting**
- **FR-019**: All core-loop screens MUST use the existing live-loading convention: a loading/ready/error state, no
  silent mock fallback, the shared Live/Mock/error strip.
- **FR-020**: UI gating MUST use the server-resolved role. Client-side role overrides MUST NOT unlock server actions.
- **FR-021**: Mock mode MUST remain unchanged and fully functional.
- **FR-022**: The emitted API contract and the generated console types MUST be regenerated, and every newly wired
  seam MUST be marked wired in the integration status document with the count updated.

### Key Entities

- **Agent (read model)**: identity, name, description, status (active/archived), model, draft configuration, active
  version reference, bound knowledge, access policy, change token for stale-edit detection.
- **Agent Version**: immutable snapshot of a draft. Label, creator, created time, content hash, belongs to exactly
  one agent.
- **Caller Token**: workspace- and agent-scoped credential. Masked form, expiry, last use, revoked state (already
  exists).
- **Eval Suite** *(new)*: named set of cases belonging to one agent in one workspace.
- **Eval Case** *(new)*: input, rubric dimensions with weights, optional required fields, optional expected output.
- **Eval Run** *(new as a persisted aggregate)*: one execution of a suite against a version. Verdict, reason,
  scores, deltas, failing cases, cost, time, and the baseline it was compared to.
- **Eval Baseline** *(new)*: the server-held reference a suite's runs are compared against. It is either
  automatic (the latest completed active-version run) or frozen (an admin-pinned run, audited); see FR-023.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In a live workspace, a builder can go from a new agent to a successful external call with a minted
  token (configure → version → playground → publish → token → cURL) without any step being "local only", in under
  10 minutes on first use.
- **SC-002**: 100% of the core-loop seams listed in the plan are marked wired, and the integration status count
  rises from 4 by exactly the number of seams wired. No seam is claimed without a passing build and a recorded
  live check.
- **SC-003**: After a page reload, 100% of saved edits, versions, suites, cases, eval runs, and tokens are still
  present.
- **SC-004**: A submitted client-side baseline changes the verdict in 0 of the tested cases.
- **SC-005**: Each of the 7 refusal conditions in FR-007 renders its own distinct message in the playground (7/7).
- **SC-006**: A revoked token is refused on its next call in 100% of tests, and an in-flight stream is cut within
  the configured re-check interval.
- **SC-007**: Tenant isolation holds for the new eval entities: 0 cross-workspace reads in the isolation tests, and
  a denied resource is indistinguishable from an absent one.
- **SC-008**: No regression: infra-free, integration, and LocalStack suites, plus the frontend lint/build, pass at or
  above the 2026-11-16 baseline (924 / 365 / 4, the held-out gate excepted). Mock mode is re-checked unchanged.

## Assumptions

- **Feature 025 US4 lands first.** If it does not, US1's integrity messages (FR-005) are built against the error
  codes in 025's contract and verified once 025 merges.
- Live mode keeps the existing dev identity bridge. Real OIDC login in the console is a separate M12 slice.
- Eval runs stay **synchronous** for v1 starter-suite sizes (≤ 25 cases within the request deadline). Async jobs
  are Nice.
- The publish gate stays **advisory** (owner decision #3; flipping it needs judge calibration).
- The starter eval set *content* is a separate release-plan item. This feature supplies the storage and screens.
- Duplicate and hard delete stay mock/disabled in live mode with a "not available yet" affordance rather than a fake
  success.
- Workspace onboarding for live testing uses the existing members-row setup (no operator provisioning in scope).
- The `make test-integration` default-interpreter defect is fixed opportunistically (one-line Makefile default) and
  is not a user story.
