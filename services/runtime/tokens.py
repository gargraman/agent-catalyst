"""Context-budget token estimation (M2a, feature 003, research D4).

A conservative, deterministic, offline heuristic behind a small abstraction. It MUST **over-estimate**
(never under-count) so a request that would overflow the model's context is caught *before* the provider
call rather than silently truncated. Exact per-provider tokenizers (tiktoken / provider count endpoints)
are a later upgrade that slots in behind this same signature; M2a makes no live calls.
"""
from __future__ import annotations

import json
import math

# Deliberately LOW chars-per-token (< the ~4 real-world average) so the estimate rounds UP vs reality.
_CHARS_PER_TOKEN = 3
_PER_MESSAGE_OVERHEAD = 4  # role/formatting framing per message/system turn


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # multimodal/parts: sum the text parts
        parts = []
        for p in content:
            if isinstance(p, dict) and isinstance(p.get("text"), str):
                parts.append(p["text"])
        return " ".join(parts)
    return ""


def count_tokens(model, messages, tools=None, system=None) -> int:
    """Over-estimating token count for system + messages + tool schemas. `model` is accepted for the
    future exact-tokenizer dispatch; the heuristic is model-agnostic."""
    chars = 0
    turns = 0
    if system:
        chars += len(system)
        turns += 1
    for m in messages or []:
        # Robust to non-dict message entries (e.g. a bare string list) — a token ESTIMATE must never
        # crash the pre-call guard and abandon the Run (review F2).
        chars += len(_text_of(m.get("content"))) if isinstance(m, dict) else len(str(m))
        turns += 1
    if tools:
        chars += len(json.dumps(tools, default=str))
    return math.ceil(chars / _CHARS_PER_TOKEN) + _PER_MESSAGE_OVERHEAD * turns
