"""M0.0.a (RED) — the canonical JSON serializer (01L §4.1).

These pin the exact byte-level rules that make snapshot/prompt/skill hashes stable across
processes and machines: keys sorted, list order preserved, no whitespace, UTF-8, floats and
Decimals normalized to a canonical decimal string.
"""
from __future__ import annotations

from decimal import Decimal

from runtime.hashing import canonical_json, sha256_hex


def test_keys_are_sorted():
    # numbers canonicalize to decimal strings (F16: int/float unified), keys sorted
    assert canonical_json({"b": 1, "a": 2}) == b'{"a":"2","b":"1"}'


def test_no_whitespace():
    assert b" " not in canonical_json({"a": 1, "b": [1, 2]})


def test_list_order_is_preserved():
    assert canonical_json([3, 1, 2]) == b'["3","1","2"]'


def test_int_and_equal_float_hash_identically():
    # F16: a param stored as int vs equal float must not desync the snapshot hash
    assert sha256_hex({"x": 1}) == sha256_hex({"x": 1.0})
    assert sha256_hex({"max_tokens": 1024}) == sha256_hex({"max_tokens": 1024.0})


def test_float_normalized_to_decimal_string():
    # floats become a canonical decimal STRING (no binary drift across machines)
    assert canonical_json({"t": 0.2}) == b'{"t":"0.2"}'


def test_decimal_normalized_strips_trailing_zeros():
    assert canonical_json({"c": Decimal("1.50")}) == b'{"c":"1.5"}'


def test_float_and_equal_decimal_hash_identically():
    assert sha256_hex({"x": 0.25}) == sha256_hex({"x": Decimal("0.25")})


def test_insertion_order_independent():
    a = {"a": 1, "b": 2, "nested": {"y": 9, "x": 8}}
    b = {"nested": {"x": 8, "y": 9}, "b": 2, "a": 1}
    assert sha256_hex(a) == sha256_hex(b)


def test_utf8_preserved_not_escaped():
    assert "café".encode("utf-8") in canonical_json({"name": "café"})


def test_sha256_hex_is_64_hex_chars():
    h = sha256_hex({"a": 1})
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)
