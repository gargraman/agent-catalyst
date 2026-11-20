"""SSE re-encoding of runtime normalized events (M4, feature 005; finding F9).

The runtime yields `NormalizedStreamEvent`s (the IR, 02); this maps each to an SSE frame
(`event: <type>\\ndata: <json>\\n\\n`). `run_start`/`done` are synthetic (built by the invoke router).
The `error` frame carries ONLY `{code, message}` — never a raw SDK type, stack, or api_key (P8/P5).
Pure: imports the runtime IR enum only (keeps `runtime` web-free; the dependency is one-way).
"""
from __future__ import annotations

import json

from runtime.providers.ir import EventType

_EVENT_NAME = {
    EventType.TEXT_DELTA: "text_delta",
    EventType.REASONING_DELTA: "reasoning_delta",
    EventType.TOOL_CALL_START: "tool_call_start",
    EventType.TOOL_CALL_DELTA: "tool_call_delta",
    EventType.TOOL_CALL_END: "tool_call_end",
    EventType.USAGE: "usage",
    EventType.ERROR: "error",
    EventType.CITATIONS: "citations",   # feature 024 — the router shapes it per plane (api.retrieval_wiring)
}


def frame(event_name: str, data: dict) -> bytes:
    """Encode one SSE frame. `default=str` keeps Decimal/UUID JSON-safe."""
    return f"event: {event_name}\ndata: {json.dumps(data, default=str)}\n\n".encode()


def _payload(ev) -> dict:
    t = ev.type
    if t in (EventType.TEXT_DELTA, EventType.REASONING_DELTA):
        return {"text": ev.data.get("text", "")}
    if t in (EventType.TOOL_CALL_START, EventType.TOOL_CALL_DELTA, EventType.TOOL_CALL_END):
        return {k: ev.data[k] for k in ("id", "name", "args", "args_delta") if k in ev.data}
    if t is EventType.USAGE:
        usage = ev.data.get("usage")
        cost = getattr(usage, "cost_usd", None)
        return {
            "input_tokens": getattr(usage, "input_tokens", 0),
            "output_tokens": getattr(usage, "output_tokens", 0),
            "cost_usd": float(cost) if cost is not None else None,
            "cost_is_estimate": bool(ev.data.get("cost_is_estimate", False)),
        }
    if t is EventType.CITATIONS:
        return {"knowledge_used": bool(ev.data.get("knowledge_used")), "citations": list(ev.data.get("citations", ()))}
    if t is EventType.ERROR:
        err = ev.data.get("error") or {}
        return {"code": err.get("type", "error"), "message": err.get("message", "")}  # scrubbed
    return {}


def encode_event(ev) -> bytes | None:
    """Encode a runtime event to an SSE frame, or None for events with no own frame (e.g. STOP)."""
    name = _EVENT_NAME.get(ev.type)
    if name is None:
        return None
    return frame(name, _payload(ev))


def error_frame(code: str, message: str) -> bytes:
    return frame("error", {"code": code, "message": message})
