"""Layer-1 deterministic checks (05:45-61)."""

import json

from evals.deterministic import run_deterministic

_VALID = {"severity": "high", "summary": "DB CPU at 98% for 20m", "next_step": "page the on-call DBA"}


def test_clean_valid_output_passes():
    r = run_deterministic(json.dumps(_VALID), {})
    assert r.passed is True
    assert r.hard_fail is False
    assert r.checks["schema_valid"] is True
    assert r.checks["required_fields"] is True
    assert r.checks["severity_enum"] is True


def test_valid_dict_output_passes():
    # output may already be a parsed dict, not a JSON string
    r = run_deterministic(_VALID, {})
    assert r.passed is True
    assert r.hard_fail is False


def test_schema_invalid_is_hard_fail():
    r = run_deterministic("not json {", {})
    assert r.passed is False
    assert r.hard_fail is True
    assert r.checks["schema_valid"] is False


def test_non_object_json_is_hard_fail():
    # valid JSON but not a top-level object
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
