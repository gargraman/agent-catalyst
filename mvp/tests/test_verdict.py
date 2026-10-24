"""verdict() ordering — one test per branch of 05:147-158, plus a test that proves the ORDER."""

from evals.drift import EfficiencyDelta, EvalPolicy, efficiency_delta, verdict


def _policy() -> EvalPolicy:
    return EvalPolicy(min_cases=10, quality_threshold=5.0, efficiency_budget_pct=10.0)


def _within():
    # candidate slightly cheaper than baseline → within any non-negative budget
    return efficiency_delta({"cost_usd": 0.95}, {"cost_usd": 1.00})


def _over():
    # candidate 20% more expensive → over a 10% budget
    return efficiency_delta({"cost_usd": 1.20}, {"cost_usd": 1.00})


def test_low_confidence_when_below_min_cases():
    assert verdict(5, True, 90.0, 90.0, _within(), _policy()) == "low_confidence"


def test_fail_on_hard_assertion():
    assert verdict(12, False, 90.0, 90.0, _within(), _policy()) == "fail"


def test_fail_on_quality_drop_over_threshold():
    # q_drop = 90 - 80 = 10 > 5.0
    assert verdict(12, True, 90.0, 80.0, _within(), _policy()) == "fail"


def test_fail_on_efficiency_over_budget():
    # no quality drop, but cost over budget
    assert verdict(12, True, 90.0, 90.0, _over(), _policy()) == "fail"


def test_pass_when_all_green():
    # q_drop = 2 (< 5), cost within budget
    assert verdict(12, True, 90.0, 88.0, _within(), _policy()) == "pass"


def test_ordering_low_confidence_wins_over_fail():
    # below MIN_CASES AND det-red AND huge quality drop AND cost blown — ordering says low_confidence.
    assert verdict(3, False, 100.0, 0.0, _over(), _policy()) == "low_confidence"


def test_ordering_hard_assert_wins_over_quality_and_efficiency():
    # enough cases, but det-red beats both the quality-drop and efficiency checks → fail (via hard assert).
    result = verdict(12, False, 90.0, 10.0, _over(), _policy())
    assert result == "fail"


def test_efficiency_delta_boundary_is_within_budget():
    # exactly at budget counts as within (<=)
    eff = EfficiencyDelta(cost_pct=10.0)
    assert eff.within_budget(10.0) is True
    assert eff.within_budget(9.999) is False
