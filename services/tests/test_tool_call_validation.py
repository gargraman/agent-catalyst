"""M1 (RED) — F3 tool-call trust boundary (critique #3).

A hallucinated tool name or schema-violating args must be rejected (fed back to the model as an
error result), never executed.
"""
from __future__ import annotations

from runtime.providers.validation import (
    ToolCallOk,
    ToolCallRejected,
    validate_tool_call,
)

SCHEMAS = {
    "search_runbook": {
        "type": "object",
        "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
        "required": ["query"],
        "additionalProperties": False,
    },
    "close_incident": {"type": "object", "properties": {}, "additionalProperties": False},
}


def test_valid_call_ok():
    r = validate_tool_call("search_runbook", {"query": "failed logins", "limit": 5}, SCHEMAS)
    assert isinstance(r, ToolCallOk) and r.name == "search_runbook"


def test_hallucinated_tool_name_rejected():
    r = validate_tool_call("delete_everything", {}, SCHEMAS)
    assert isinstance(r, ToolCallRejected) and r.reason == "unknown_tool"
    assert set(r.available) == {"search_runbook", "close_incident"}


def test_missing_required_arg_rejected():
    r = validate_tool_call("search_runbook", {"limit": 5}, SCHEMAS)
    assert isinstance(r, ToolCallRejected) and r.reason == "bad_args"
    assert r.errors


def test_wrong_arg_type_rejected():
    r = validate_tool_call("search_runbook", {"query": "x", "limit": "five"}, SCHEMAS)
    assert isinstance(r, ToolCallRejected) and r.reason == "bad_args"


def test_unexpected_arg_rejected_when_additionalprops_false():
    r = validate_tool_call("close_incident", {"force": True}, SCHEMAS)
    assert isinstance(r, ToolCallRejected) and r.reason == "bad_args"


def test_tool_without_schema_accepts_any_args():
    r = validate_tool_call("freeform", {"whatever": 1}, {"freeform": {}})
    assert isinstance(r, ToolCallOk)
