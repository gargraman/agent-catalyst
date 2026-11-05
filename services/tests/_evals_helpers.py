"""Shared mocked harness for the eval & drift engine tests (M3, feature 004).

No network, no api_key use, no live/paid calls (SC-005). The judge and candidate both run through the real
`runtime.invoke_result`; only the transport (`runtime.providers.gateway.stream`) is mocked — which is what
proves the P6 invariant (eval scores what production runs). The gateway double routes by `req.model` so one
patched stream can serve both the candidate model and the judge model (`claude-haiku-5-5`) in one eval run.
"""
from __future__ import annotations

import json

from runtime.snapshot import VersionSnapshot
from tests._invoke_helpers import (  # reuse the proven M2a builders
    PROFILE_SNAPSHOT,
    CapturingSink,
    make_context,
    make_snapshot,
    priced_usage,
    stop_ev,
    text_ev,
    usage_ev,
)
from evals.types import Assertion, BaselineAggregate, EvalCase, RubricDimension

JUDGE_MODEL = "claude-haiku-5-5"

# A well-formed triage candidate output (passes the deterministic floor).
VALID_OUTPUT = {"severity": "high", "summary": "DB CPU at 98% for 20m", "next_step": "page the on-call DBA"}


def make_eval_case(
    cid: str = "c1",
    *,
    workspace_id: str = "ws_1",
    inp: str = "An XDR alert fired: DB CPU 98%.",
    rubric=(("accuracy", 1.0), ("completeness", 1.0)),
    required_fields=None,
    expected=None,
    assertions=(),
) -> EvalCase:
    return EvalCase(
        id=cid,
        input=inp,
        workspace_id=workspace_id,
        rubric=tuple(RubricDimension(name=n, weight=w) for n, w in rubric),
        required_fields=required_fields,
        expected=expected,
        assertions=tuple(assertions),
    )


def make_baseline(
    *,
    quality: float = 90.0,
    cost_usd: float = 0.001,
    latency_ms: int = 100,
    per_case: dict[str, str] | None = None,
    workspace_id: str = "ws_1",
) -> BaselineAggregate:
    return BaselineAggregate(
        quality=quality,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        per_case=per_case if per_case is not None else {"c1": json.dumps(VALID_OUTPUT)},
        workspace_id=workspace_id,
    )


def candidate_snapshot(**overrides) -> VersionSnapshot:
    """A candidate agent snapshot (openai gpt-5-mini) — distinct model from the judge, so the routed gateway
    can tell candidate from judge calls."""
    return make_snapshot(**overrides)


def judge_payload(*, rubric=None, pairwise="candidate", rationale="looks solid") -> dict:
    """The strict-JSON body the judge model is expected to emit."""
    return {
        "rubric": rubric if rubric is not None else {"accuracy": 4.5, "completeness": 4.0},
        "pairwise": pairwise,
        "rationale": rationale,
    }


def make_routed_gateway(
    *,
    candidate_text: str | None = None,
    candidate_cost: float = 0.001,
    judge_body: dict | None = None,
    judge_cost: float = 0.0001,
    on_judge_call=None,
):
    """A drop-in `gateway.stream` that returns the judge JSON for the judge model and candidate output for
    any other model. `on_judge_call` (optional) is invoked once per judge call for assertions/failure
    injection (e.g. to trip the breaker)."""
    cand = candidate_text if candidate_text is not None else json.dumps(VALID_OUTPUT)
    jbody = judge_body if judge_body is not None else judge_payload()

    async def _stream(req, *, api_key, profile=None, timeout=None):
        if getattr(req, "model", None) == JUDGE_MODEL:
            if on_judge_call is not None:
                on_judge_call(req)
            yield text_ev(json.dumps(jbody))
            yield usage_ev(priced_usage(cost_usd=judge_cost))
            yield stop_ev()
        else:
            yield text_ev(cand)
            yield usage_ev(priced_usage(cost_usd=candidate_cost))
            yield stop_ev()

    return _stream


def boom_gateway():
    """A gateway that must never be called (asserts no live/provider path on a pre-call rejection)."""
    def _boom(*a, **k):
        raise AssertionError("gateway.stream must not be called")
    return _boom
