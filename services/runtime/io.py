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


def tool_schemas_from_snapshot(snapshot) -> dict[str, dict]:
    """Map each BOUND tool's name → its argument JSON-schema, for `validate_tool_call` (M2b F3, research D4).

    Each `ToolRef.schema_snapshot` is a provider-shaped tool def; we tolerate both the Anthropic (`input_schema`)
    and OpenAI (`parameters`) dialects. Defs without a usable name are skipped (the model can only legitimately
    call a tool it was given by name); a named def with no schema maps to `{}` (accept any args), matching the
    existing `validate_tool_call` empty-schema behavior."""
    out: dict[str, dict] = {}
    for t in (snapshot.tools or ()):
        d = t.schema_snapshot or {}
        name = d.get("name")
        if not isinstance(name, str) or not name:
            continue
        out[name] = d.get("input_schema") or d.get("parameters") or {}
    return out


def build_correction_messages(messages: list[dict], assistant_text: str, defects: dict,
                              available_tools: tuple[str, ...] = ()) -> list[dict]:
    """Return a NEW messages list (original + one bounded correction turn) using plain normalized roles (M2b D3).

    `defects` = {"output": RepairNeeded|None, "tools": [(tool_call_dict, ToolCallRejected), ...]}. Tool-call
    feedback takes precedence over output repair (FR-014 precedence). `available_tools` is the full set of bound
    tool names (caller-supplied) so the feedback names the real tools even on a `bad_args` rejection (which does
    not populate `ToolCallRejected.available`). No provider-native tool-result messages — the runtime does not
    execute tools (P2); plain user-role feedback is provider-agnostic and sufficient. The original is not mutated."""
    out = list(messages)
    tool_defects = defects.get("tools") or []
    output_defect = defects.get("output")
    if tool_defects:
        attempts = ", ".join(f"{tc.get('name')}({tc.get('args')})" for tc, _ in tool_defects)
        reasons = "; ".join(r.detail for _, r in tool_defects)
        schema_errs = "; ".join(e for _, r in tool_defects for e in (r.errors or ()))
        # union the bound-tool set with any per-rejection availability so the list is accurate for bad_args too
        available = sorted(set(available_tools) | {n for _, r in tool_defects for n in (r.available or ())})
        avail_str = ", ".join(available) if available else "(none)"
        msg = f"That tool call was invalid: {reasons}."
        if schema_errs:
            msg += f" Argument errors: {schema_errs}."
        msg += f" Available tools: {avail_str}. Call a valid tool or answer directly."
        out.append({"role": "assistant", "content": f"Attempted tool call(s): {attempts}"})
        out.append({"role": "user", "content": msg})
    elif output_defect is not None:
        errs = "; ".join(output_defect.errors) if output_defect.errors else output_defect.detail
        out.append({"role": "assistant", "content": assistant_text})
        out.append({"role": "user", "content": (
            f"Your previous response was invalid ({output_defect.reason}): {errs}. "
            "Return ONLY valid JSON matching the required schema.")})
    return out


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



# ---- feature 024: retrieved-context placement (research D13) ---------------------------------------------------
def latest_user_text(messages) -> str:
    """Text of the latest user message — the always-on retrieval query. "" when there is none."""
    for m in reversed(list(messages or ())):
        if isinstance(m, dict) and m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                return " ".join(p.get("text", "") for p in c if isinstance(p, dict) and isinstance(p.get("text"), str))
    return ""


def insert_context_message(messages, rendered: str) -> list:
    """Insert the rendered retrieved context as a user-role message immediately BEFORE the latest user message (the
    system prompt is never touched, so retrieved data can't masquerade as system instructions). No-op when empty."""
    msgs = list(messages or ())
    if not rendered:
        return msgs
    idx = next((i for i in range(len(msgs) - 1, -1, -1)
                if isinstance(msgs[i], dict) and msgs[i].get("role") == "user"), len(msgs))
    return msgs[:idx] + [{"role": "user", "content": rendered}] + msgs[idx:]
