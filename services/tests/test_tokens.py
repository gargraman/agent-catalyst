"""US5 — count_tokens is monotonic + over-estimate-safe (M2a, research D4)."""
from __future__ import annotations

from runtime.tokens import count_tokens


def _msgs(text: str):
    return [{"role": "user", "content": text}]


def test_count_tokens_monotonic_in_size():
    short = count_tokens("gpt-5-mini", _msgs("hi"))
    long = count_tokens("gpt-5-mini", _msgs("hi " * 500))
    assert long > short


def test_count_tokens_over_estimates_vs_reference():
    # The guard must never UNDER-count (else a real overflow slips through). Reference ~ chars/4.
    text = "lorem ipsum dolor sit amet " * 50
    reference = len(text) // 4
    assert count_tokens("gpt-5-mini", _msgs(text)) >= reference


def test_count_tokens_includes_system_and_tools():
    base = count_tokens("gpt-5-mini", _msgs("hello"))
    more = count_tokens("gpt-5-mini", _msgs("hello"), system="a long system prompt " * 20)
    assert more > base
