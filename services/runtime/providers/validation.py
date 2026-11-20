"""Defensive AI boundaries (critique F1/F2/F3) — net-new contracts the design lacked.

The gateway's transport layer (retry/breaker/fallback) handles 429/5xx/timeout, but nothing in the
design validated what the model actually *returns*. These are the semantic trust boundary:

  - F1/F2 validate_output: a published `output_format=json` agent must not hand malformed or
    schema-violating JSON to a caller. Parse + validate against the declared io_contract schema;
    on failure return RepairNeeded so the runtime (M2) can do one bounded repair re-ask. The
    EMULATED structured-output path (model lacks native support → JSON coaxed via prompt) is the
    highest-risk case and is validated here post-hoc, exactly like the native path.
  - F3 validate_tool_call: a hallucinated tool name or schema-violating args must be rejected and
    fed back to the model as an error result — never executed.

Contracts (the result dataclasses) are defined here; the functions are stubbed until GREEN.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from jsonschema import Draft202012Validator


def _schema_errors(schema: dict, instance) -> list[str]:
    """Collect human-readable schema errors (empty list == valid), path-prefixed where applicable."""
    validator = Draft202012Validator(schema)
    out: list[str] = []
    for e in sorted(validator.iter_errors(instance), key=lambda e: list(e.path)):
        loc = "/".join(str(p) for p in e.path)
        out.append(f"{loc}: {e.message}" if loc else e.message)
    return out


# ---- F1/F2: structured-output validation -----------------------------------------------------
@dataclass(frozen=True)
class OutputValid:
    text: str
    structured: dict | None  # parsed+validated object in json mode; None in text mode


@dataclass(frozen=True)
class RepairNeeded:
    reason: str              # "invalid_json" | "schema_violation"
    detail: str
    errors: tuple[str, ...] = ()


def validate_output(raw_text: str, *, output_format: str, output_schema: dict | None) -> OutputValid | RepairNeeded:
    """Validate a model's final message against the version's io_contract output slot.
    text mode → pass through; json mode → parse + (if a schema is declared) validate."""
    if output_format != "json":
        return OutputValid(text=raw_text, structured=None)
    try:
        obj = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError) as exc:
        return RepairNeeded("invalid_json", f"model output is not valid JSON: {exc}")
    if output_schema:
        errs = _schema_errors(output_schema, obj)
        if errs:
            return RepairNeeded(
                "schema_violation", "output does not match the declared schema", tuple(errs)
            )
    return OutputValid(text=raw_text, structured=obj)


# ---- F3: tool-call validation ----------------------------------------------------------------
@dataclass(frozen=True)
class ToolCallOk:
    name: str
    args: dict


@dataclass(frozen=True)
class ToolCallRejected:
    reason: str              # "unknown_tool" | "bad_args"
    detail: str
    available: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


def validate_tool_call(name: str, args: dict, tool_schemas: dict[str, dict]) -> ToolCallOk | ToolCallRejected:
    """`tool_schemas` maps each BOUND tool name → its frozen JSON-schema (from the VersionSnapshot's
    tool_bindings schema_snapshot). Reject an unbound name or args that fail the schema."""
    if name not in tool_schemas:
        return ToolCallRejected(
            "unknown_tool",
            f"model called an unbound tool: {name!r}",
            available=tuple(sorted(tool_schemas)),
        )
    schema = tool_schemas[name]
    if schema:
        errs = _schema_errors(schema, args)
        if errs:
            return ToolCallRejected(
                "bad_args", f"arguments for {name!r} do not match its schema", errors=tuple(errs)
            )
    return ToolCallOk(name=name, args=args)
