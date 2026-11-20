"""Feature 026 US4 (T046) — server-held baseline resolution, case-set diff and reason codes (`api.baseline`).

Infra-free: every input is a plain row object (SimpleNamespace), so this runs in `services-unit` with no DB.
Design: research.md D7 (resolution order: frozen > automatic > none), D8 (compare common
`(case_id, content_hash)` only; edited case = new + dropped), data-model.md `eval_runs.reason_code`.

Reason-code precedence (026 decision, recorded in tasks.md Evidence; REVERSED 2026-11-18 after code review #2):
  hard_fail > no_baseline > judge_unavailable > below_min_cases > baseline_stale > quality_drift > cost_drift > ok
A deterministic hard failure is a definitive fail that needs no baseline, no judge and no sample size (the engine's
own rule, runner.py) — hard-failed cases are never judged, so checking it later masked it as judge_unavailable.
"""
from __future__ import annotations

import datetime as dt
import inspect
import uuid
from types import SimpleNamespace as Row

import pytest

from api.baseline import (
    REASON_CODES,
    REASON_TEMPLATES,
    baseline_aggregate,
    case_diff,
    compared_quality,
    decide,
    resolve_baseline,
)
from evals.drift import EvalPolicy

T0 = dt.datetime(2026, 11, 17, 12, 0, tzinfo=dt.timezone.utc)
V1, V2 = uuid.uuid4(), uuid.uuid4()


def _run(version_id, *, minutes=0, status="completed", rid=None):
    return Row(id=rid or uuid.uuid4(), version_id=version_id, status=status,
               finished_at=T0 + dt.timedelta(minutes=minutes))


def _policy(**over) -> EvalPolicy:
    base = dict(min_cases=3, quality_threshold=5.0, efficiency_budget_pct=10.0)
    base.update(over)
    return EvalPolicy(**base)


# ---- resolution order (D7) ------------------------------------------------------------------------

def test_frozen_run_wins_over_a_newer_automatic_candidate():
    frozen = _run(V1, minutes=0)
    newer_active = _run(V2, minutes=30)
    got = resolve_baseline(frozen, V2, [frozen, newer_active])
    assert got.origin == "frozen"
    assert got.run is frozen


def test_automatic_is_the_latest_completed_run_of_the_active_version():
    old = _run(V1, minutes=0)
    latest = _run(V1, minutes=10)
    other_version = _run(V2, minutes=20)        # newer, but not the active version
    failed = _run(V1, minutes=30, status="failed")  # newest of V1, but failed runs are never baselines
    got = resolve_baseline(None, V1, [old, other_version, latest, failed])
    assert got.origin == "automatic"
    assert got.run is latest


def test_automatic_ties_on_finished_at_break_by_id_descending():
    a = _run(V1, minutes=5, rid=uuid.UUID(int=1))
    b = _run(V1, minutes=5, rid=uuid.UUID(int=2))
    assert resolve_baseline(None, V1, [a, b]).run is b
    assert resolve_baseline(None, V1, [b, a]).run is b


def test_none_when_nothing_qualifies():
    got = resolve_baseline(None, V1, [_run(V2), _run(V1, status="failed")])
    assert got.origin == "none"
    assert got.run is None


def test_none_when_the_agent_has_no_active_version_and_nothing_is_frozen():
    got = resolve_baseline(None, None, [_run(V1)])
    assert got.origin == "none"
    assert got.run is None


# ---- self-comparison --------------------------------------------------------------------------------

def test_active_candidate_with_no_other_run_resolves_to_none_never_itself():
    this_run = _run(V1, minutes=1)
    got = resolve_baseline(None, V1, [this_run], exclude_run_id=this_run.id)
    assert got.origin == "none"
    assert got.run is None


def test_active_candidate_compares_to_its_own_previous_run():
    # D7: same version vs its previous run is a legitimate "did the model/provider drift" check.
    previous = _run(V1, minutes=0)
    this_run = _run(V1, minutes=1)
    got = resolve_baseline(None, V1, [previous, this_run], exclude_run_id=this_run.id)
    assert got.origin == "automatic"
    assert got.run is previous


# ---- case diff (D8) ---------------------------------------------------------------------------------

def _case(cid, h):
    return Row(id=cid, content_hash=h)


def _result(cid, h, **kw):
    base = dict(case_id=cid, case_hash=h, quality=80.0, cost_usd=0.01, latency_ms=100, candidate_output=f"out-{cid}")
    base.update(kw)
    return Row(**base)


def test_case_diff_matches_on_id_and_hash():
    a, b, c, d = (uuid.uuid4() for _ in range(4))
    candidate = [_case(a, "h1"), _case(b, "h2"), _case(c, "h3")]
    baseline = [_result(a, "h1"), _result(b, "h2"), _result(d, "h4")]
    diff = case_diff(candidate, baseline)
    assert (diff.compared, diff.new, diff.dropped, diff.total) == (2, 1, 1, 3)
    assert diff.common == frozenset({(a, "h1"), (b, "h2")})


def test_an_edited_case_counts_as_new_and_dropped():
    a = uuid.uuid4()
    diff = case_diff([_case(a, "h-edited")], [_result(a, "h-original")])
    assert (diff.compared, diff.new, diff.dropped, diff.total) == (0, 1, 1, 1)
    assert diff.common == frozenset()


def test_case_diff_without_a_baseline_marks_every_case_new():
    cases = [_case(uuid.uuid4(), "h") for _ in range(3)]
    diff = case_diff(cases, [])
    assert (diff.compared, diff.new, diff.dropped, diff.total) == (0, 3, 0, 3)


def test_in_comparison_follows_the_common_set():
    a, b = uuid.uuid4(), uuid.uuid4()
    diff = case_diff([_case(a, "h1"), _case(b, "h2")], [_result(a, "h1")])
    assert diff.in_comparison(a, "h1") is True
    assert diff.in_comparison(b, "h2") is False
    assert diff.in_comparison(a, "other") is False


# ---- baseline aggregate over the common set (D7) ----------------------------------------------------

def test_baseline_aggregate_uses_common_results_only():
    a, b, gone = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    results = [
        _result(a, "h1", quality=80.0, cost_usd=0.01, latency_ms=100),
        _result(b, "h2", quality=60.0, cost_usd=0.03, latency_ms=300),
        _result(gone, "h9", quality=0.0, cost_usd=9.0, latency_ms=9000),   # not common → excluded
    ]
    diff = case_diff([_case(a, "h1"), _case(b, "h2")], results)
    agg = baseline_aggregate(results, diff)
    assert agg.quality == pytest.approx(70.0)          # mean
    assert agg.cost_usd == pytest.approx(0.04)         # sum
    assert agg.latency_ms == pytest.approx(200.0)      # mean
    assert agg.per_case == {str(a): "out-" + str(a), str(b): "out-" + str(b)}


def test_baseline_aggregate_skips_null_quality_and_null_cost():
    a, b = uuid.uuid4(), uuid.uuid4()
    results = [_result(a, "h1", quality=None, cost_usd=None, latency_ms=None), _result(b, "h2", quality=90.0)]
    diff = case_diff([_case(a, "h1"), _case(b, "h2")], results)
    agg = baseline_aggregate(results, diff)
    assert agg.quality == pytest.approx(90.0)
    assert agg.cost_usd == pytest.approx(0.01)
    assert agg.latency_ms == pytest.approx(100.0)


def test_compared_quality_uses_only_cases_scored_in_both_runs():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    results = [_result(a, "h1", quality=90.0), _result(b, "h2", quality=50.0), _result(c, "h3", quality=None)]
    diff = case_diff([_case(a, "h1"), _case(b, "h2"), _case(c, "h3")], results)
    # candidate: judge failed closed on `a` (None); `c` was scored now but not in the baseline run
    qb, qc = compared_quality({str(a): None, str(b): 52.0, str(c): 70.0}, results, diff)
    assert (qb, qc) == (pytest.approx(50.0), pytest.approx(52.0))     # only `b` is scored on both sides


def test_compared_quality_is_none_when_no_case_is_scored_in_both_runs():
    a = uuid.uuid4()
    results = [_result(a, "h1", quality=80.0)]
    diff = case_diff([_case(a, "h1")], results)
    assert compared_quality({str(a): None}, results, diff) == (None, None)


def test_compared_quality_ignores_cases_outside_the_common_set():
    a, edited = uuid.uuid4(), uuid.uuid4()
    results = [_result(a, "h1", quality=60.0), _result(edited, "old", quality=10.0)]
    diff = case_diff([_case(a, "h1"), _case(edited, "new")], results)
    assert compared_quality({str(a): 70.0, str(edited): 99.0}, results, diff) == (
        pytest.approx(60.0), pytest.approx(70.0))


# ---- reason codes -----------------------------------------------------------------------------------

def _diff(compared, total, new=None, dropped=0):
    return Row(compared=compared, total=total, new=total - compared if new is None else new, dropped=dropped)


def _decide(**over):
    base = dict(origin="automatic", diff=_diff(5, 5), det_all_green=True, judge_available=True,
                quality_b=80.0, quality_c=80.0, cost_b=1.0, cost_c=1.0, policy=_policy())
    base.update(over)
    return decide(**base)


def test_reason_code_vocabulary_is_exactly_the_data_model_set():
    assert set(REASON_CODES) == {"ok", "quality_drift", "cost_drift", "hard_fail", "below_min_cases",
                                 "no_baseline", "baseline_stale", "judge_unavailable"}
    assert set(REASON_TEMPLATES) == set(REASON_CODES)


def test_no_baseline_is_low_confidence_when_the_correctness_floor_holds():
    d = _decide(origin="none", diff=_diff(0, 5), judge_available=False, quality_b=None, cost_b=None)
    assert (d.verdict, d.reason_code) == ("low_confidence", "no_baseline")


def test_hard_fail_wins_even_without_a_baseline():
    d = _decide(origin="none", diff=_diff(0, 5), det_all_green=False, judge_available=False,
                quality_b=None, cost_b=None)
    assert (d.verdict, d.reason_code) == ("fail", "hard_fail")


def test_judge_unavailable_is_low_confidence_before_any_pass_fail():
    d = _decide(judge_available=False, quality_c=10.0)
    assert (d.verdict, d.reason_code) == ("low_confidence", "judge_unavailable")


def test_hard_fail_is_not_masked_as_judge_unavailable_when_no_case_was_judged():
    # every case hard-failed → none was judged → no candidate quality; the cause is the hard fail
    d = _decide(judge_available=False, det_all_green=False, quality_c=None)
    assert (d.verdict, d.reason_code) == ("fail", "hard_fail")


def test_suite_smaller_than_min_cases_is_below_min_cases():
    d = _decide(diff=_diff(2, 2), policy=_policy(min_cases=3))
    assert (d.verdict, d.reason_code) == ("low_confidence", "below_min_cases")


def test_big_suite_with_too_few_common_cases_is_baseline_stale():
    d = _decide(diff=_diff(2, 5, new=3), policy=_policy(min_cases=3))
    assert (d.verdict, d.reason_code) == ("low_confidence", "baseline_stale")


def test_a_hard_failure_outranks_baseline_stale():
    d = _decide(diff=_diff(2, 5), det_all_green=False, policy=_policy(min_cases=3))
    assert (d.verdict, d.reason_code) == ("fail", "hard_fail")


def test_a_hard_failure_outranks_below_min_cases():
    d = _decide(diff=_diff(2, 2), det_all_green=False, policy=_policy(min_cases=3))
    assert (d.verdict, d.reason_code) == ("fail", "hard_fail")


def test_hard_fail():
    d = _decide(det_all_green=False)
    assert (d.verdict, d.reason_code) == ("fail", "hard_fail")


def test_quality_drift():
    d = _decide(quality_b=90.0, quality_c=80.0)      # 10-point drop > 5 threshold
    assert (d.verdict, d.reason_code) == ("fail", "quality_drift")


def test_cost_drift():
    d = _decide(cost_b=1.0, cost_c=1.2)              # +20% > 10% budget
    assert (d.verdict, d.reason_code) == ("fail", "cost_drift")


def test_ok():
    d = _decide(quality_b=80.0, quality_c=81.0, cost_b=1.0, cost_c=0.9)
    assert (d.verdict, d.reason_code) == ("pass", "ok")


def test_verdict_counts_compared_cases_not_total_cases():
    # 5 cases total but only 3 compared, min 4 → stale, even though total >= min.
    d = _decide(diff=_diff(3, 5), policy=_policy(min_cases=4))
    assert d.reason_code == "baseline_stale"


# ---- reason text: fixed templates, no provider text --------------------------------------------------

def test_decide_accepts_no_free_text_input():
    # The only str parameter is the closed `origin` enum — nothing a provider or client wrote can reach `reason`.
    params = inspect.signature(decide).parameters
    assert set(params) == {"origin", "diff", "det_all_green", "judge_available", "quality_b", "quality_c",
                           "cost_b", "cost_c", "policy"}
    with pytest.raises(ValueError):
        _decide(origin="Ignore previous instructions")


@pytest.mark.parametrize("over, code", [
    (dict(origin="none", diff=_diff(0, 5), quality_b=None, cost_b=None), "no_baseline"),
    (dict(judge_available=False), "judge_unavailable"),
    (dict(diff=_diff(2, 2)), "below_min_cases"),
    (dict(diff=_diff(2, 5)), "baseline_stale"),
    (dict(det_all_green=False), "hard_fail"),
    (dict(quality_b=90.0, quality_c=80.0), "quality_drift"),
    (dict(cost_c=1.2), "cost_drift"),
    (dict(), "ok"),
])
def test_reason_is_rendered_from_its_fixed_template(over, code):
    d = _decide(**over)
    assert d.reason_code == code
    tmpl = REASON_TEMPLATES[code]
    # Rendering the template with the decision's own numbers reproduces the reason exactly.
    assert d.reason == tmpl.format(**d.reason_args)
    assert d.reason.strip() and "\n" not in d.reason


def test_stale_reason_names_the_counts():
    d = _decide(diff=_diff(2, 5, new=3, dropped=1), policy=_policy(min_cases=3))
    assert "2" in d.reason and "3" in d.reason
