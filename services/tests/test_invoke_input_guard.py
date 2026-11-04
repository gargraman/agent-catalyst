"""US5 — invalid/oversized inputs caught before any provider call (M2a). Mocked; no live calls (SC-008)."""
from __future__ import annotations

import asyncio

from runtime import invoke as invoke_mod
from runtime.providers import gateway
from runtime.run import Outcome
from tests._invoke_helpers import make_context, make_gateway, make_snapshot, text_ev, usage_ev


def _boom_gateway():
    def boom(*a, **k):
        raise AssertionError("gateway.stream must not be called on a pre-call rejection")
    return boom


def test_invalid_input_fails_closed_without_gateway_call(monkeypatch):
    # US5/AC1 (FR-004, SC-005): inputs violating io_contract.input_schema → invalid_input, no provider call.
    monkeypatch.setattr(gateway, "stream", _boom_gateway())
    snap = make_snapshot()  # input_schema requires "message": string
    ctx = make_context()
    res = asyncio.run(invoke_mod.invoke_result(snap, {"wrong_field": 123}, ctx))
    assert res.run.outcome is Outcome.INVALID_INPUT
    assert res.run.cost_usd is None and res.run.usage is None
    assert len(ctx.record_run.runs) == 1


def test_context_overflow_fails_closed_without_gateway_call(monkeypatch):
    # US5/AC2 (FR-005, SC-005): inputs over the model context budget → context_overflow, no provider call.
    monkeypatch.setattr(gateway, "stream", _boom_gateway())
    snap = make_snapshot()
    # shrink the frozen profile's context budget so a modest message overflows it
    snap.profile_snapshot["limits"]["context_tokens"] = 5
    ctx = make_context()
    res = asyncio.run(invoke_mod.invoke_result(snap, {"message": "this message is clearly longer than five tokens worth of text"}, ctx))
    assert res.run.outcome is Outcome.CONTEXT_OVERFLOW
    assert res.run.cost_usd is None
    assert len(ctx.record_run.runs) == 1


def test_valid_input_within_budget_proceeds(monkeypatch):
    # Guard does not false-positive on valid, in-budget input.
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("ok"), usage_ev()]))
    snap = make_snapshot()
    ctx = make_context()
    res = asyncio.run(invoke_mod.invoke_result(snap, {"message": "hi"}, ctx))
    assert res.run.outcome is Outcome.SUCCESS


def test_empty_workspace_fails_closed_without_gateway_call(monkeypatch):
    # C1 (converge) / P5 + data-model (`RunContext.workspace_id` MUST be non-empty — fail closed): an
    # invocation with no workspace on EITHER the context or the snapshot must fail closed BEFORE any
    # provider call, never silently emitting an untenanted Run.
    monkeypatch.setattr(gateway, "stream", _boom_gateway())
    snap = make_snapshot(workspace_id="")      # nothing to fall back to
    ctx = make_context(workspace_id="")
    res = asyncio.run(invoke_mod.invoke_result(snap, {"message": "hi"}, ctx))
    assert res.run.outcome is Outcome.CONFIG_ERROR
    assert res.run.usage is None and res.run.cost_usd is None   # zero provider interaction
    assert len(ctx.record_run.runs) == 1


def test_workspace_falls_back_to_snapshot_when_ctx_empty(monkeypatch):
    # The guard rejects ONLY when both are empty; a ctx without a workspace still runs under the snapshot's
    # workspace (existing `ctx.workspace_id or snapshot.workspace_id` behavior preserved).
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("ok"), usage_ev()]))
    snap = make_snapshot(workspace_id="ws_snap")
    ctx = make_context(workspace_id="")
    res = asyncio.run(invoke_mod.invoke_result(snap, {"message": "hi"}, ctx))
    assert res.run.outcome is Outcome.SUCCESS
    assert res.run.workspace_id == "ws_snap"
