"""score_quality — weighted blend + 1–5 → 0–100 normalization (05:141-142)."""

import pytest

from evals.drift import score_quality


def test_all_max_is_100():
    scores = {"accuracy": 5.0, "completeness": 5.0}
    weights = {"accuracy": 1.0, "completeness": 1.0}
    assert score_quality(scores, weights) == pytest.approx(100.0)


def test_all_min_is_0():
    scores = {"accuracy": 1.0, "completeness": 1.0}
    weights = {"accuracy": 1.0, "completeness": 1.0}
    assert score_quality(scores, weights) == pytest.approx(0.0)


def test_all_middle_is_50():
    scores = {"accuracy": 3.0, "completeness": 3.0}
    weights = {"accuracy": 2.0, "completeness": 7.0}  # weights irrelevant when scores are equal
    assert score_quality(scores, weights) == pytest.approx(50.0)


def test_weighting_shifts_the_blend():
    # accuracy=5 (weight 3), completeness=1 (weight 1) → weighted mean = (15+1)/4 = 4.0 → (4-1)/4*100 = 75
    scores = {"accuracy": 5.0, "completeness": 1.0}
    weights = {"accuracy": 3.0, "completeness": 1.0}
    assert score_quality(scores, weights) == pytest.approx(75.0)


def test_zero_weights_fall_back_to_equal():
    scores = {"accuracy": 5.0, "completeness": 1.0}  # mean 3.0 → 50
    weights = {}
    assert score_quality(scores, weights) == pytest.approx(50.0)


def test_empty_scores_is_zero():
    assert score_quality({}, {}) == 0.0
