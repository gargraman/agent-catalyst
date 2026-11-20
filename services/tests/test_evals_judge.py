"""US3 — the advisory, versioned judge runs through the SAME runtime.invoke_result (P6). Fully mocked:
only gateway.stream is patched, so the judge genuinely exercises the runtime pipeline. No live/paid calls."""
from __future__ import annotations

import asyncio
import json
import random

from runtime.providers import gateway

from evals.judge import JUDGE_MODEL, JUDGE_VERSION, _present_order, judge_case
from tests._evals_helpers import make_context, make_eval_case, make_routed_gateway
from tests._invoke_helpers import priced_usage, stop_ev, text_ev, usage_ev


def test_judge_returns_versioned_verdict_via_runtime(monkeypatch):
    seen = {"model": None}
    gw = make_routed_gateway(
        judge_body={"rubric": {"accuracy": 4.5, "completeness": 4.0}, "pairwise": "candidate",
                    "rationale": "candidate is more complete"},
        on_judge_call=lambda req: seen.__setitem__("model", req.model),
    )
    monkeypatch.setattr(gateway, "stream", gw)

    case = make_eval_case()
    ctx = make_context()
    jv = asyncio.run(judge_case(case, baseline_out="{}", candidate_out=json.dumps({"severity": "high"}), ctx=ctx))

    # FR-014 shape + FR-017 versioned identity
    assert jv.rubric_scores == {"accuracy": 4.5, "completeness": 4.0}
    assert jv.pairwise_winner == "candidate"
    assert jv.rationale
    assert jv.judge_version == JUDGE_VERSION == "judge-v1"
    assert jv.judge_model == JUDGE_MODEL == "claude-haiku-5-5"
    assert jv.judge_prompt_hash
    assert jv.low_confidence is False
    # FR-015/P6 — the judge went through the real runtime path using the judge model
    assert seen["model"] == "claude-haiku-5-5"
    # P8 — a RunRecord for the judge call was emitted to the sink
    assert len(ctx.record_run.runs) == 1


def test_pairwise_order_is_seeded_and_reproducible():
    """FR-018/D5 — presentation order is randomized to cancel position bias, but seedable for reproducibility."""
    a = _present_order(random.Random(42))
    b = _present_order(random.Random(42))
    assert a == b                                   # same seed → same order
    assert sorted(a) == ["baseline", "candidate"]   # both always present, order may swap
    # over many seeds, BOTH orders occur (randomization is real, not fixed)
    orders = {tuple(_present_order(random.Random(s))) for s in range(50)}
    assert len(orders) == 2


def test_unparseable_judge_output_fails_closed_low_confidence(monkeypatch):
    async def _stream(req, *, api_key, profile=None, timeout=None, **_resilience):
        yield text_ev("not json {{{")               # judge emitted garbage
        yield usage_ev(priced_usage(cost_usd=0.0001))
        yield stop_ev()
    monkeypatch.setattr(gateway, "stream", _stream)

    case = make_eval_case()
    ctx = make_context()
    jv = asyncio.run(judge_case(case, baseline_out="{}", candidate_out="{}", ctx=ctx))

    assert jv.low_confidence is True               # fail closed, no crash
    assert jv.rubric_scores == {}                  # no fabricated scores
    assert jv.judge_version == JUDGE_VERSION       # still stamped with identity


def test_judge_makes_no_live_call(monkeypatch):
    """SC-005/FR-019 — if the transport is never patched to a live client, the mock proves no real call.
    Here a boom stream asserts the judge only ever touches the mocked gateway, never a live provider."""
    async def _boom(req, *, api_key, profile=None, timeout=None, **_resilience):
        raise AssertionError("no live provider call — gateway must be the mocked one")
        yield  # pragma: no cover
    monkeypatch.setattr(gateway, "stream", _boom)

    case = make_eval_case()
    ctx = make_context()
    try:
        asyncio.run(judge_case(case, baseline_out="{}", candidate_out="{}", ctx=ctx))
    except AssertionError as e:
        assert "no live provider call" in str(e)   # proves the judge DID route through the (mocked) gateway
    else:
        raise AssertionError("judge did not route through the gateway at all")
