"""Input validation + request assembly for the invoke spine (M2a, feature 003).

`build_normalized_request` maps a frozen VersionSnapshot + caller inputs onto the M1 NormalizedRequest
(data-model.md). `validate_input` (US5) checks inputs against the snapshot's input contract. Reuses the
M1 schema-error helper so the validation dialect matches the rest of the platform.
"""
from __future__ import annotations

from runtime.providers.ir import NormalizedRequest
from runtime.providers.validation import _schema_errors

# C1 (analyze finding): the inputs→messages contract is pinned here. `inputs` is a dict validated against
# io_contract.input_schema; messages are derived by preferring an explicit "messages" list, else wrapping a
# "message" string as a single user turn. A bare list is accepted as messages as-is.


def inputs_to_messages(inputs) -> list[dict]:
    if isinstance(inputs, dict):
        msgs = inputs.get("messages")
        if isinstance(msgs, list):
            return list(msgs)
        if isinstance(inputs.get("message"), str):
            return [{"role": "user", "content": inputs["message"]}]
    if isinstance(inputs, list):
        return list(inputs)
    raise ValueError("inputs must be a dict with 'messages' (list) or 'message' (str), or a messages list")


def validate_input(inputs, io_contract: dict) -> list[str]:
    """Return human-readable schema errors (empty == valid) for inputs vs io_contract.input_schema.
    No schema declared → no errors (permissive). (FR-004)"""
    schema = (io_contract or {}).get("input_schema")
    if not schema:
        return []
    return _schema_errors(schema, inputs)


def build_normalized_request(snapshot, inputs) -> NormalizedRequest:
    """Assemble the M1 NormalizedRequest from the frozen snapshot + validated inputs (FR-001 assembly)."""
    mc = snapshot.model_config or {}
    io = snapshot.io_contract or {}

    response_format = None
    if io.get("output_format") == "json":
        schema = io.get("output_schema")
        response_format = {"json_schema": schema} if schema else {"type": "json_object"}

    tools = [t.schema_snapshot for t in snapshot.tools] if snapshot.tools else []
    cache_hints = (snapshot.context_config or {}).get("cache_hints", []) or []

    return NormalizedRequest(
        provider=mc["provider"],
        model=mc["model"],
        api_version=mc.get("api_version"),
        messages=inputs_to_messages(inputs),
        system=snapshot.system_prompt or None,
        params=dict(mc.get("params", {})),
        tools=tools,
        tool_choice=mc.get("tool_choice"),
        response_format=response_format,
        reasoning=mc.get("reasoning"),
        cache_hints=cache_hints,
    )
