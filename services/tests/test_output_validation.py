"""M1 (RED) — F1/F2 structured-output trust boundary (critique #1/#2)."""
from __future__ import annotations

from runtime.providers.validation import OutputValid, RepairNeeded, validate_output

SCHEMA = {
    "type": "object",
    "properties": {"severity": {"type": "string"}, "score": {"type": "number"}},
    "required": ["severity", "score"],
    "additionalProperties": False,
}


def test_text_mode_passes_through():
    r = validate_output("just prose", output_format="text", output_schema=None)
    assert isinstance(r, OutputValid)
    assert r.text == "just prose" and r.structured is None


def test_json_mode_valid_parses_structured():
    r = validate_output('{"severity":"high","score":8}', output_format="json", output_schema=SCHEMA)
    assert isinstance(r, OutputValid)
    assert r.structured == {"severity": "high", "score": 8}


def test_json_mode_malformed_needs_repair():
    r = validate_output("{not json", output_format="json", output_schema=SCHEMA)
    assert isinstance(r, RepairNeeded) and r.reason == "invalid_json"


def test_json_mode_schema_violation_needs_repair():
    # missing required "score" + wrong type would both be schema violations
    r = validate_output('{"severity":"high"}', output_format="json", output_schema=SCHEMA)
    assert isinstance(r, RepairNeeded) and r.reason == "schema_violation"
    assert r.errors  # carries the specific schema errors


def test_json_mode_wrong_type_needs_repair():
    r = validate_output('{"severity":"high","score":"eight"}', output_format="json", output_schema=SCHEMA)
    assert isinstance(r, RepairNeeded) and r.reason == "schema_violation"


def test_json_mode_without_schema_accepts_any_valid_json():
    r = validate_output('{"anything":true}', output_format="json", output_schema=None)
    assert isinstance(r, OutputValid) and r.structured == {"anything": True}


def test_emulated_output_is_validated_post_hoc():
    """A model lacking native structured output coaxed to emit JSON is validated the SAME way —
    emulation does not bypass the trust boundary (critique F2)."""
    emulated = 'Sure! Here is the JSON:\n{"severity":"high"}'  # chatter before the JSON → not pure JSON
    r = validate_output(emulated, output_format="json", output_schema=SCHEMA)
    assert isinstance(r, RepairNeeded) and r.reason == "invalid_json"
