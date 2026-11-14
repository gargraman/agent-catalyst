"""Feature 023 US3 (T030): first-token / run-latency histograms + deadline counter (FR-015..018).

Pure: gateway mocked for the runtime part; metrics read from the custom registry as BEFORE/AFTER deltas.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from api import metrics
from runtime.invoke import invoke_result
from runtime.providers import gateway
from runtime.run import Outcome
from tests._invoke_helpers import (
    JSON_IO, make_context, make_gateway, make_snapshot, stop_ev, text_ev, usage_ev,
)

INPUTS = {"message": "go"}


def _delayed_gateway(delay: float):
    async def _stream(req, *, api_key, profile=None, timeout=None):
        await asyncio.sleep(delay)
        yield text_ev("hi")
        yield usage_ev()
        yield stop_ev()
    return _stream


# ---- runtime: first_token_ms ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_pass_through_records_first_token(monkeypatch):
    monkeypatch.setattr(gateway, "stream", _delayed_gateway(0.12))
    recs: list = []
    res = await invoke_result(make_snapshot(), INPUTS, make_context(record_call=recs.append))
    assert res.run.first_token_ms is not None and 100 <= res.run.first_token_ms < 1000
    assert recs[0].first_token_ms == res.run.first_token_ms


@pytest.mark.asyncio
async def test_validated_mode_has_no_first_token(monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev('{"severity": "low", "score": 1}'), usage_ev(), stop_ev()]))
    res = await invoke_result(make_snapshot(io_contract=JSON_IO), INPUTS, make_context())
    assert res.run.first_token_ms is None


# ---- metrics ----------------------------------------------------------------------------------------------
def _v(name, labels):
    return metrics.REGISTRY.get_sample_value(name, labels) or 0.0


def _rec(outcome=Outcome.SUCCESS, source="playground", latency_ms=1200, first_token_ms=None):
    from runtime.run import RunRecord
    return RunRecord(run_id="r", correlation_id="c", workspace_id="w", agent_id="a", version_id="v",
                     version_label="v1", provider="openai", model="m", profile_hash="p", prompt_hash="ph",
                     snapshot_hash="s", source=source, outcome=outcome, latency_ms=latency_ms,
                     first_token_ms=first_token_ms)


def test_first_token_histogram_observed_only_when_set():
    src = "sli-ft"
    before = _v("ac_first_token_seconds_count", {"source": src})
    b15 = _v("ac_first_token_seconds_bucket", {"source": src, "le": "1.5"})
    metrics.record_run_slis(_rec(source=src, first_token_ms=1200))
    metrics.record_run_slis(_rec(source=src, first_token_ms=None))
    assert _v("ac_first_token_seconds_count", {"source": src}) == before + 1
    assert _v("ac_first_token_seconds_bucket", {"source": src, "le": "1.5"}) == b15 + 1
    assert _v("ac_first_token_seconds_bucket", {"source": src, "le": "1.0"}) == 0


def test_run_latency_observed_every_run():
    src = "sli-lat"
    before = _v("ac_run_latency_seconds_count", {"source": src})
    for o in (Outcome.SUCCESS, Outcome.PROVIDER_ERROR, Outcome.TIMEOUT):
        metrics.record_run_slis(_rec(outcome=o, source=src))
    assert _v("ac_run_latency_seconds_count", {"source": src}) == before + 3


def test_deadline_counter_matches_timeout_runs():
    src = "sli-dl"
    d0 = _v("ac_deadline_exceeded_total", {"source": src})
    t0 = _v("ac_runs_total", {"status": "timeout", "source": src})
    batch = [Outcome.SUCCESS, Outcome.TIMEOUT, Outcome.PROVIDER_ERROR, Outcome.TIMEOUT, Outcome.DEGRADED]
    for o in batch:
        rec = _rec(outcome=o, source=src)
        metrics.record_run(o.value, src)
        metrics.record_run_slis(rec)
    d = _v("ac_deadline_exceeded_total", {"source": src}) - d0
    t = _v("ac_runs_total", {"status": "timeout", "source": src}) - t0
    assert d == t == 2


def test_bucket_layouts():
    metrics.record_run_slis(_rec(source="sli-buckets", first_token_ms=5))   # ensure a labeled child exists
    ft = sorted({float(s.labels["le"]) for m in metrics.REGISTRY.collect() if m.name == "ac_first_token_seconds"
                 for s in m.samples if s.name.endswith("_bucket") and s.labels["le"] != "+Inf"})
    lat = sorted({float(s.labels["le"]) for m in metrics.REGISTRY.collect() if m.name == "ac_run_latency_seconds"
                  for s in m.samples if s.name.endswith("_bucket") and s.labels["le"] != "+Inf"})
    assert ft == [0.25, 0.5, 1, 1.5, 2, 3, 5, 8, 13, 30]
    assert lat == [0.5, 1, 2, 5, 10, 15, 20, 25, 30, 45]


def test_record_run_slis_is_best_effort(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(metrics.ac_run_latency_seconds, "labels", boom)
    metrics.record_run_slis(_rec())          # must not raise


def test_exposition_has_no_tenant_or_content_labels():
    metrics.record_run_slis(replace(_rec(source="sli-priv", first_token_ms=10), workspace_id="ws-PRIVATE-ID"))
    text = metrics.render()[0].decode()
    assert "ws-PRIVATE-ID" not in text
    for line in text.splitlines():
        if line.startswith(("ac_first_token_seconds", "ac_run_latency_seconds", "ac_deadline_exceeded_total")):
            assert "workspace" not in line and "agent" not in line and "user" not in line
