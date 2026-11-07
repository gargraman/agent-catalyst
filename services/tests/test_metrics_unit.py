"""Feature 010 (M6) — pure metrics helpers (T006). RED until api.metrics exists.

Counters are process-global and persist across the session, so assert BEFORE/AFTER deltas, never absolute.
"""
from __future__ import annotations

import pytest

from api import metrics


def _runs(status, source):
    return metrics.REGISTRY.get_sample_value("ac_runs_total", {"status": status, "source": source}) or 0.0


def _refused(code):
    return metrics.REGISTRY.get_sample_value("ac_requests_refused_total", {"code": code}) or 0.0


def test_record_run_increments_by_one():
    before = _runs("success", "playground")
    metrics.record_run("success", "playground")
    assert _runs("success", "playground") == before + 1


def test_record_refusal_increments_by_one():
    before = _refused("budget.exceeded")
    metrics.record_refusal("budget.exceeded")
    assert _refused("budget.exceeded") == before + 1


def test_record_run_is_best_effort(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("metrics backend down")
    monkeypatch.setattr(metrics.ac_runs_total, "labels", _boom)
    metrics.record_run("success", "eval")   # must NOT raise


def test_record_refusal_is_best_effort(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(metrics.ac_requests_refused_total, "labels", _boom)
    metrics.record_refusal("access.denied")  # must NOT raise


def test_render_returns_prometheus_content_type():
    body, content_type = metrics.render()
    assert isinstance(body, bytes)
    assert "text/plain" in content_type and "version=" in content_type
