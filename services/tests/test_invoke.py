"""US1 — one shared execution path runs a frozen snapshot (M2a). Mocked gateway; no live calls (SC-008)."""
from __future__ import annotations

import asyncio

from runtime import invoke as invoke_mod
from runtime.providers import gateway
from tests._invoke_helpers import (
    collect,
    make_context,
    make_gateway,
    make_snapshot,
    text_ev,
    usage_ev,
)


def test_invoke_streams_content_events(monkeypatch):
    # US1/AC1: invoke() re-surfaces the gateway's content events.
    snap = make_snapshot()
    ctx = make_context()
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("Hello"), text_ev(" world"), usage_ev()]))
    events = collect(invoke_mod.invoke(snap, {"message": "hi"}, ctx))
    kinds = [e.type.value for e in events]
    assert kinds.count("text_delta") == 2
    assert "usage" in kinds


def test_invoke_result_accumulates_text(monkeypatch):
    # US1/AC1: invoke_result() collects streamed content into a result.
    snap = make_snapshot()
    ctx = make_context()
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("Hello"), text_ev(" world"), usage_ev()]))
    res = asyncio.run(invoke_mod.invoke_result(snap, {"message": "hi"}, ctx))
    assert res.text == "Hello world"


def test_invoke_is_snapshot_driven(monkeypatch):
    # US1/AC2 (FR-002): behavior + Run identity come from the frozen snapshot, not any live lookup.
    snap = make_snapshot(version_id="ver_X", snapshot_hash="snap_X")
    ctx = make_context()
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("ok"), usage_ev()]))
    res = asyncio.run(invoke_mod.invoke_result(snap, {"message": "hi"}, ctx))
    assert res.run.version_id == "ver_X"
    assert res.run.snapshot_hash == "snap_X"
    assert res.run.model == "gpt-5-mini"


def test_invoke_path_is_source_agnostic(monkeypatch):
    # US1/AC3 (FR-003): no caller-specific branch — same events + text for any source; only Run.source differs.
    snap = make_snapshot()
    results = {}
    for src in ("playground", "eval"):
        ctx = make_context(source=src)
        monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("same"), usage_ev()]))
        res = asyncio.run(invoke_mod.invoke_result(snap, {"message": "hi"}, ctx))
        results[src] = res
    assert results["playground"].text == results["eval"].text == "same"
    assert results["playground"].run.source == "playground"
    assert results["eval"].run.source == "eval"
