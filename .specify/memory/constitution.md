# Agent Catalyst Constitution

## Core Principles

### 1. Deliver a Verifiable Customer Outcome

Build for the people who assemble, publish, govern, and call agents. Every feature MUST address a documented
user job and measurable outcome, and MUST make setup, permissions, success, failure, and recovery understandable
to its intended users. Include accessible interaction and actionable errors in the design. Optimize the complete
path from configuration through evaluation and handoff, not screen count or feature volume. Use an LLM only when
it provides value that deterministic software cannot provide. Never represent a mock or unverified behavior
as implemented, and never generalize a measured result beyond its evidence, context, or limitations.

### 2. Govern Scope and Deliver in Phases

Every feature MUST belong to an owner-approved phase with explicit scope, non-goals, dependencies, and customer
impact. The backend target covers the current frontend capability surface through an approved phased plan; a UI
surface is not proof that the behavior exists or belongs in the pilot. Do not silently drop or promote a feature.
Changes to scope, release boundary, or deferred capabilities require an explicit decision in
[`../../docs/scope.md`](../../docs/scope.md).

### 3. Bound Agent Authority and Keep Controls Deterministic

An agent may act only through explicitly configured, authorized capabilities. Enforce identity, permissions,
tenant boundaries, schemas, tool arguments, resource limits, and policy decisions in ordinary code; model
instructions are not enforcement. Apply least privilege, bounded time/spend/recursion, and fail-closed behavior
for authorization and other safety controls. Classify actions by risk. High-impact or irreversible actions MUST
require human approval unless an owner-approved risk decision specifies equivalent safeguards, audit, and
recovery. Prompts, imported content, retrieved documents, tool results, and model output are untrusted inputs.

### 4. Earn Trust With Evidence About Agent Behavior

Agent quality MUST be tested against representative, versioned scenarios before promotion. Use deterministic
checks for objectively verifiable requirements and calibrated evaluation for subjective behavior. Any AI judge
used as a gate MUST have a versioned identity, prompt, rubric, schema, provider settings, and calibration record.
Keep development/tuning, calibration, and held-out measurement data separate; report sample size, uncertainty,
and repeatability. An uncalibrated judge MUST NOT support a publish or release gate. Evaluation reports MUST
show what passed, what failed, and material limits; do not silently rebaseline or claim universal accuracy.

### 5. Protect Customer Data and Tenant Boundaries

Treat workspace separation as a security boundary from the first release, even when the deployment has one
organization. Enforce identity and authorization server-side, with storage-level isolation as defense in depth.
For every feature, define data classification, purpose, access, retention/deletion, and any external processing.
Minimize data sent to providers; do not permit customer data to train external models without explicit
organizational approval and applicable disclosure. Keep provider, tool, and source credentials out of prompts,
exports, logs, traces, and client-visible errors. Authoritative tenant and financial records MUST have an
explicit source of truth; analytics copies cannot grant access or establish billable truth.

### 6. Make Agent Versions Reproducible and Reversible

Published versions MUST preserve an immutable, auditable snapshot of the agent configuration and dependency
references that affect behavior, authorization, or cost, including applicable prompts, model/profile,
input/output contracts, retrieval settings, tool/skill schemas, guardrails, memory, and runtime policy. Pin or
version dependencies where supported. Where an input intentionally changes independently (such as refreshed
knowledge), its update, freshness, and evaluation behavior MUST be explicitly designed and disclosed; it MUST
NOT be mistaken for the unchanged, previously evaluated version. Playground, evaluation, and production
invocation MUST exercise the same runtime contract. Relevant changes MUST trigger the applicable gate as
defined by that policy. Preserve enough evidence to explain a run and safely roll back; never change a live
agent silently when its draft configuration changes.

### 7. Be Honest About Providers, Capabilities, and Cost

Keep orchestration, provider transport, and the platform's normalized contract replaceable. Maintain reviewed,
versioned capability and pricing data. Test provider behavior for the features the product exposes; do not claim
seamless portability where behavior differs. Surface every unsupported capability, downgrade, rewrite, or
fallback as an explicit substitution. Label uncertain cost as an estimate, validate pricing and usage against
provider evidence, and use conservative estimates for enforcement until reconciled. Do not present estimates as
exact charges or let model/provider changes silently alter an agent's contract or spend profile.

### 8. Operate the Product; Make Failures Visible

Design for bounded latency, availability, concurrency, spend, and safe failure appropriate to each phase. Define
the relevant operational targets, alerts, ownership, and recovery/rollback path before production promotion.
Record model/profile identity, prompt/version identity, usage, latency, cost, outcome, and correlation metadata
subject to data minimization and redaction. Give builders and operators useful diagnostics without exposing
secrets or private tenant data. Degradation MUST be explicit; do not report success when required work or safety
checks did not complete.

### 9. Specify, Test First, and Preserve Evidence

Every bounded feature MUST have a reviewed user outcome, acceptance scenarios, explicit non-goals, and relevant
data, security, privacy, compatibility, failure, cost, and operational requirements before implementation. If
material behavior is unclear, pause at that boundary and ask the project owner; do not turn an assumption into
an implemented requirement.

For each behavior-changing implementation task, use a strict **Red→Green→Refactor** loop:

1. Map acceptance criteria to observable verification and name the focused command. Settle intended behavior in
   reviewed feature artifacts before writing code.
2. **Red:** write and run the smallest useful test before production implementation; retain evidence of failure
   for the expected behavior. A syntax, import, or environment failure is not valid Red evidence. Show the test
   files and failure evidence to the project owner and get confirmation before implementation code.
3. **Green:** make the smallest change that passes the new test; run the focused test, relevant regressions, and
   applicable plan checks. Do not weaken, skip, or mark tests expected-to-fail to force a pass.
4. **Refactor:** preserve the approved contract, rerun affected checks, and keep each dependency-ordered task
   green.

Production types, interfaces, validators, migrations, and behavior-changing configuration follow this sequence.
Named package/test-harness scaffolding may precede Red only when it does not implement the tested behavior. If
a test reveals ambiguity, stop and clarify the feature artifacts before changing either the test or code. Any
exception to test-first sequencing MUST be justified in the feature plan and approved before implementation.
Docs-only changes use suitable link, consistency, and rendering checks rather than fabricated unit tests.
Compilation or a manual demo alone is not completion evidence. Plans MUST name checks; completion reports MUST
identify commands run, evidence retained, and unverified requirements. Failed or inconclusive gates MUST be
reported as such, not rounded up to a pass.

## Purpose, Users, and Current Product Boundary

Agent Catalyst is an internal platform for application developers and technical product managers to build,
configure, evaluate, publish, and operate LLM agents without each team rebuilding the provider and runtime
plumbing. Publishers approve versions; platform administrators and compliance users govern and inspect the
agent estate; application and channel callers consume published agents under their own identity and access
rules. The customer outcome is a safe, repeatable handoff from a configured agent to an evaluated endpoint,
with visibility when behavior, access, provider capability, latency, or cost changes.

The current release target is an **internal pilot for one corporate deployment**. This constitution does not
authorize a public SaaS launch, marketplace, external reliability claims, or deferred capabilities. The
eventual backend target is the capability surface represented by the current frontend, delivered only through
approved phases. The phase assignments, including current P1/P0 pilot scope, P2 follow-on scope, and deferred
voice, are maintained in [`../../docs/scope.md`](../../docs/scope.md) and [`../../docs/spec.md`](../../docs/spec.md).
The frontend is a capability and UX reference; mock behavior does not count as a functioning backend feature.

For this project, the key architecture invariant is that playground, evaluation, published invocation, and
channel calls use the same runtime contract, so evaluation evidence applies to what customers actually run.
Technology choices and detailed API/data/runtime boundaries belong in [`../../docs/spec.md`](../../docs/spec.md)
and [`../../docs/backend/`](../../docs/backend/), not in this constitution. The current pre-production
viability experiment and its GO/NO-GO criteria are defined in [`../../docs/mvp-spec.md`](../../docs/mvp-spec.md);
its live status and decisions are recorded there and in [`../../docs/scope.md`](../../docs/scope.md).

## Development Workflow and Verification

### Required Feature Artifacts

Before implementation, each feature MUST have:

1. A reviewed `spec.md` with user stories, measurable acceptance scenarios, explicit non-goals, data
   classification, security boundaries, compatibility, and failure behavior. It MUST name the primary user
   and job, and specify user-visible setup, permission, success, failure, and recovery states.
2. Clarifications resolved or explicitly recorded as non-blocking assumptions with an owner and validation
   point.
3. A reviewed `plan.md` covering architecture, APIs/contracts, data ownership and migrations, dependencies,
   privacy/security, observability, cost, and rollout/rollback where relevant.
4. A requirements checklist, dependency-ordered `tasks.md`, and consistency analysis showing that every
   requirement maps to implementation and verification without conflicts. Each code task MUST identify its
   Red test, focused command, relevant regression checks, and any applicable quality gates.
5. A clear decision on external model calls, sensitive data, schema migrations, and deployment actions,
   with explicit budgets and approval gates where needed.

Add `research.md`, `data-model.md`, `quickstart.md`, and API contracts when required by the feature plan. Use
the official Spec Kit CLI and its configured agent integration to create/review the artifacts. Use the
specify, clarify, plan, checklist, tasks, analyze, implement, and converge workflows when available; verify
the installed integration before relying on a command. Preserve this constitution during Spec Kit
initialization or refresh.

### Minimum Verification Checks

| Change area | Required evidence before completion |
|---|---|
| User-facing workflow | Acceptance checks cover the intended job, permission/error states, and accessible interaction where applicable |
| API or frontend integration | Typed request/response and error contracts; streaming/SSE contract where applicable; frontend type/build checks |
| Database or tenant behavior | Migration checks; workspace isolation and authorization tests, including negative cross-tenant cases |
| Agent tools or actions | Permission and argument validation; abuse/injection cases; approval behavior for high-impact actions; bounded execution |
| Provider/runtime behavior | Adapter contract checks; streaming, timeout/failure, usage normalization, substitution, and independently reconciled cost evidence |
| Model or prompt behavior | Deterministic checks plus versioned evaluation against a frozen baseline; judge calibration and measurement separation where the judge affects a gate |
| Security or secrets | Secret scan; authorization and abuse-case checks; evidence secrets and sensitive inputs do not leak to logs, traces, or exports |
| Release or deployment | Acceptance criteria green; applicable pilot checks; operating/rollback evidence; explicit promotion approval |

Feature plans may add checks. They may omit a listed check only when they prove the feature cannot affect
that area and record the rationale. Paid provider calls MUST NOT be ordinary tests. Each live call requires
an approved provider/model, public or synthetic input unless sensitive-data use is explicitly approved, a
hard spend ceiling, worst-case preflight, and retained usage/cost evidence. Stop before a call that could
exceed its approved cap.

## Governance

- This ratified constitution is the durable project-wide baseline: it remains in force until amended through
  this section. “Permanent” means standing and binding, not impossible to change.
- The source hierarchy is: this constitution for principles; [`../../docs/scope.md`](../../docs/scope.md) for
  owner-approved product decisions, phase assignments, and reversals; [`../../docs/spec.md`](../../docs/spec.md)
  for product requirements and technical baseline; [`../../docs/backend/`](../../docs/backend/) for detailed
  design; and feature-level Spec Kit artifacts for the approved, actionable contract of a bounded change.
  Feature artifacts MUST align with these sources. If they conflict, pause and reconcile the conflict at the
  appropriate source before implementation; a feature spec cannot silently override a project decision or
  constitutional principle.
- The current standalone H1/H2 viability experiment MUST produce a reproducible GO/NO-GO before production
  backend expansion. Its thresholds and current status live in [`../../docs/mvp-spec.md`](../../docs/mvp-spec.md)
  and [`../../docs/scope.md`](../../docs/scope.md), not as hard-coded status in this constitution. Respect the
  latest approved gate decision; do not substitute weaker evidence without an explicit owner decision.
- An exception to a constitutional principle requires project-owner approval, rationale, impact analysis,
  compensating safeguards where relevant, and a decision-log entry. An amendment requires a semantic-version
  increment, the reason and affected principles, review of impacted feature artifacts, and an entry in
  [`../../docs/scope.md`](../../docs/scope.md).
- The project owner ratified version 1.0.0 on 2026-10-31. Follow this constitution alongside approved product
  decisions; later amendments use the process above.

Version: 1.0.0 | Ratified: 2026-10-31 | Last Amended: 2026-10-31
