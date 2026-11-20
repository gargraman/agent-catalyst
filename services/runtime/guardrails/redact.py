"""The shared redaction primitive (feature 023, FR-006/008/010; research D5).

ONE definition of "what counts as a secret / PII" for the platform: the per-call log sink uses it today, and the
future output guardrail MUST import this same function rather than grow a second rule set.

Order (deterministic, no model in the loop — constitution P3):
  1. exact-match scrub of every caller-supplied known secret (e.g. the run's provider api key) — the strongest
     FR-008 guarantee, independent of pattern coverage;
  2. credential-shape patterns (PEM private keys, JWTs, provider api keys, AWS ids, GitHub/Slack tokens, Bearer);
  3. PII patterns (email, Luhn-valid payment cards, US SSN shape, phone numbers).
Each hit becomes `[REDACTED:<kind>]`. Pattern redaction is NOT complete PII detection (names, addresses pass) —
that residual risk is why content logging is off by default. Truncation is the caller's job and must happen
AFTER redaction so a secret split by the cut cannot leak. Pure: stdlib `re` only (P6).
"""
from __future__ import annotations

import re
from typing import Iterable

_CREDENTIALS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----", re.DOTALL)),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")),
    ("api_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{10,}")),
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")),
)

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_CARD = re.compile(r"(?<![\d-])\d(?:[ -]?\d){12,18}(?![\d-])")
_SSN = re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])")
_PHONE = re.compile(
    r"(?<![\w+])\+\d{8,15}(?!\d)"                                   # E.164
    r"|(?<![\d-])(?:\(\d{3}\)\s?|\d{3}[-.\s])\d{3}[-.\s]\d{4}(?![\d-])"  # (415) 555-2671 / 415-555-2671
)


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _card_sub(m: re.Match[str]) -> str:
    digits = re.sub(r"[ -]", "", m.group(0))
    return "[REDACTED:card]" if 13 <= len(digits) <= 19 and _luhn_ok(digits) else m.group(0)


def redact(text: str | None, *, known_secrets: Iterable[str | None] = ()) -> str:
    """Return `text` with known secrets, credential shapes and common PII replaced by `[REDACTED:<kind>]`.

    Deterministic; `None`/empty → `""`. Raises only on programmer error — callers that must never leak wrap it and
    substitute a fixed marker on failure (fail closed, FR-007)."""
    if not text:
        return ""
    out = str(text)
    # Longest first so a secret that contains another known secret is scrubbed whole.
    for secret in sorted({s for s in known_secrets if s}, key=len, reverse=True):
        out = out.replace(secret, "[REDACTED:secret]")
    for kind, pattern in _CREDENTIALS:
        out = pattern.sub(f"[REDACTED:{kind}]", out)
    out = _EMAIL.sub("[REDACTED:email]", out)
    out = _CARD.sub(_card_sub, out)
    out = _SSN.sub("[REDACTED:ssn]", out)
    out = _PHONE.sub("[REDACTED:phone]", out)
    return out
