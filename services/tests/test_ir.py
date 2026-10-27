"""M1 (RED) — the provider-gateway IR contract (02 §2)."""
from __future__ import annotations

import dataclasses

import pytest

from runtime.providers.ir import (
    EventType,
    NormalizedRequest,
    NormalizedStreamEvent,
    NormalizedUsage,
)


def test_request_is_frozen_with_defaults():
    r = NormalizedRequest(provider="anthropic", model="m", api_version=None, messages=[], system=None, params={})
    assert r.tools == [] and r.stream is True and r.tool_choice is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.model = "other"  # type: ignore[misc]


def test_event_type_values():
    assert EventType.TEXT_DELTA == "text_delta"
    assert EventType.USAGE == "usage"
    assert {e.value for e in EventType} == {
        "text_delta", "reasoning_delta", "tool_call_start", "tool_call_delta",
        "tool_call_end", "usage", "stop", "error",
    }


def test_stream_event_carries_typed_payload():
    ev = NormalizedStreamEvent(type=EventType.TEXT_DELTA, data={"text": "hi"})
    assert ev.type is EventType.TEXT_DELTA and ev.data["text"] == "hi"


def test_usage_defaults_zero():
    u = NormalizedUsage(input_tokens=10, output_tokens=5)
    assert (u.cached_input_tokens, u.cache_write_tokens, u.reasoning_tokens, u.cost_usd) == (0, 0, 0, 0.0)
