"""Verdict math + drift triggers (M3, feature 004) — verbatim port of `mvp/evals/drift.py`.

Mirrors docs/backend/05-eval-drift.md §3 (05:139-158) and §5 (05:190-200). The verdict() ordering here is
load-bearing and copied exactly: statistical honesty first (low_confidence), then the correctness floor
(hard assertions), then the quality axis, then the efficiency axis (FR-008).

M3-slice note: `DRIFT_TRIGGERS` is ported as the documented constant for later wiring; this slice does NOT
wire it to a persisted `publishable` flag or the publish gate (deferred to M4/M5). The judge's pairwise axis
is surfaced advisory-only by the runner and is deliberately NOT a parameter of verdict() here (clarify
2026-11-05).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# 05:190-200 — a change to ANY of these invalidates a version's publishable flag and demands a fresh eval.
# NOTE: kb_index_version is deliberately EXCLUDED (KB content is live & independent — 04 §9 / parked C3).
DRIFT_TRIGGERS = {
    "model", "provider", "api_version",     # gateway config (02)
    "params",                                # temperature/max_tokens…
    "prompt_hash",                           # system prompt text (01)
    "guardrail_config",                      # guardrails change behavior (06)
    "tool_bindings",                         # tool set / schema snapshot (01)
    "skill_bindings", "skill_content_hash",  # bound skills: instruction fragment + resources injected into the prompt
    "embedding_model", "kb_binding",         # RAG affects quality (04)
    "retrieval_config",                      # top_k/threshold/injection/reranker/hybrid (04)
    "capability_substitution",               # a provider downgrade changed behavior (02)
}

# rubric scores are on a 1–5 scale (05:72); score_quality rescales to the FE 'quality' 0–100 range.
RUBRIC_MIN = 1.0
RUBRIC_MAX = 5.0


@dataclass(kw_only=True)
class EvalPolicy:
    """Per-workspace eval policy (05 §1). Maps 1:1 to the FE EvalPolicy type. kw_only so the field order
    from the plan (min_cases default first, then the required thresholds) stays valid in a dataclass."""
    min_cases: int = 10
    quality_threshold: float
    efficiency_budget_pct: float
    allow_low_confidence_override: bool = False


@dataclass(frozen=True)
class EfficiencyDelta:
    """cost% delta vs baseline (05:144-145). Positive = candidate more expensive than baseline."""
    cost_pct: float

    def within_budget(self, pct: float) -> bool:
        return self.cost_pct <= pct


def _cost_of(x) -> float:
    if hasattr(x, "cost_usd"):
        return float(x.cost_usd)
    if isinstance(x, dict):
        return float(x["cost_usd"])
    return float(x)


def score_quality(rubric_scores: dict[str, float], weights: dict[str, float]) -> float:
    """Weighted blend of per-dimension rubric scores, normalized from the 1–5 scale to 0–100 (FE 'quality').

    A weighted mean of RUBRIC_MIN maps to 0, RUBRIC_MAX maps to 100.
    """
    if not rubric_scores:
        return 0.0
    total_w = sum(weights.get(dim, 0.0) for dim in rubric_scores)
    if total_w == 0:
        # no weights supplied → treat all present dimensions equally
        weighted_mean = sum(rubric_scores.values()) / len(rubric_scores)
    else:
        weighted_mean = sum(score * weights.get(dim, 0.0) for dim, score in rubric_scores.items()) / total_w
    return (weighted_mean - RUBRIC_MIN) / (RUBRIC_MAX - RUBRIC_MIN) * 100.0


def efficiency_delta(candidate, baseline) -> EfficiencyDelta:
    """Build the efficiency delta from candidate/baseline aggregates. Each arg may be a float cost, a dict
    with a 'cost_usd' key, or any object with a .cost_usd attribute."""
    c = _cost_of(candidate)
    b = _cost_of(baseline)
    if b == 0:
        cost_pct = 0.0 if c == 0 else float("inf")
    else:
        cost_pct = (c - b) / b * 100.0
    return EfficiencyDelta(cost_pct=cost_pct)


Verdict = Literal["pass", "fail", "low_confidence"]


def verdict(
    n_cases: int,
    det_all_green: bool,
    quality_b: float,
    quality_c: float,
    eff: EfficiencyDelta,
    policy: EvalPolicy,
) -> Verdict:
    """The drift verdict — ordering copied exactly from 05:147-158.

    The order is the guarantee: a suite that is both below MIN_CASES and failing returns low_confidence,
    because statistical honesty is checked before any pass/fail judgment.
    """
    if n_cases < policy.min_cases:
        return "low_confidence"
    if not det_all_green:
        return "fail"
    q_drop = quality_b - quality_c
    if q_drop > policy.quality_threshold:
        return "fail"
    if not eff.within_budget(policy.efficiency_budget_pct):
        return "fail"
    return "pass"


def verdict_with_reason(
    n_cases: int,
    det_all_green: bool,
    quality_b: float,
    quality_c: float,
    eff: EfficiencyDelta,
    policy: EvalPolicy,
) -> tuple[Verdict, str]:
    """`verdict()` plus a specific, case-grounded reason (FR-009). Same ordering — the reason names the axis
    that decided the verdict and its magnitude, so the explanation is true and specific (not generic)."""
    v = verdict(n_cases, det_all_green, quality_b, quality_c, eff, policy)
    if v == "low_confidence":
        reason = (f"Suite has only {n_cases} case(s), below the {policy.min_cases}-case floor for a "
                  f"confident verdict. Expand the suite to trust the score.")
    elif not det_all_green:
        reason = "A deterministic hard assertion failed (empty, refusal, or malformed output) — correctness floor."
    elif (quality_b - quality_c) > policy.quality_threshold:
        reason = (f"Quality dropped {quality_b - quality_c:.1f} points vs baseline "
                  f"({quality_c:.1f} < {quality_b:.1f}), beyond the {policy.quality_threshold:.1f} threshold.")
    elif not eff.within_budget(policy.efficiency_budget_pct):
        reason = (f"Cost {eff.cost_pct:+.1f}% vs baseline, over the {policy.efficiency_budget_pct:.1f}% budget.")
    else:
        reason = "All axes at or above baseline within policy — quality held and cost stayed within budget."
    return v, reason
