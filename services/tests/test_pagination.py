"""M0.0.a (RED) — keyset cursor codec + limit clamp + sort whitelist (00L §2)."""
from __future__ import annotations

import pytest

from api import errors as E
from api import pagination as P


def test_page_model_defaults_next_cursor_none():
    page = P.Page[int](items=[1, 2, 3])
    assert page.items == [1, 2, 3]
    assert page.next_cursor is None


def test_cursor_round_trip():
    cur = P.encode_cursor("2026-01-01T00:00:00Z", "row-123")
    assert isinstance(cur, str)
    assert P.decode_cursor(cur) == ("2026-01-01T00:00:00Z", "row-123")


def test_cursor_is_opaque_not_plaintext():
    cur = P.encode_cursor("2026-01-01T00:00:00Z", "row-123")
    assert "row-123" not in cur  # base64url-encoded, not raw


def test_decode_none_returns_none():
    assert P.decode_cursor(None) is None
    assert P.decode_cursor("") is None


def test_decode_malformed_raises_input_invalid():
    with pytest.raises(E.InputInvalid):
        P.decode_cursor("!!! not base64 !!!")


# 00L §2 contract: `limit or DEFAULT_LIMIT` — 0 and None are "unset" → default 50;
# negatives clamp up to 1; over-max clamps down to 200.
@pytest.mark.parametrize("given,expected", [(None, 50), (0, 50), (-5, 1), (5, 5), (200, 200), (9999, 200)])
def test_clamp_limit(given, expected):
    assert P.clamp_limit(given) == expected


def test_validate_sort_default_when_none():
    assert P.validate_sort(None, {"created_at", "name"}, "created_at") == "created_at"


def test_validate_sort_allows_descending_prefix():
    assert P.validate_sort("-created_at", {"created_at"}, "created_at") == "-created_at"


def test_validate_sort_rejects_unknown_key():
    with pytest.raises(E.InputInvalid):
        P.validate_sort("password", {"created_at", "name"}, "created_at")
