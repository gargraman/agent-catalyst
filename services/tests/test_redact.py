"""Feature 023 US1 (T007): the shared redaction primitive `runtime.guardrails.redact.redact` (FR-006/008/010).

Pure, deterministic. Sample secrets below are synthetic and shaped only to exercise the patterns.
"""
from __future__ import annotations

import pytest

from runtime.guardrails.redact import redact

# Synthetic, pattern-shaped values (not real credentials). Built by concatenation so secret scanners don't flag
# the test file itself.
OPENAI = "sk-" + "proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
ANTHROPIC = "sk-ant-" + "api03-" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6"
AWS = "AKIA" + "IOSFODNN7EXAMPLE"
GITHUB = "ghp_" + "a" * 36
SLACK = "xoxb-" + "123456789012-abcdefABCDEF"
JWT = "eyJhbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0" + "." + "dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"
PEM = "-----BEGIN " + "RSA PRIVATE KEY-----\nMIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1tPf9Cnzj4p4WGeKLs1Pt8Qu\n-----END " + "RSA PRIVATE KEY-----"


@pytest.mark.parametrize("secret, kind", [
    (OPENAI, "api_key"),
    (ANTHROPIC, "api_key"),
    (AWS, "aws_key"),
    (GITHUB, "github_token"),
    (SLACK, "slack_token"),
    (JWT, "jwt"),
    (PEM, "private_key"),
])
def test_credential_patterns(secret, kind):
    out = redact(f"before {secret} after")
    assert secret not in out
    assert f"[REDACTED:{kind}]" in out
    assert out.startswith("before ") and out.endswith(" after")


def test_bearer_token():
    out = redact("Authorization: Bearer abcdef1234567890ABCDEF")
    assert "abcdef1234567890ABCDEF" not in out and "[REDACTED:bearer]" in out


@pytest.mark.parametrize("pii, kind", [
    ("jane.doe@example.com", "email"),
    ("+14155552671", "phone"),
    ("(415) 555-2671", "phone"),
    ("415-555-2671", "phone"),
    ("4111 1111 1111 1111", "card"),
    ("4111111111111111", "card"),
    ("123-45-6789", "ssn"),
])
def test_pii_patterns(pii, kind):
    out = redact(f"contact {pii} now")
    assert pii not in out and f"[REDACTED:{kind}]" in out


def test_luhn_invalid_number_is_not_a_card():
    out = redact("order 4111111111111112 shipped")
    assert "[REDACTED:card]" not in out


def test_exact_match_known_secret_even_without_pattern():
    weird = "zz-not-a-pattern-QQQ"
    out = redact(f"key={weird};", known_secrets=[weird])
    assert weird not in out and "[REDACTED:secret]" in out


def test_known_secret_empty_values_ignored():
    assert redact("hello", known_secrets=["", None]) == "hello"


@pytest.mark.parametrize("value", [None, ""])
def test_empty(value):
    assert redact(value) == ""


def test_plain_prose_unchanged_and_deterministic():
    text = "The quick brown fox jumps over 3 lazy dogs at 10:30 on 2026-11-14."
    assert redact(text) == text
    assert redact(OPENAI + " x") == redact(OPENAI + " x")
