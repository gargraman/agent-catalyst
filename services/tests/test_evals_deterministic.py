"""US1 — Layer-1 deterministic checks (M3, feature 004). Ported from mvp/tests/test_deterministic.py
(verbatim behavior) + determinism (F18/SC-004) and an EvalCase-path test (FR-006). Zero model calls."""
from __future__ import annotations

import json

from evals.deterministic import run_deterministic
from evals.types import EvalCase

_VALID = {"severity": "high", "summary": "DB CPU at 98% for 20m", "next_step": "page the on-call DBA"}


def test_clean_valid_output_passes():
    r = run_deterministic(json.dumps(_VALID), {})
    assert r.passed is True
    assert r.hard_fail is False
    assert r.checks["schema_valid"] is True
    assert r.checks["required_fields"] is True
    assert r.checks["severity_enum"] is True


def test_valid_dict_output_passes():
    r = run_deterministic(_VALID, {})
    assert r.passed is True
    assert r.hard_fail is False


def test_schema_invalid_is_hard_fail():
    r = run_deterministic("not json {", {})
    assert r.passed is False
    assert r.hard_fail is True
    assert r.checks["schema_valid"] is False


def test_non_object_json_is_hard_fail():
    r = run_deterministic("[1, 2, 3]", {})
    assert r.passed is False
    assert r.hard_fail is True
    assert r.checks["schema_valid"] is False


def test_missing_required_field_soft_fails():
    partial = {"severity": "high", "summary": "something"}  # no next_step
    r = run_deterministic(json.dumps(partial), {})
    assert r.passed is False
    assert r.hard_fail is False
    assert r.checks["required_fields"] is False


def test_empty_required_field_soft_fails():
    partial = {"severity": "high", "summary": "", "next_step": "x"}
    r = run_deterministic(json.dumps(partial), {})
    assert r.passed is False
    assert r.hard_fail is False
    assert r.checks["required_fields"] is False


def test_bad_severity_enum_soft_fails():
    bad = {"severity": "urgent", "summary": "x", "next_step": "y"}
    r = run_deterministic(json.dumps(bad), {})
    assert r.passed is False
    assert r.hard_fail is False
    assert r.checks["severity_enum"] is False


def test_refusal_is_hard_fail():
    r = run_deterministic("I'm sorry, I cannot help with that request.", {})
    assert r.passed is False
    assert r.hard_fail is True
    assert r.checks["no_refusal"] is False


def test_first_person_unable_is_refusal():
    r = run_deterministic("I'm unable to assist with that.", {})
    assert r.passed is False
    assert r.hard_fail is True
    assert r.checks["no_refusal"] is False


def test_incidental_unable_to_in_valid_json_is_not_refusal():
    """FR-003 guard: valid incident JSON whose prose contains the bare words "unable to" must NOT be flagged
    as a refusal — only first-person refusals ("i'm unable to") count."""
    out = {"severity": "high", "summary": "The checkout service was unable to start after the deploy.",
           "next_step": "roll back the deploy"}
    r = run_deterministic(json.dumps(out), {})
    assert r.passed is True
    assert r.hard_fail is False
    assert r.checks["no_refusal"] is True


def test_empty_output_is_hard_fail():
    r = run_deterministic("   ", {})
    assert r.passed is False
    assert r.hard_fail is True
    assert r.checks["non_empty"] is False


def test_empty_dict_is_hard_fail():
    r = run_deterministic({}, {})
    assert r.passed is False
    assert r.hard_fail is True


def test_expected_severity_match():
    r = run_deterministic(json.dumps(_VALID), {"expected": "high"})
    assert r.passed is True
    assert r.checks["expected_match"] is True


def test_expected_severity_mismatch_soft_fails():
    r = run_deterministic(json.dumps(_VALID), {"expected": "critical"})
    assert r.passed is False
    assert r.hard_fail is False
    assert r.checks["expected_match"] is False


def test_custom_required_fields_override():
    out = {"severity": "low", "summary": "x"}  # no next_step, but case only requires these two
    r = run_deterministic(json.dumps(out), {"required_fields": ["severity", "summary"]})
    assert r.passed is True


# ---- M3 additions ----------------------------------------------------------------------------

def test_determinism_same_input_twice_identical():
    """F18/SC-004/FR-022 — identical (output, case) → identical DetResult across repeated runs."""
    out = json.dumps(_VALID)
    case = {"expected": "high"}
    r1 = run_deterministic(out, case)
    r2 = run_deterministic(out, case)
    assert r1 == r2
    # and on a soft-fail path too
    bad = json.dumps({"severity": "urgent", "summary": "x", "next_step": "y"})
    assert run_deterministic(bad, {}) == run_deterministic(bad, {})


def test_evalcase_det_spec_drives_required_fields_override():
    """FR-006 via the EvalCase dataclass path: required_fields override flows through det_spec()."""
    case = EvalCase(id="c1", input="x", workspace_id="ws_1", required_fields=("severity", "summary"))
    out = {"severity": "low", "summary": "x"}  # no next_step
    r = run_deterministic(out, case.det_spec())
    assert r.passed is True
    assert r.checks["required_fields"] is True
