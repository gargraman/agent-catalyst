"""US2 — one honest, observable Run per invocation (M2a). Mocked gateway; no live calls (SC-008)."""
from __future__ import annotations

import asyncio
from decimal import Decimal

from runtime import invoke as invoke_mod
from runtime.providers import gateway
from runtime.providers.profiles import Substitution
from runtime.run import Outcome
from tests._invoke_helpers import (
    error_ev,
    make_context,
    make_gateway,
    make_snapshot,
    priced_usage,
    text_ev,
    usage_ev,
)


def _run_once(monkeypatch, events, **ctx_over):
    snap = make_snapshot()
    ctx = make_context(**ctx_over)
    monkeypatch.setattr(gateway, "stream", make_gateway(events))
    res = asyncio.run(invoke_mod.invoke_result(snap, {"message": "hi"}, ctx))
    return res, ctx.record_run.runs


def test_exactly_one_run_on_success(monkeypatch):
    res, runs = _run_once(monkeypatch, [text_ev("ok"), usage_ev(priced_usage(cost_usd=0.000132))])
    assert len(runs) == 1
    r = runs[0]
    assert r.outcome is Outcome.SUCCESS
    assert r.agent_id == "agt_1" and r.version_id == "ver_1" and r.model == "gpt-5-mini"
    assert r.profile_hash and r.prompt_hash == "prompt_hash_1"
    assert r.usage is not None and r.cost_usd == Decimal("0.000132")
    assert r.latency_ms >= 0 and r.correlation_id


def test_exactly_one_run_on_provider_error(monkeypatch):
    err = {"type": "provider_error", "retryable": True, "message": "boom", "status": 503}
    res, runs = _run_once(monkeypatch, [text_ev("partial"), error_ev(err)])
    assert len(runs) == 1
    assert runs[0].outcome is Outcome.PROVIDER_ERROR
    assert runs[0].outcome is not Outcome.SUCCESS
    assert runs[0].error and runs[0].error.get("message") == "boom"


def test_exactly_one_run_on_degraded(monkeypatch):
    err = {"type": "degraded", "retryable": False, "message": "provider stream ended without a usage report"}
    res, runs = _run_once(monkeypatch, [text_ev("partial"), error_ev(err)])
    assert len(runs) == 1
    assert runs[0].outcome is Outcome.DEGRADED
    assert runs[0].usage is None


def test_cost_carried_not_recomputed(monkeypatch):
    # The gateway's cost is authoritative even if it is deliberately inconsistent with the token counts.
    weird = priced_usage(input=1, output=1, cost_usd=9.999999)
    res, runs = _run_once(monkeypatch, [text_ev("x"), usage_ev(weird)])
    assert runs[0].cost_usd == Decimal("9.999999")


def test_correlation_minted_when_absent_and_preserved_when_present(monkeypatch):
    res, runs = _run_once(monkeypatch, [text_ev("x"), usage_ev()], correlation_id=None)
    assert runs[0].correlation_id  # minted
    res2, runs2 = _run_once(monkeypatch, [text_ev("x"), usage_ev()], correlation_id="corr-123")
    assert runs2[0].correlation_id == "corr-123"


def test_substitutions_carried_through(monkeypatch):
    sub = Substitution("prompt_caching", "dropped", "deferred to M7")
    res, runs = _run_once(monkeypatch, [text_ev("x"), usage_ev(subs=(sub,))])
    assert runs[0].substitutions == (sub,)
    assert runs[0].cost_is_estimate is True
