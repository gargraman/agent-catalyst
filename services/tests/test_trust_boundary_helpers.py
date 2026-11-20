"""M2b foundational helpers (feature 007, T005): tool-schema extraction + correction-message building.

Pure-function unit tests (no gateway, no DB). RED until the helpers exist in runtime/io.py (T006)."""
from __future__ import annotations

from runtime.io import build_correction_messages, tool_schemas_from_snapshot
from runtime.providers.validation import RepairNeeded, ToolCallRejected
from runtime.snapshot import ToolRef, VersionSnapshot
from tests._invoke_helpers import make_snapshot


def _tool_ref(schema_snapshot: dict) -> ToolRef:
    return ToolRef(
        kind="http", schema_snapshot=schema_snapshot, config_snapshot={}, risk_tier="low",
        requires_approval=False, subagent_config_id=None, auth_mode=None, mcp_scopes=(),
        tool_id="t1", mcp_tool_id=None,
    )


# ---- tool_schemas_from_snapshot (D4) ---------------------------------------------------------

def test_tool_schemas_extracts_name_and_input_schema():
    snap = make_snapshot(tools=(_tool_ref({"name": "search_runbook",
                                           "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}}}),))
    schemas = tool_schemas_from_snapshot(snap)
    assert set(schemas) == {"search_runbook"}
    assert schemas["search_runbook"]["properties"]["q"]["type"] == "string"


def test_tool_schemas_tolerates_openai_parameters_key():
    snap = make_snapshot(tools=(_tool_ref({"name": "close_incident",
                                           "parameters": {"type": "object", "properties": {"id": {"type": "string"}}}}),))
    schemas = tool_schemas_from_snapshot(snap)
    assert "close_incident" in schemas and "id" in schemas["close_incident"]["properties"]


def test_tool_schemas_skips_unnamed_defs_and_defaults_empty_schema():
    snap = make_snapshot(tools=(
        _tool_ref({"type": "object", "properties": {}}),          # bare/unnamed → skipped
        _tool_ref({"name": "ping"}),                               # named, no schema → {} (accept any args)
    ))
    schemas = tool_schemas_from_snapshot(snap)
    assert set(schemas) == {"ping"} and schemas["ping"] == {}


def test_tool_schemas_empty_when_no_tools():
    assert tool_schemas_from_snapshot(make_snapshot()) == {}


# ---- build_correction_messages (D3) ----------------------------------------------------------

def test_build_correction_messages_output_repair_appends_plain_roles():
    base = [{"role": "user", "content": "do it"}]
    defects = {"output": RepairNeeded("invalid_json", "model output is not valid JSON", ()), "tools": []}
    out = build_correction_messages(base, "{not json", defects)
    assert out[0] == base[0]                                   # original preserved
    assert out[-2]["role"] == "assistant" and "{not json" in out[-2]["content"]
    assert out[-1]["role"] == "user" and "JSON" in out[-1]["content"]
    assert base == [{"role": "user", "content": "do it"}]      # original list not mutated


def test_build_correction_messages_tool_feedback_lists_available_tools():
    base = [{"role": "user", "content": "search"}]
    rej = ToolCallRejected("unknown_tool", "model called an unbound tool: 'nope'",
                           available=("search_runbook", "close_incident"))
    defects = {"output": None, "tools": [({"id": "c1", "name": "nope", "args": {}}, rej)]}
    out = build_correction_messages(base, "", defects)
    user_msg = out[-1]
    assert user_msg["role"] == "user"
    assert "nope" in user_msg["content"] and "search_runbook" in user_msg["content"]


def test_build_correction_messages_bad_args_names_real_tools_not_none():
    """code-review F1: a bad_args rejection (available=()) must still name the bound tools + the schema errors,
    never 'Available tools: (none)'."""
    base = [{"role": "user", "content": "search"}]
    rej = ToolCallRejected("bad_args", "arguments for 'search' do not match its schema",
                           errors=("limit: 'five' is not of type 'number'",))
    defects = {"output": None, "tools": [({"id": "c1", "name": "search", "args": {"limit": "five"}}, rej)]}
    out = build_correction_messages(base, "", defects, available_tools=("search", "close_incident"))
    msg = out[-1]["content"]
    assert "(none)" not in msg
    assert "search" in msg and "close_incident" in msg
    assert "not of type 'number'" in msg                      # schema errors surfaced for self-correction
