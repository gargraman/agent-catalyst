"""US2 — verdict() ordering (FR-008) + verdict_with_reason() specificity (FR-009) + per-workspace policy
(FR-012). Ported from mvp/tests/test_verdict.py (verbatim ordering) and extended for the M3 acceptance
scenarios (SC-003/SC-006)."""
from __future__ import annotations

import pytest

from evals.drift import EfficiencyDelta, EvalPolicy, efficiency_delta, verdict, verdict_with_reason


def _policy(**over) -> EvalPolicy:
    base = dict(min_cases=10, quality_threshold=5.0, efficiency_budget_pct=10.0)
    base.update(over)
    return EvalPolicy(**base)


def _within():
    return efficiency_delta({"cost_usd": 0.95}, {"cost_usd": 1.00})   # candidate cheaper → within budget


def _over():
    return efficiency_delta({"cost_usd": 1.20}, {"cost_usd": 1.00})   # 20% more expensive → over 10% budget


# ---- ordering (verbatim port) ---------------------------------------------------------------------

def test_low_confidence_when_below_min_cases():
    assert verdict(5, True, 90.0, 90.0, _within(), _policy()) == "low_confidence"


def test_fail_on_hard_assertion():
    assert verdict(12, False, 90.0, 90.0, _within(), _policy()) == "fail"


def test_fail_on_quality_drop_over_threshold():
    assert verdict(12, True, 90.0, 80.0, _within(), _policy()) == "fail"   # q_drop 10 > 5


def test_fail_on_efficiency_over_budget():
    assert verdict(12, True, 90.0, 90.0, _over(), _policy()) == "fail"


def test_pass_when_all_green():
    assert verdict(12, True, 90.0, 88.0, _within(), _policy()) == "pass"   # q_drop 2 < 5, within budget


def test_ordering_low_confidence_wins_over_fail():
    # below MIN_CASES AND det-red AND huge quality drop AND cost blown → low_confidence (statistical honesty first)
    assert verdict(3, False, 100.0, 0.0, _over(), _policy()) == "low_confidence"


def test_ordering_hard_assert_wins_over_quality_and_efficiency():
    assert verdict(12, False, 90.0, 10.0, _over(), _policy()) == "fail"


def test_efficiency_delta_boundary_is_within_budget():
    eff = EfficiencyDelta(cost_pct=10.0)
    assert eff.within_budget(10.0) is True
    assert eff.within_budget(9.999) is False


def test_efficiency_delta_zero_baseline_nonzero_candidate_is_inf():
    eff = efficiency_delta({"cost_usd": 0.01}, {"cost_usd": 0.0})
    assert eff.cost_pct == float("inf")
    assert eff.within_budget(10_000.0) is False


def test_efficiency_delta_zero_baseline_zero_candidate_is_zero():
    eff = efficiency_delta(0.0, 0.0)
    assert eff.cost_pct == 0.0
    assert eff.within_budget(0.0) is True


def test_efficiency_delta_accepts_float_and_attr_forms():
    class _Agg:
        cost_usd = 2.0
    assert efficiency_delta(2.0, 1.0).cost_pct == pytest.approx(100.0)
    assert efficiency_delta(_Agg(), {"cost_usd": 1.0}).cost_pct == pytest.approx(100.0)


# ---- M3 additions: specific reasons (FR-009/SC-006) + per-workspace policy (FR-012) ---------------

def test_reason_low_confidence_names_the_case_floor():
    v, reason = verdict_with_reason(5, True, 90.0, 90.0, _within(), _policy())
    assert v == "low_confidence"
    assert "5" in reason and "10" in reason          # names actual count + floor (specific, not generic)


def test_reason_quality_names_the_drop():
    v, reason = verdict_with_reason(12, True, 90.0, 80.0, _within(), _policy())
    assert v == "fail"
    assert "10.0" in reason and "quality" in reason.lower()


def test_reason_efficiency_names_the_overage():
    v, reason = verdict_with_reason(12, True, 90.0, 90.0, _over(), _policy())
    assert v == "fail"
    assert "%" in reason and "budget" in reason.lower()


def test_reason_pass_is_specific():
    v, reason = verdict_with_reason(12, True, 90.0, 88.0, _within(), _policy())
    assert v == "pass"
    assert reason and "baseline" in reason.lower()


def test_per_workspace_policy_changes_the_verdict():
    """FR-012 — the same scores can PASS under a lenient workspace policy and FAIL under a strict one."""
    # q_drop = 8; lenient threshold 10 → pass, strict threshold 5 → fail
    lenient = _policy(quality_threshold=10.0)
    strict = _policy(quality_threshold=5.0)
    assert verdict(12, True, 90.0, 82.0, _within(), lenient) == "pass"
    assert verdict(12, True, 90.0, 82.0, _within(), strict) == "fail"
