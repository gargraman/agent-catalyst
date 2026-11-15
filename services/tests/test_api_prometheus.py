"""Feature 010 (M6) — US2: /metrics endpoint + run-status / refusal counters (T008).

Integration (compose Postgres); gateway mocked. Counters are process-global → assert before/after deltas.
(Named test_api_prometheus to avoid colliding with feature-006's test_api_metrics.py dashboard tests.)
"""
from __future__ import annotations

import pytest

from api import metrics
from runtime.providers import gateway
from tests._api_helpers import (
    client, dev_headers, seed_agent, seed_limits, seed_run, seed_version, seed_workspace,
)
from tests._invoke_helpers import make_gateway, stop_ev, text_ev, usage_ev

pytestmark = pytest.mark.integration


def _runs(status, source):
    return metrics.REGISTRY.get_sample_value("ac_runs_total", {"status": status, "source": source}) or 0.0


def _refused(code):
    return metrics.REGISTRY.get_sample_value("ac_requests_refused_total", {"code": code}) or 0.0


async def _seed():
    ws = await seed_workspace()
    aid = await seed_agent(ws)
    await seed_version(ws, aid)
    return ws, aid


async def test_metrics_endpoint_is_unauthenticated_prometheus_text():
    async with client() as c:
        r = await c.get("/metrics")                    # no auth headers at all
    assert r.status_code == 200
    assert "text/plain" in r.headers.get("content-type", "")
    assert "ac_runs_total" in r.text or "# HELP" in r.text


async def test_successful_run_increments_runs_counter(monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("ok"), usage_ev(), stop_ev()]))
    ws, aid = await _seed()
    before = _runs("success", "playground")
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/invoke", headers=dev_headers(workspace=ws),
                         json={"inputs": {"message": "x"}})
    assert r.status_code == 200
    assert _runs("success", "playground") == before + 1


async def test_over_cap_increments_refusal_counter_budget(monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("ok"), usage_ev(), stop_ev()]))
    ws, aid = await _seed()
    await seed_limits(ws, 10)
    await seed_run(ws, cost_usd=15.0)                  # over cap → 402 budget.exceeded
    before = _refused("budget.exceeded")
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/invoke", headers=dev_headers(workspace=ws),
                         json={"inputs": {"message": "x"}})
    assert r.status_code == 402
    assert _refused("budget.exceeded") == before + 1


async def test_denied_increments_refusal_counter_access(monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("ok"), usage_ev(), stop_ev()]))
    ws, aid = await _seed()
    before = _refused("access.denied")
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/invoke",
                         headers=dev_headers(workspace=ws, claims={ws: "norole"}),  # unknown role → 403
                         json={"inputs": {"message": "x"}})
    assert r.status_code == 403
    assert _refused("access.denied") == before + 1


async def test_metrics_output_has_no_tenant_or_content(monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("secret-reply"), usage_ev(), stop_ev()]))
    ws, aid = await _seed()
    async with client() as c:
        await c.post(f"/api/v1/agents/{aid}/invoke", headers=dev_headers(workspace=ws),
                     json={"inputs": {"message": "secret-prompt"}})
        r = await c.get("/metrics")
    body = r.text
    assert ws not in body                              # no workspace id
    assert "secret-prompt" not in body and "secret-reply" not in body   # no content


def test_retrieval_seconds_observed_only_for_runs_with_knowledge():
    """Feature 024 (T049): `ac_retrieval_seconds{source}` observes `rec.retrieval.retrieval_ms`; a run with no knowledge
    bound (retrieval None) adds no sample. Aggregate-only — no tenant/KB labels."""
    from types import SimpleNamespace
    from runtime.run import RetrievalSummary

    def count():
        return metrics.REGISTRY.get_sample_value("ac_retrieval_seconds_count", {"source": "eval"}) or 0.0

    def rec(retrieval):
        return SimpleNamespace(source="eval", latency_ms=10, first_token_ms=None, outcome="success", retrieval=retrieval)

    before = count()
    metrics.record_run_slis(rec(RetrievalSummary(retrieval_ms=120)))
    metrics.record_run_slis(rec(None))
    assert count() == before + 1
    body = metrics.render()[0].decode()
    assert "ac_retrieval_seconds_bucket" in body and "kb_id" not in body
