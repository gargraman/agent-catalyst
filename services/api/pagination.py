"""Keyset pagination + filter/sort guard (00L §2).

``Page[T]`` is the contract. The cursor codec, limit clamp, and sort validator are implemented;
their behavior is pinned by test_pagination.py.
"""
from __future__ import annotations

import base64
import json
from typing import Generic, TypeVar

from pydantic import BaseModel

from api.errors import InputInvalid

T = TypeVar("T")

DEFAULT_LIMIT, MAX_LIMIT = 50, 200


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = None  # null ⇒ last page


def encode_cursor(sort_value: str, row_id: str) -> str:
    """Opaque, stable cursor encoding the (sort_key, id) of the last row on the page so the
    next query is a keyset seek, not an OFFSET (which drifts under concurrent inserts)."""
    raw = json.dumps({"s": sort_value, "id": row_id}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(cursor: str | None) -> tuple[str, str] | None:
    """Decode a cursor to ``(sort_value, row_id)``; ``None`` for an empty cursor;
    a malformed cursor raises ``InputInvalid`` (a client error, never a 500)."""
    if not cursor:
        return None
    try:
        d = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
        return d["s"], d["id"]
    except Exception as exc:
        raise InputInvalid("invalid cursor", detail={"cursor": cursor}) from exc


def clamp_limit(limit: int | None) -> int:
    """Clamp a requested page size into ``[1, MAX_LIMIT]`` (default ``DEFAULT_LIMIT``)."""
    return max(1, min(limit or DEFAULT_LIMIT, MAX_LIMIT))


def validate_sort(sort: str | None, allowed: set[str], default: str) -> str:
    """Whitelist the sort key (leading ``-`` = descending); unknown → ``InputInvalid``."""
    key = sort or default
    # Strip exactly ONE leading '-' (DESC marker). lstrip('-') would strip all of them, so '--name'
    # or '-----name' would wrongly pass the whitelist and return a malformed ORDER BY key.
    base = key[1:] if key.startswith("-") else key
    if base not in allowed:
        raise InputInvalid("unsupported sort key", detail={"sort": sort, "allowed": sorted(allowed)})
    return key
