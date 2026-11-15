"""Shared mocked test harness for the runtime invoke spine (M2a, feature 003, task T001).

No network, no api_key use (SC-008): tests monkeypatch `runtime.providers.gateway.stream` with one of the
canned async streams built here. Provides a VersionSnapshot factory, NormalizedStreamEvent builders, a
priced-USAGE builder matching the M1 gateway's USAGE contract, and a capturing RunSink.
"""
from __future__ import annotations

import asyncio
import copy
from decimal import Decimal

from runtime.context import RunContext
from runtime.providers.ir import (
    EventType,
    NormalizedStreamEvent,
    NormalizedUsage,
)
from runtime.providers.profiles import Substitution
from runtime.snapshot import ToolRef, VersionSnapshot

# A profile_snapshot in the on-disk profile JSON shape (what the M4 to_snapshot() will freeze), so the
# runtime can build the FROZEN CapabilityProfile from it via _profile_from_json (review finding F1).
PROFILE_SNAPSHOT = {
    "provider": "openai",
    "model": "gpt-5-mini",
    "api_version": "2026-01",
    "availability": "active",
    "sunset_at": None,
    "provisional": True,
    "pricing_usd_per_mtok": {
        "input": 0.6, "output": 2.4, "cached_input": 0.06,
        "cache_write": 0.75, "reasoning": 2.4, "batch_multiplier": 0.5,
    },
    "limits": {"context_tokens": 400000, "max_output_tokens": 128000},
    "features": {
        "structured_output": True, "forced_tool_choice": True,
        "reasoning_control": True, "prompt_caching": True,
    },
}


def make_snapshot(**overrides) -> VersionSnapshot:
    """A minimal valid frozen snapshot; override any field (e.g. profile_snapshot, io_contract)."""
    base = dict(
        version_id="ver_1",
        agent_id="agt_1",
        workspace_id="ws_1",
        label="v1",
        snapshot_hash="snap_hash_1",
        model_config={
            "provider": "openai", "model": "gpt-5-mini", "api_version": "2026-01",
            "params": {"temperature": 0.2, "max_tokens": 256},
        },
        profile_snapshot=copy.deepcopy(PROFILE_SNAPSHOT),
        system_prompt="You are a test agent.",
        prompt_hash="prompt_hash_1",
        io_contract={
            "input_schema": {
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
            "output_format": "text",
            "output_schema": None,
        },
        memory_config={},
        context_config={},
        guardrail_config={},
    )
    base.update(overrides)
    return VersionSnapshot(**base)


class CapturingSink:
    """A RunSink that records every RunRecord it receives (to assert exactly-one-per-invocation)."""

    def __init__(self) -> None:
        self.runs: list = []

    def __call__(self, run) -> None:
        self.runs.append(run)


def make_context(**overrides) -> RunContext:
    base = dict(
        workspace_id="ws_1", source="eval", api_key="sk-test-key",
        record_run=CapturingSink(), correlation_id=None, deadline_s=30.0, max_tool_calls=8,
    )
    base.update(overrides)
    return RunContext(**base)


# ---- NormalizedStreamEvent builders ----------------------------------------------------------
def text_ev(t: str) -> NormalizedStreamEvent:
    return NormalizedStreamEvent(EventType.TEXT_DELTA, {"text": t})


def reasoning_ev(t: str) -> NormalizedStreamEvent:
    return NormalizedStreamEvent(EventType.REASONING_DELTA, {"text": t})


def tool_start_ev(call_id: str, name: str) -> NormalizedStreamEvent:
    return NormalizedStreamEvent(EventType.TOOL_CALL_START, {"id": call_id, "name": name})


def tool_delta_ev(call_id: str, frag: str) -> NormalizedStreamEvent:
    return NormalizedStreamEvent(EventType.TOOL_CALL_DELTA, {"id": call_id, "args_delta": frag})


def tool_end_ev(call_id: str, name: str, args: dict) -> NormalizedStreamEvent:
    return NormalizedStreamEvent(EventType.TOOL_CALL_END, {"id": call_id, "name": name, "args": args})


def stop_ev(reason: str = "stop") -> NormalizedStreamEvent:
    return NormalizedStreamEvent(EventType.STOP, {"reason": reason})


def error_ev(error: dict) -> NormalizedStreamEvent:
    return NormalizedStreamEvent(EventType.ERROR, {"error": error})


def priced_usage(
    *, input=100, output=50, cached=0, cache_write=0, reasoning=0, cost_usd=0.000132
) -> NormalizedUsage:
    """A NormalizedUsage with cost already attached — as the M1 gateway hands it out on USAGE."""
    return NormalizedUsage(
        input_tokens=input, output_tokens=output, cached_input_tokens=cached,
        cache_write_tokens=cache_write, reasoning_tokens=reasoning, cost_usd=cost_usd,
    )


def usage_ev(usage: NormalizedUsage | None = None, *, subs=(), estimate=True) -> NormalizedStreamEvent:
    """The gateway's terminal USAGE event contract: {usage, substitutions, [cost_is_estimate]}."""
    data = {"usage": usage or priced_usage(), "substitutions": list(subs)}
    if estimate:
        data["cost_is_estimate"] = True
    return NormalizedStreamEvent(EventType.USAGE, data)


def make_gateway(events, *, per_event_delay: float = 0.0):
    """Build a drop-in replacement for gateway.stream yielding the given events (optionally slowly)."""
    async def _stream(req, *, api_key, profile=None, timeout=None, **_resilience):
        for ev in events:
            if per_event_delay:
                await asyncio.sleep(per_event_delay)
            yield ev
    return _stream


def make_multi_turn_gateway(turns, *, per_event_delay: float = 0.0):
    """A gateway.stream mock that returns a DIFFERENT event-list per call (M2b correction loop).

    `turns` is a list of event-lists; the Nth call to stream yields turns[N]. Calls beyond the list repeat
    the last turn. `calls` records how many times the gateway was invoked (assert correction-turn counts)."""
    state = {"n": 0}
    async def _stream(req, *, api_key, profile=None, timeout=None, **_resilience):
        i = min(state["n"], len(turns) - 1)
        state["n"] += 1
        for ev in turns[i]:
            if per_event_delay:
                await asyncio.sleep(per_event_delay)
            yield ev
    _stream.calls = state  # type: ignore[attr-defined]
    return _stream


JSON_IO = {
    "input_schema": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]},
    "output_format": "json",
    "output_schema": {"type": "object",
                      "properties": {"severity": {"type": "string"}, "score": {"type": "number"}},
                      "required": ["severity", "score"]},
}


def make_tool_ref(name: str | None, args_schema: dict | None = None, *, key: str = "input_schema") -> ToolRef:
    """Build a bound ToolRef with a provider-shaped schema_snapshot ({name, input_schema|parameters})."""
    snap: dict = {}
    if name is not None:
        snap["name"] = name
    if args_schema is not None:
        snap[key] = args_schema
    return ToolRef(kind="http", schema_snapshot=snap, config_snapshot={}, risk_tier="low",
                   requires_approval=False, subagent_config_id=None, auth_mode=None, mcp_scopes=(),
                   tool_id="t1", mcp_tool_id=None)


def collect(async_iter):
    """Drive an async iterator to a list synchronously (test convenience)."""
    async def _go():
        return [x async for x in async_iter]
    return asyncio.run(_go())
