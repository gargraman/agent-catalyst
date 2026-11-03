"""H1 plumbing harness — offline, mocked judge. Proves the eval pipeline mechanically flags a
seeded regression on each verdict axis and passes an unchanged control (NOT the hypothesis result).
"""
from experiments.h1_eval import SUITE, assert_h1, default_policy, run_h1


def test_control_arm_passes_no_false_positive():
    r = run_h1()
    assert r["control_verdict"] == "pass"
    assert r["false_positive_rate"] == 0.0


def test_every_seed_is_detected():
    r = run_h1()
    assert set(r["seed_verdicts"]) == {"correctness", "quality", "efficiency"}
    assert all(v == "fail" for v in r["seed_verdicts"].values())
    assert r["detection_rate"] == 1.0


def test_suite_meets_min_cases_so_verdict_is_not_low_confidence():
    assert len(SUITE) >= default_policy().min_cases


def test_assert_h1_passes_on_a_clean_run():
    assert_h1(run_h1())  # raises if the control is flagged or any seed is missed


def test_each_seed_axis_in_isolation():
    # correctness → det floor; quality → quality drop; efficiency → budget. Each must FAIL on its own.
    r = run_h1()
    assert r["seed_verdicts"]["correctness"] == "fail"
    assert r["seed_verdicts"]["quality"] == "fail"
    assert r["seed_verdicts"]["efficiency"] == "fail"
