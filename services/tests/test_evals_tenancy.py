"""P5/FR-023 — eval runs are workspace-scoped; cases/baseline/candidate from different workspaces are
rejected fail-closed BEFORE any provider call, never silently compared."""
from __future__ import annotations

import asyncio

import pytest

from runtime.providers import gateway

from evals.drift import EvalPolicy
from evals.runner import run_eval
from evals.types import TenancyError
from tests._evals_helpers import (
    boom_gateway,
    candidate_snapshot,
    make_baseline,
    make_context,
    make_eval_case,
    make_routed_gateway,
)


def _policy() -> EvalPolicy:
    return EvalPolicy(min_cases=1, quality_threshold=10.0, efficiency_budget_pct=10.0)


def test_cross_workspace_baseline_rejected_without_gateway_call(monkeypatch):
    monkeypatch.setattr(gateway, "stream", boom_gateway())
    cases = [make_eval_case(cid="c1", workspace_id="ws_1")]
    baseline = make_baseline(workspace_id="ws_2", per_case={"c1": "{}"})  # mismatched tenant
    snap = candidate_snapshot(workspace_id="ws_1")
    ctx = make_context(workspace_id="ws_1")
    with pytest.raises(TenancyError):
        asyncio.run(run_eval(cases, snap, baseline, _policy(), ctx, workspace_id="ws_1"))


def test_cross_workspace_case_rejected_without_gateway_call(monkeypatch):
    monkeypatch.setattr(gateway, "stream", boom_gateway())
    cases = [make_eval_case(cid="c1", workspace_id="ws_OTHER")]              # case from another tenant
    baseline = make_baseline(workspace_id="ws_1", per_case={"c1": "{}"})
    snap = candidate_snapshot(workspace_id="ws_1")
    ctx = make_context(workspace_id="ws_1")
    with pytest.raises(TenancyError):
        asyncio.run(run_eval(cases, snap, baseline, _policy(), ctx, workspace_id="ws_1"))


def test_cross_workspace_candidate_rejected_without_gateway_call(monkeypatch):
    monkeypatch.setattr(gateway, "stream", boom_gateway())
    cases = [make_eval_case(cid="c1", workspace_id="ws_1")]
    baseline = make_baseline(workspace_id="ws_1", per_case={"c1": "{}"})
    snap = candidate_snapshot(workspace_id="ws_ANOTHER")                    # candidate from another tenant
    ctx = make_context(workspace_id="ws_1")
    with pytest.raises(TenancyError):
        asyncio.run(run_eval(cases, snap, baseline, _policy(), ctx, workspace_id="ws_1"))


def test_matching_workspace_proceeds(monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_routed_gateway())
    cases = [make_eval_case(cid="c1", workspace_id="ws_1")]
    baseline = make_baseline(workspace_id="ws_1", per_case={"c1": "{}"})
    snap = candidate_snapshot(workspace_id="ws_1")
    ctx = make_context(workspace_id="ws_1")
    res = asyncio.run(run_eval(cases, snap, baseline, _policy(), ctx, workspace_id="ws_1"))
    assert res.workspace_id == "ws_1"
