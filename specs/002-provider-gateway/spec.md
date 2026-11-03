# Feature Specification: Provider Gateway (M1)

**Feature Branch**: `feat/backend-m0-foundation` (continuation; no dedicated branch created — no git hook configured)

**Created**: 2026-11-03

**Status**: Draft

**Input**: User description: "M1 Provider Gateway (defensible core #1) — the provider-abstraction layer the runtime calls to execute an LLM request against a chosen provider and return a normalized result, with cost computed from our own reviewed profiles and every capability downgrade recorded as an explicit substitution."

---

## Overview *(context)*

Agent Catalyst's defensibility lives in two places; this feature is the first — **provider honesty and portability**. Today a builder can pick a model for an agent in the console, but there is no backend that can actually *run* that model call, normalize what comes back, or tell the truth about what it cost. This feature delivers the single layer that does so, behind one contract, so that a builder's agent behaves and bills the same way regardless of which provider sits behind it, and so that an operator never sees a cost number the platform didn't independently compute.

This is **M1 production expansion**, authorized by the 2026-11-03 advisory-gate decision in `docs/scope.md` (viability gate held OPEN; build proceeds in parallel, publish-gate advisory).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run a configured model call and get one normalized result shape (Priority: P1)

A builder has configured an agent to use a specific model from a specific provider. When that agent runs, the platform must execute the call against the right provider and return the result in **one uniform shape** — streamed content, then a final usage summary — no matter which provider served it. The same uniform shape is what every higher layer (playground, evaluation, published invoke, channel) will consume, so that evaluation evidence applies to what customers actually run.

**Why this priority**: This is the irreducible core. Without a single normalized execution path there is no runtime, no eval parity, and no provider-agnosticism — every other story depends on it.

**Independent Test**: Issue one normalized request naming a supported model and confirm the layer streams content events followed by a single usage summary, using the identical event vocabulary across all supported providers — verified with mocked provider transport (no live call).

**Acceptance Scenarios**:

1. **Given** a normalized request for a supported provider/model, **When** the gateway executes it, **Then** it yields a stream of content events followed by exactly one usage summary event, in the shared event vocabulary.
2. **Given** the same logical request expressed for three different providers, **When** each is executed, **Then** each returns the same event types and the same usage fields, so no caller needs provider-specific handling.
3. **Given** a request that streams partial content, **When** the consumer reads the stream, **Then** content arrives incrementally (first content before completion), not only as a single terminal blob.

---

### User Story 2 - Honest, independently-computed cost on every call (Priority: P1)

An operator (and the builder) must be able to trust the cost attached to every run. The platform MUST compute cost from its **own reviewed, versioned pricing data**, never from a number the provider or transport library reports. For every call the computed cost MUST reconcile to an independent hand-calculation.

**Why this priority**: Cost honesty is a constitutional promise (P7) and the whole point of "defensible provider abstraction." A wrong or provider-trusted cost poisons spend enforcement, drift economics, and operator trust — so this ships with the core, not after.

**Independent Test**: For a fixed usage breakdown and a known pricing profile, confirm the computed cost equals a hand-calculation to the platform's monetary precision, and confirm that any cost value the provider/transport returned is ignored.

**Acceptance Scenarios**:

1. **Given** a usage record (input, output, cached-read, cache-write, reasoning tokens) and the model's pricing profile, **When** cost is computed, **Then** it equals the independent hand-calculation at the platform's monetary scale.
2. **Given** a provider response that includes its own cost field, **When** the gateway processes it, **Then** the platform's reported cost is derived solely from our profile, not the provider's field.
3. **Given** token accounting where cached and reasoning tokens are reported inclusively, **When** cost is computed, **Then** each token slice is billed exactly once at its own rate (no double-billing of reasoning or cached input).
4. **Given** a model whose pricing profile is marked provisional/unreconciled, **When** cost is reported, **Then** it is labeled an estimate rather than presented as an exact charge.

---

### User Story 3 - Every unsupported capability surfaces as an explicit substitution (Priority: P2)

When a builder's request asks for something a chosen provider cannot do (e.g. structured output, forced tool choice, reasoning control, prompt caching), the platform MUST NOT silently drop or secretly rewrite it. Each downgrade, rewrite, or fallback MUST be recorded as an explicit **substitution** attached to the result, so the builder can see exactly how their intent was altered.

**Why this priority**: Silent capability drift breaks the "no surprises" promise (P7) and would make two providers look falsely interchangeable. It's essential for trust but layered on top of the working execution + cost core.

**Independent Test**: Issue a request that uses a capability a given provider's profile says it lacks, and confirm the result carries a substitution record naming the capability and what was done instead — with no silent omission.

**Acceptance Scenarios**:

1. **Given** a request using a capability absent from the target provider's capability profile, **When** the gateway prepares the call, **Then** a substitution record is produced naming the capability and the fallback taken.
2. **Given** a request whose capabilities are all supported, **When** executed, **Then** no substitution records are produced.
3. **Given** a capability limitation discovered only at call time (the provider rejects a parameter mid-call), **When** it occurs, **Then** it is surfaced on the result (final usage/outcome) rather than hidden.

---

### User Story 4 - Add a new provider without changing callers (Priority: P2)

The platform ships support for three providers (two ported from the proven experiment, one net-new) behind a single adapter contract, proving that a provider can be added without any change to the code that issues requests. The net-new provider is the one the product leans on most (frontend seeds are heavy on it, and the evaluation judge uses it).

**Why this priority**: Provider-agnosticism is only real if adding a provider is a contained change. Demonstrating it with a third, net-new adapter de-risks every future provider and the eval judge's provider — but it's built on the US1 contract.

**Independent Test**: Run the identical adapter-contract conformance suite against all three provider adapters and confirm each passes without caller-side changes.

**Acceptance Scenarios**:

1. **Given** the three supported provider adapters, **When** the shared adapter-contract suite runs against each, **Then** all three conform to the same contract (same event vocabulary, same usage normalization obligations, same substitution-reporting obligation).
2. **Given** the net-new provider, **When** its capability and pricing profiles are loaded, **Then** cost and capability behavior are driven by those profiles exactly as for the ported providers.

---

### Edge Cases

- **Unknown model/provider**: a request for a model with no capability profile MUST fail closed with a clear, caller-visible error — never a silent default to another model or guessed pricing.
- **Provider timeout / transport failure / HTTP error**: MUST be normalized into the shared error outcome (an error event on the stream), not raised as a provider-specific exception leaking SDK types to callers.
- **Interrupted stream**: if the provider stream ends without a usage summary, the gateway MUST make the degradation explicit (error/outcome), not report a successful zero-cost run.
- **Inconsistent provider usage numbers** (e.g. reasoning tokens exceeding output, missing cached counts): usage normalization MUST clamp/reconcile deterministically so cost never goes negative and is reproducible.
- **Unavailable/sunset model**: a model whose profile marks it retired MUST be treated as a caller-visible condition, not executed as if active.
- **Secrets**: a provider credential MUST NOT appear in any event, usage record, substitution, error, or log emitted by this layer.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST accept a single provider-neutral request representation and execute it against the provider/model it names, returning results in one shared, normalized streaming vocabulary (content events then a single usage summary).
- **FR-002**: The system MUST expose one adapter contract with (at least) three conforming implementations — two ported from the validated experiment and one net-new for the product's primary provider — such that request-issuing callers contain no provider-specific branching.
- **FR-003**: The system MUST compute the monetary cost of every call exclusively from its own reviewed, versioned pricing profiles, and MUST ignore any cost figure supplied by the provider or transport.
- **FR-004**: Computed cost MUST reconcile to an independent hand-calculation at the platform's monetary precision, billing each token slice (input, cached-read, cache-write, output, reasoning) exactly once at its own rate.
- **FR-005**: The system MUST normalize provider usage into a single usage record (input, output, cached, cache-write, reasoning token counts) regardless of provider wire format, with deterministic reconciliation of inconsistent inputs so cost is never negative.
- **FR-006**: The system MUST record every capability downgrade, rewrite, or fallback as an explicit substitution attached to the result; it MUST NOT silently drop or alter a requested capability.
- **FR-007**: When all requested capabilities are supported, the system MUST produce no substitution records (no false positives).
- **FR-008**: The system MUST resolve a model to its capability + pricing profile before execution and MUST fail closed with a caller-visible error when no profile exists.
- **FR-009**: The system MUST normalize provider timeouts, transport failures, and HTTP/API errors into the shared error outcome without leaking provider SDK types or exceptions to callers.
- **FR-010**: Cost whose underlying pricing profile is provisional/unreconciled MUST be labeled an estimate rather than presented as an exact charge.
- **FR-011**: The provider-gateway code MUST remain a pure library with no dependency on any inbound web framework (it must be importable and runnable by the runtime, an eval worker, and later a channel caller without starting a web server). Outbound provider transport is permitted.
- **FR-012**: No provider credential or other secret may appear in any emitted event, usage record, substitution, error payload, or log line.
- **FR-013**: The system MUST ship reviewed capability + pricing profiles for the net-new provider's relevant models, including the model used by the evaluation judge.
- **FR-014**: Provider behavior MUST be verified against the features the product exposes — adapter-contract conformance, streaming, timeout/failure, usage normalization, substitution, and independently-reconciled cost — for each supported provider, using mocked transport (no live/paid provider call is part of this feature's tests).

### Key Entities *(data involved)*

- **Normalized Request**: the provider-neutral description of one model call — provider, model, messages, system, parameters, tools, tool-choice, response format, reasoning controls, cache hints, stream flag. The single input every adapter consumes.
- **Normalized Stream Event**: one item in the uniform output vocabulary — content delta, reasoning delta, tool-call start/delta/end, usage, stop, error.
- **Normalized Usage**: the single usage record — input, output, cached-read, cache-write, and reasoning token counts (with documented inclusive/subset semantics), plus the platform-computed cost. Cost is filled by the platform, never the provider.
- **Substitution**: an explicit record that a requested capability was downgraded, rewritten, or fell back — the named capability and what happened instead.
- **Capability + Pricing Profile**: the reviewed, versioned source of truth for what a model supports (structured output, forced tool choice, reasoning control, caching, limits, availability) and what it costs (per-token rates for each slice, batch multiplier). Drives both capability (substitution) and cost decisions.
- **Provider Adapter**: one implementation of the shared contract per provider — translates the normalized request to the provider wire format, normalizes raw usage, and reports the substitutions it must make. Knows a provider exists; does not compute cost.
- **Gateway**: the orchestrator — resolves the profile, selects the adapter, computes pre-call substitutions, runs the stream, attaches platform-computed cost to the usage event, and collects substitutions.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All three supported provider adapters pass the identical adapter-contract conformance suite (same event vocabulary, usage normalization, and substitution obligations) with zero caller-side provider branching.
- **SC-002**: For every cost test case, platform-computed cost equals the independent hand-calculation exactly at the platform's monetary scale (not an approximate band); in 0 cases is a provider-reported cost used.
- **SC-003**: 100% of requested capabilities that a provider lacks are reported as explicit substitutions; 0 silent drops, and 0 false substitutions when every capability is supported.
- **SC-004**: An automated check confirms the provider-gateway package imports no inbound web framework (the single-library invariant holds).
- **SC-005**: 100% of simulated provider timeouts, transport failures, and API errors are returned as the shared normalized error outcome, with 0 provider SDK exceptions escaping to callers.
- **SC-006**: A secret-leakage check confirms 0 occurrences of provider credentials in emitted events, usage, substitutions, errors, or logs across the test suite.
- **SC-007**: The net-new provider ships with reviewed capability + pricing profiles covering the frontend-seeded models and the evaluation judge model.

## Assumptions

- **Scope excludes** (each its own later feature): the `runtime.invoke` spine (M2), the eval/drift engine (M3), the FastAPI app and SSE transport (Phase 4), real Vault key custody (P1 — the skeleton takes provider keys from configuration/env), and the **transport-library choice itself** (adopt a transport library vs. keep the proven hand-rolled transport) — that is a `/speckit-plan` research decision, recorded there, not in this spec.
- **No live provider calls in this feature.** All provider behavior is tested with mocked transport. The first real, paid call happens in the M2/skeleton feature under the constitution's paid-call protocol (approved model, synthetic input, hard spend ceiling, preflight, retained evidence).
- **Cost precision** reuses the already-seeded, test-pinned cost contract (fixed-scale monetary rounding); "reconciles to hand-calc" means exact at that scale, distinct from the viability experiment's ±5% aggregate H2 band.
- **Profiles are the source of truth** for both capability and pricing; this feature reuses the already-seeded profile loader and cost/validation primitives in `services/runtime/providers/` and ports the proven adapters + tests from `mvp/`.
- **Judge-model naming** (docs reference an older Claude judge version; the experiment approved a newer one) stays **deferred to M3** per the open reconciliation item; the net-new profiles will seed the approved judge model and the naming is reconciled when the eval engine lands. Recorded as a non-blocking assumption with M3 as the validation point.
- **Primary users** of this layer are the platform runtime and eval worker (internal callers), with the builder and operator as the ultimate beneficiaries (honest behavior + honest cost); the layer itself exposes no end-user UI.
- **Data classification**: requests may carry customer prompt content and tenant context; this layer minimizes data to what the provider call needs, keeps credentials out of all emitted artifacts, and does not persist request/response content itself (persistence is the runtime/observability layer's concern in M2+).
