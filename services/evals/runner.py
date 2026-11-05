"""Eval runner (M3, feature 004) — orchestrates the three layers into one honest verdict.

Flow (05 §7, scoped): tenancy check (P5/FR-023, fail closed) → per-case: generate candidate via the SAME
`runtime.invoke_result` with eval source (P6/FR-024) → deterministic pre-filter (skip the judge on a hard
fail, FR-007) → advisory judge via an instance circuit breaker (F8) → aggregate into the full frontend
`EvalRun` shape (FR-025). The judge's pairwise win-rate is surfaced ADVISORY-only and never gates (clarify
2026-11-05). Pure library (P6): no web/DB imports.
"""
from __future__ import annotations

import statistics
from dataclasses import replace
from typing import Sequence

from runtime.invoke import invoke_result

from evals.breaker import CircuitBreaker
from evals.deterministic import run_deterministic
from evals.drift import efficiency_delta, score_quality, verdict_with_reason
from evals.judge import JUDGE_VERSION, judge_case
from evals.types import (
    BaselineAggregate,
    CaseScore,
    DimensionScore,
    EvalCase,
    EvalRunResult,
    FailingCase,
    TenancyError,
)


def _assert_one_workspace(cases, baseline, candidate_snapshot, workspace_id: str) -> None:
    for c in cases:
        if c.workspace_id != workspace_id:
            raise TenancyError(f"case {c.id!r} workspace {c.workspace_id!r} != run workspace {workspace_id!r}")
    if baseline.workspace_id != workspace_id:
        raise TenancyError(f"baseline workspace {baseline.workspace_id!r} != run workspace {workspace_id!r}")
    if candidate_snapshot.workspace_id != workspace_id:
        raise TenancyError(
            f"candidate workspace {candidate_snapshot.workspace_id!r} != run workspace {workspace_id!r}")


async def run_eval(
    cases: Sequence[EvalCase],
    candidate_snapshot,
    baseline: BaselineAggregate,
    policy,
    ctx,
    *,
    workspace_id: str,
    breaker: CircuitBreaker | None = None,
    rng=None,
) -> EvalRunResult:
    # Tenancy (P5/FR-023) — fail closed BEFORE any provider call.
    _assert_one_workspace(cases, baseline, candidate_snapshot, workspace_id)

    breaker = breaker or CircuitBreaker()
    # Eval runs are eval-sourced (FR-024 forward marker; the M2a runtime has no response cache yet, so there
    # is nothing to bypass — the eval source is the signal a future cache will read to avoid masking drift).
    eval_ctx = replace(ctx, source="eval")

    case_scores: list[CaseScore] = []
    for case in cases:
        # 1) Candidate output via the SAME runtime (P6). allow_cache=off ≡ eval source (see above).
        cand = await invoke_result(candidate_snapshot, {"message": case.input}, eval_ctx)
        cand_cost = cand.run.cost_usd if cand.run is not None else None
        cand_latency = cand.run.latency_ms if cand.run is not None else None

        # 2) Deterministic pre-filter — a hard fail forces FAIL and NEVER reaches the judge (FR-007/SC-002).
        det = run_deterministic(cand.text, case.det_spec())
        if det.hard_fail:
            case_scores.append(CaseScore(case.id, det, None, 0.0, cand_cost, cand_latency, failed=True))
            continue

        # 3) Advisory judge via the instance breaker (F8). Baseline per-case output drives the pairwise call.
        baseline_out = baseline.per_case.get(case.id, "")
        jv = await breaker.call(judge_case, case, baseline_out, cand.text, eval_ctx, rng=rng)
        quality = score_quality(jv.rubric_scores, case.rubric_weights())
        case_scores.append(
            CaseScore(case.id, det, jv, quality, cand_cost, cand_latency, failed=not det.passed))

    # ---- aggregate -------------------------------------------------------------------------------
    n = len(case_scores)
    det_all_green = not any(cs.det.hard_fail for cs in case_scores)
    judged = [cs for cs in case_scores if cs.judge is not None]

    quality_c = statistics.fmean([cs.quality for cs in judged]) if judged else 0.0
    quality_b = float(baseline.quality)

    cand_total_cost = float(sum(float(cs.candidate_cost_usd or 0.0) for cs in case_scores))
    eff = efficiency_delta(cand_total_cost, baseline.cost_usd)

    cand_latencies = [cs.candidate_latency_ms for cs in case_scores if cs.candidate_latency_ms is not None]
    cand_mean_latency = statistics.fmean(cand_latencies) if cand_latencies else 0.0
    latency_delta = float(cand_mean_latency) - float(baseline.latency_ms)

    # Verdict (FR-008 ordering) + specific reason (FR-009). Pairwise is NOT a parameter here — advisory only.
    v, reason = verdict_with_reason(n, det_all_green, quality_b, quality_c, eff, policy)

    # Per-dimension candidate means (1-5). Baseline dims are None — the baseline is an aggregate, not re-judged.
    dim_values: dict[str, list[float]] = {}
    for cs in judged:
        for dim, score in cs.judge.rubric_scores.items():
            dim_values.setdefault(dim, []).append(score)
    dimensions = tuple(
        DimensionScore(name=d, candidate=statistics.fmean(vals)) for d, vals in sorted(dim_values.items()))

    failing_cases = tuple(
        FailingCase(cs.case_id,
                    "deterministic hard failure (empty / refusal / malformed)" if cs.det.hard_fail
                    else "soft deterministic failure (missing field / out-of-enum / expected mismatch)",
                    hard=cs.det.hard_fail)
        for cs in case_scores if cs.failed)

    # Advisory pairwise rate — fraction of DECIDED (non-tie) judged cases the candidate won head-to-head.
    decided = [cs for cs in judged if cs.judge.pairwise_winner in ("candidate", "baseline")]
    wins = [cs for cs in decided if cs.judge.pairwise_winner == "candidate"]
    pairwise_rate = (len(wins) / len(decided)) if decided else 0.0

    return EvalRunResult(
        verdict=v,
        reason=reason,
        quality=quality_c,
        quality_delta=quality_c - quality_b,
        cost_delta=eff.cost_pct,
        latency_delta=latency_delta,
        cases=n,
        dimensions=dimensions,
        failing_cases=failing_cases,
        pairwise_rate=pairwise_rate,
        workspace_id=workspace_id,
        judge_version=JUDGE_VERSION,
    )
