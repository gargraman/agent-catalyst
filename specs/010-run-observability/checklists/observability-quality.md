# Requirements-Quality Checklist: Run Observability (010)

**Purpose**: Pre-implementation "unit tests for the spec" — correlation correctness, metrics coverage, and
no-leak, before `/speckit-implement`.
**Created**: 2026-11-07
**Feature**: [spec.md](../spec.md)
**Audience**: implementer + reviewer

> Reviewer-owned. `[x]` = reviewer judged the criterion satisfied by the *requirements*. `/speckit-implement`
> reads but does not modify these. Left unchecked intentionally.

## Correlation Correctness & Coverage

- [ ] CHK001 - Is the inbound-id precedence (traceparent trace-id > X-Correlation-Id > generated) stated unambiguously? [Clarity, Spec §FR-002, research D1]
- [ ] CHK002 - Is the accepted shape of each inbound id specified (traceparent format incl. all-zero rejection; X-Correlation-Id charset/length)? [Completeness, Spec §FR-004, §Assumptions]
- [ ] CHK003 - Is the always-has-an-id guarantee specified (generated fallback when absent/malformed)? [Completeness, Spec §FR-003, §SC-002]
- [ ] CHK004 - Is "ignore a malformed inbound id" stated as a MUST (untrusted value never stored unchecked)? [Clarity, Spec §FR-004, §Edge Cases]
- [ ] CHK005 - Is coverage of BOTH paid paths (invoke + eval-run) required for correlation? [Coverage, Spec §FR-001, §US1/AC2]
- [ ] CHK006 - Is the echo-back-to-caller behavior (response header) specified and measurable? [Measurability, Spec §FR-005, §SC-003]

## Metrics Completeness & Accuracy

- [ ] CHK007 - Is the run-status counter's identity (labels: outcome status + source) and once-per-run semantics specified? [Clarity, Spec §FR-006, data-model D2]
- [ ] CHK008 - Is the set of status values enumerated / traceable to run outcomes (success/timeout/provider_error/…)? [Completeness, Spec §FR-006, data-model]
- [ ] CHK009 - Is the refusal counter's scope specified (which codes: budget.exceeded + access.denied) and its distinction from runs (refusals create no run)? [Clarity, Spec §FR-012, research D3]
- [ ] CHK010 - Is the metrics exposure format + endpoint placement (standard text, non-/api/v1, unauthenticated) specified? [Completeness, Spec §FR-007, §FR-011]
- [ ] CHK011 - Is counter accuracy measurable (N known runs → exactly N counted)? [Measurability, Spec §SC-004]

## No-Leak, Best-Effort & Composition

- [ ] CHK012 - Is "no tenant identifier tied to content, no prompt/response, no secrets" required of the metrics surface AND any new log line? [Completeness, Spec §FR-008, §SC-005]
- [ ] CHK013 - Is telemetry best-effort stated as a MUST (a metrics/correlation failure never fails a run, its stream, or persistence)? [Clarity, Spec §FR-009, §SC-006, §US2/AC4]
- [ ] CHK014 - Is the runtime-purity constraint (control in API layer; runtime stays HTTP/metrics-free) stated with a verification? [Measurability, Spec §FR-010, §SC-007]
- [ ] CHK015 - Is composition with the spend cap (009), RBAC (008), and tenancy (RLS) addressed (the refusal counter observes their 402/403 without altering them)? [Consistency, Spec §FR-012, data-model §Composition]
- [ ] CHK016 - Is the unauthenticated `/metrics` endpoint's non-interference with the 008 deny-by-default coverage test reasoned (non-/api/v1)? [Consistency, research D4]

## Scope & Dependencies

- [ ] CHK017 - Is the deferred heavy stack (Langfuse spans, ClickHouse, per-call model-call log, provider-hop correlation) explicitly out of scope? [Clarity, Spec §Assumptions]
- [ ] CHK018 - Is the one new dependency (Prometheus client) and the no-migration/no-new-column constraint stated so scope can't silently expand? [Clarity, Spec §Assumptions]
- [ ] CHK019 - Are the metrics specified as process-local (scraper aggregates across replicas), not a shared/DB counter? [Clarity, Spec §Assumptions]

## Notes

- Scope: infra-light observability (correlation + counters) only; heavy telemetry stack deferred (infra
  unavailable). All items are requirements-quality questions — none assert implementation behavior.
