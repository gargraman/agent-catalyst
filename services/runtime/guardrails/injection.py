"""Deterministic indirect-prompt-injection scanner (feature 024, research D11, contracts/runtime.md §7; spec FR-021..FR-026).

Pipeline: NFKC → hidden-character handling (Unicode tag block decoded and flagged; bidi controls flagged; zero-width
characters stripped, flagged when they split a word) → lowercase → collapse horizontal whitespace (newlines kept, so
line-anchored role labels still work) → five pattern families. HTML comments and bounded base64 blocks are decoded and
re-scanned once (never recursively). Pure, deterministic, no I/O.

Honest limit: this is a speed bump tuned on the DEV corpora only, not a guarantee. The real containment is the D13
rendering (context is a delimited, escaped user-role block) plus the F3 tool allow-list.
"""
from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from dataclasses import dataclass

SCANNER_VERSION = "inj-v1"
CATEGORIES = ("instruction_override", "role_impersonation", "tool_hijack", "exfiltration", "hidden_instruction")

_MAX_B64_DECODES = 5
_MAX_B64_BYTES = 4096


@dataclass(frozen=True)
class ScanResult:
    flagged: bool
    categories: tuple[str, ...] = ()
    scanner_version: str = SCANNER_VERSION


# ---------------------------------------------------------------------------------------------- normalization

_ZERO_WIDTH = "​‌‍⁠﻿­"
_ZW_IN_WORD = re.compile(rf"[A-Za-z][{_ZERO_WIDTH}]+[A-Za-z]")
_ZW = re.compile(rf"[{_ZERO_WIDTH}]")
_BIDI = re.compile("[‪-‮⁦-⁩]")
_TAG = re.compile("[\U000e0000-\U000e007f]+")
_HSPACE = re.compile(r"[ \t\f\v ]+")


def normalize(text: str) -> tuple[str, bool]:
    """(normalized text, hidden-characters-present). Tag-block payloads are decoded onto their own line so the
    families can see what they say."""
    t = unicodedata.normalize("NFKC", text or "")
    hidden = False
    tags = _TAG.findall(t)
    if tags:
        hidden = True
        decoded = " ".join("".join(chr(ord(c) - 0xE0000) for c in run) for run in tags)
        t = _TAG.sub(" ", t) + "\n" + decoded
    if _BIDI.search(t):
        hidden = True
        t = _BIDI.sub(" ", t)
    if _ZW_IN_WORD.search(t):
        hidden = True
    t = _ZW.sub("", t)
    t = _HSPACE.sub(" ", t.lower())
    return t, hidden


# ---------------------------------------------------------------------------------------------- pattern families

_DET = r"(?:all|any|the|your|my|these|those|previous|prior|above|earlier|preceding|original|system|every)"
_FAMILIES: dict[str, tuple[re.Pattern, ...]] = {
    "instruction_override": tuple(re.compile(p, re.M) for p in (
        rf"\b(?:ignore|disregard|forget|override|bypass)\b[^.\n]{{0,40}}?\b{_DET}\b[^.\n]{{0,30}}?"
        r"\b(?:instructions?|prompts?|rules|directions|guidelines)\b",
        r"\b(?:ignore|disregard|forget)\b (?:everything|all|anything) (?:above|before|previously|prior|said)",
        r"\b(?:previous|prior|above|earlier|original) instructions (?:no longer apply|are (?:void|cancell?ed|revoked|"
        r"obsolete))",
        r"\bnew instructions\s*:",
        r"\byou are now (?:an? )?(?:unrestricted|unfiltered|jailbroken|uncensored|free of)",
        r"\bfrom now on,? you (?:must|will|answer|respond|only)",
    )),
    "role_impersonation": tuple(re.compile(p, re.M) for p in (
        r"<\|im_(?:start|end)\|>",
        r"<\|(?:system|assistant|user|endoftext|developer)\|>",
        r"\[/?inst\]",
        r"\[system[^\]\n]{0,24}\]",
        r"^\s*#{2,}\s*(?:system|instructions?)\s*:?\s*$",
        r"^\s*(?:system|assistant|developer)\s*:\s*\S",
        r"\bdeveloper mode (?:enabled|on|activated)",
        r"\byou are (?:now )?dan\b",
    )),
    "tool_hijack": tuple(re.compile(p, re.M) for p in (
        r"\b(?:call|invoke|use|run|execute|trigger)\b (?:the |a |an )?[a-z0-9]+(?:_[a-z0-9]+)+ (?:tool|function)\b",
        r"\b(?:call|invoke|execute|run|trigger)\b (?:the )?(?:function|tool) [a-z0-9]+(?:_[a-z0-9]+)+",
        r"\b(?:run|use|invoke|call) the (?:shell|terminal|bash|exec|command[- ]line) (?:tool|function|command)",
        r"\{\s*\"name\"\s*:\s*\"[^\"]+\"\s*,\s*\"(?:arguments|args|parameters)\"\s*:",
    )),
    "exfiltration": tuple(re.compile(p, re.M) for p in (
        r"!\[[^\]\n]*\]\(\s*https?://[^)\s]+\?[^)\s]*",                       # image beacon carrying a query
        r"\]\(\s*https?://[^)\s]*\?[^)\s]*\{[^}\s]*\}",                       # link with a template placeholder
        r"\b(?:send|post|forward|upload|transmit|exfiltrate|leak|submit)\b[^.\n]{0,80}?\b(?:to|into|via)\b"
        r"[^.\n]{0,40}?https?://",
        r"\b(?:send|forward|email|mail)\b[^.\n]{0,60}?\b(?:all|every|the full|entire)\b[^.\n]{0,60}?\bto\s+\S+@\S+",
        r"\b(?:add|append|include|render|insert)\b[^.\n]{0,40}?\bimage\b[^.\n]{0,80}?https?://\S*\?",
        r"data:(?:text/html|text/javascript|application/(?:x-)?javascript|image/svg\+xml)[;,]",
    )),
}
_COMMENT = re.compile(r"<!--(.*?)(?:-->|$)", re.S)
_HIDING_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(?:px)?\b")
_B64 = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")


_LINE_ANCHORED = frozenset({"role_impersonation"})   # needs line structure (`^system:`); everything else must not —
_ANY_WS = re.compile(r"\s+")                         # an attacker can wrap "call the x\ntool" across lines


def _families(t: str) -> set[str]:
    flat = _ANY_WS.sub(" ", t)
    return {cat for cat, pats in _FAMILIES.items()
            if any(p.search(t if cat in _LINE_ANCHORED else flat) for p in pats)}


def _b64_payloads(raw: str):
    for n, m in enumerate(_B64.finditer(raw)):
        if n >= _MAX_B64_DECODES:
            return
        blob = m.group(0)[: _MAX_B64_BYTES * 4 // 3 + 4]
        try:
            data = base64.b64decode(blob + "=" * (-len(blob) % 4), validate=False)[:_MAX_B64_BYTES]
            text = data.decode("utf-8")
        except (binascii.Error, ValueError, UnicodeDecodeError):
            continue
        if text and sum(c.isprintable() or c.isspace() for c in text) / len(text) > 0.9:
            yield text


def scan(text: str) -> ScanResult:
    t, hidden = normalize(text)
    cats = _families(t)
    hidden_hit = hidden
    for inner in _COMMENT.findall(t):                       # imperatives hidden in HTML comments
        found = _families(inner.strip())
        if found:
            cats |= found
            hidden_hit = True
    if cats and _HIDING_STYLE.search(t):                    # CSS-hidden element carrying an instruction
        hidden_hit = True
    for payload in _b64_payloads(unicodedata.normalize("NFKC", text or "")):   # base64 is case-sensitive: raw text
        found = _families(normalize(payload)[0])
        if found:
            cats |= found
            hidden_hit = True
    if hidden_hit:
        cats.add("hidden_instruction")
    ordered = tuple(c for c in CATEGORIES if c in cats)
    return ScanResult(bool(ordered), ordered, SCANNER_VERSION)
