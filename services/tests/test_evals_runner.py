"""Integration — run_eval orchestrates det pre-filter → judge (via breaker) → full EvalRun aggregate.
Covers FR-007/FR-013/FR-024/FR-025 + SC-001/SC-002/SC-007. Fully mocked (gateway.stream); no live calls."""
from __future__ import annotations

import asyncio
import json

from runtime.providers import gateway

from evals.drift import EvalPolicy
from evals.runner import run_eval
from evals.types import EvalRunResult
from tests._evals_helpers import (
    JUDGE_MODEL,
    VALID_OUTPUT,
    candidate_snapshot,
    make_baseline,
    make_context,
    make_eval_case,
    make_routed_gateway,
)
from tests._invoke_helpers import priced_usage, stop_ev, text_ev, usage_ev


def _counting_judge_gateway(judge_behaviors):
    """A gateway that returns the candidate output for the candidate model and, for each successive JUDGE
    call, the next behavior in `judge_behaviors` (a dict → that JSON body; "garbage" → unparseable text;
    "raise" → provider exception). The last behavior repeats if there are more calls than entries."""
    state = {"i": -1}

    async def _stream(req, *, api_key, profile=None, timeout=None):
        if getattr(req, "model", None) == JUDGE_MODEL:
            state["i"] += 1
            beh = judge_behaviors[min(state["i"], len(judge_behaviors) - 1)]
            if beh == "raise":
                raise RuntimeError("judge provider 503")
            body = "not json {" if beh == "garbage" else json.dumps(beh)
            yield text_ev(body)
            yield usage_ev(priced_usage(cost_usd=0.0001))
            yield stop_ev()
        else:
            yield text_ev(json.dumps(VALID_OUTPUT))
            yield usage_ev(priced_usage(cost_usd=0.001))
            yield stop_ev()

    return _stream


_GOOD = {"rubric": {"accuracy": 5.0, "completeness": 5.0}, "pairwise": "candidate", "rationale": "great"}


def _cases(n: int, ws: str = "ws_1"):
    return [make_eval_case(cid=f"c{i}", workspace_id=ws) for i in range(n)]


def _baseline(n: int, **kw):
    per = {f"c{i}": json.dumps(VALID_OUTPUT) for i in range(n)}
    return make_baseline(per_case=per, **kw)


def _policy(**o) -> EvalPolicy:
    base = dict(min_cases=3, quality_threshold=10.0, efficiency_budget_pct=10.0)
    base.update(o)
    return EvalPolicy(**base)


def test_hard_fail_cases_skip_the_judge(monkeypatch):
    """FR-007/SC-002 — an empty candidate output hard-fails deterministically and never reaches the judge."""
    judged = {"n": 0}
    gw = make_routed_gateway(candidate_text="   ",  # whitespace → _is_empty → hard_fail
                             on_judge_call=lambda req: judged.__setitem__("n", judged["n"] + 1))
    monkeypatch.setattr(gateway, "stream", gw)
    res = asyncio.run(run_eval(_cases(3), candidate_snapshot(), _baseline(3, cost_usd=0.003),
                               _policy(min_cases=1), make_context(), workspace_id="ws_1"))
    assert judged["n"] == 0                              # judge never invoked on hard-failed cases
    assert res.verdict == "fail"                         # correctness floor
    assert res.failing_cases and all(fc.hard for fc in res.failing_cases)


def test_seeded_regression_fails_and_unchanged_passes(monkeypatch):
    """SC-001 — mechanical drift detection: a degraded candidate (low judge scores) → fail; unchanged → pass."""
    ctx = make_context()
    cases, baseline = _cases(3), _baseline(3, quality=90.0, cost_usd=0.003)

    gw_bad = make_routed_gateway(judge_body={"rubric": {"accuracy": 1.5, "completeness": 1.0},
                                             "pairwise": "baseline", "rationale": "worse"})
    monkeypatch.setattr(gateway, "stream", gw_bad)
    regressed = asyncio.run(run_eval(cases, candidate_snapshot(), baseline, _policy(), ctx, workspace_id="ws_1"))
    assert regressed.verdict == "fail"

    gw_good = make_routed_gateway(judge_body={"rubric": {"accuracy": 5.0, "completeness": 5.0},
                                              "pairwise": "candidate", "rationale": "great"})
    monkeypatch.setattr(gateway, "stream", gw_good)
    unchanged = asyncio.run(run_eval(cases, candidate_snapshot(), baseline, _policy(), ctx, workspace_id="ws_1"))
    assert unchanged.verdict == "pass"


def test_returns_full_evalrun_shape(monkeypatch):
    """FR-025 — the aggregate carries the full frontend EvalRun shape."""
    monkeypatch.setattr(gateway, "stream", make_routed_gateway())
    res = asyncio.run(run_eval(_cases(3), candidate_snapshot(), _baseline(3, cost_usd=0.003),
                               _policy(), make_context(), workspace_id="ws_1"))
    assert isinstance(res, EvalRunResult)
    for attr in ("verdict", "reason", "quality", "quality_delta", "cost_delta", "latency_delta",
                 "cases", "dimensions", "failing_cases", "pairwise_rate", "workspace_id", "judge_version"):
        assert hasattr(res, attr), f"missing {attr}"
    assert res.cases == 3
    assert res.dimensions                               # per-dimension scores present
    assert res.judge_version == "judge-v1"
    assert res.reason


def test_pairwise_loss_is_advisory_not_gating(monkeypatch):
    """FR-016/FR-025/SC-007 — candidate losing every pairwise does NOT flip a within-policy verdict; the
    pairwise rate is surfaced, never gated."""
    gw = make_routed_gateway(judge_body={"rubric": {"accuracy": 5.0, "completeness": 5.0},
                                         "pairwise": "baseline", "rationale": "baseline wins head-to-head"})
    monkeypatch.setattr(gateway, "stream", gw)
    res = asyncio.run(run_eval(_cases(3), candidate_snapshot(), _baseline(3, quality=90.0, cost_usd=0.003),
                               _policy(), make_context(), workspace_id="ws_1"))
    assert res.verdict == "pass"                        # quality held → pass, despite losing every pairwise
    assert res.pairwise_rate == 0.0                     # advisory signal surfaced (candidate won 0 of 3)


def test_candidate_runs_marked_eval_source(monkeypatch):
    """FR-024 (honest marker) — eval runs are tagged source='eval' (the forward cache-bypass signal; the
    M2a runtime has no response cache yet). Every emitted RunRecord is eval-sourced."""
    monkeypatch.setattr(gateway, "stream", make_routed_gateway())
    ctx = make_context(source="playground")             # even if a non-eval source is passed…
    asyncio.run(run_eval(_cases(3), candidate_snapshot(), _baseline(3, cost_usd=0.003),
                         _policy(), ctx, workspace_id="ws_1"))
    assert ctx.record_run.runs
    assert all(r.source == "eval" for r in ctx.record_run.runs)   # …the runner forces eval source


# ---- T020 (converge Phase 8) — spec Edge Cases: handled-but-previously-untested behaviors -----------

def test_empty_suite_is_low_confidence_without_crashing(monkeypatch):
    """Spec edge case — a zero-case suite returns low_confidence (below any min_cases floor), never crashes."""
    monkeypatch.setattr(gateway, "stream", make_routed_gateway())
    res = asyncio.run(run_eval([], candidate_snapshot(), make_baseline(per_case={}),
                               _policy(), make_context(), workspace_id="ws_1"))
    assert res.verdict == "low_confidence"
    assert res.cases == 0
    assert res.failing_cases == () and res.dimensions == ()


def test_all_tie_pairwise_does_not_crash_and_rate_is_zero(monkeypatch):
    """Spec edge case — an all-`tie` pairwise run computes a verdict with pairwise_rate 0.0 (ties aren't wins)."""
    gw = make_routed_gateway(judge_body={"rubric": {"accuracy": 3.0, "completeness": 3.0},
                                         "pairwise": "tie", "rationale": "even match"})
    monkeypatch.setattr(gateway, "stream", gw)
    res = asyncio.run(run_eval(_cases(3), candidate_snapshot(), _baseline(3, quality=50.0, cost_usd=0.003),
                               _policy(), make_context(), workspace_id="ws_1"))
    assert res.pairwise_rate == 0.0                      # ties are not head-to-head wins
    assert res.verdict == "pass"                         # candidate quality 50 == baseline 50, cost within


# ---- post-review hardening (code-review 2026-11-05) — regression tests for the robustness fixes -----

def test_low_confidence_judge_excluded_from_quality(monkeypatch):
    """A fail-closed (low_confidence) judge verdict is a NON-score, not a quality-0 — it must not drag the
    quality mean and flip an otherwise-passing candidate to fail (advisory judge, FR-016)."""
    # c0, c1 score 5/5 (quality 100); c2's judge output is garbage → low_confidence, excluded from quality.
    gw = _counting_judge_gateway([_GOOD, _GOOD, "garbage"])
    monkeypatch.setattr(gateway, "stream", gw)
    res = asyncio.run(run_eval(_cases(3), candidate_snapshot(), _baseline(3, quality=90.0, cost_usd=0.003),
                               _policy(), make_context(), workspace_id="ws_1"))
    assert res.quality == 100.0                          # mean of the 2 SCORED cases only (not dragged to 66.7)
    assert res.verdict == "pass"                         # q_drop -10 → pass; the low_confidence case didn't gate


def test_judge_exception_does_not_abort_the_run(monkeypatch):
    """An advisory judge/transport exception degrades that case to fail-closed — it must never abort the
    whole suite (the deterministic floor already ran)."""
    gw = _counting_judge_gateway([_GOOD, "raise", _GOOD])   # middle judge call explodes
    monkeypatch.setattr(gateway, "stream", gw)
    res = asyncio.run(run_eval(_cases(3), candidate_snapshot(), _baseline(3, quality=90.0, cost_usd=0.003),
                               _policy(), make_context(), workspace_id="ws_1"))
    assert isinstance(res, EvalRunResult)               # completed with a verdict — did not raise
    assert res.verdict == "pass"                         # the 2 scored cases (100) carry the verdict


def test_total_judge_outage_is_low_confidence_not_fail(monkeypatch):
    """P8 evidence honesty — if EVERY judge call fails (total outage) past the correctness floor, there is no
    quality signal, so the verdict is low_confidence, NOT a fabricated quality-0 'fail'."""
    gw = _counting_judge_gateway(["raise"])             # every judge call raises
    monkeypatch.setattr(gateway, "stream", gw)
    res = asyncio.run(run_eval(_cases(3), candidate_snapshot(), _baseline(3, quality=90.0, cost_usd=0.003),
                               _policy(), make_context(), workspace_id="ws_1"))
    assert res.verdict == "low_confidence"
    assert "judge" in res.reason.lower()
