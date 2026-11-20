"""Feature 010 (M6 observability) — pure correlation-id resolution (T002).

RED until api.correlation exists. No DB, no HTTP server — a tiny fake request exposing `.headers`.
"""
from __future__ import annotations

import re

from api.correlation import resolve_correlation_id

_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_VALID_TP = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
_ZERO_TP = "00-00000000000000000000000000000000-00f067aa0ba902b7-01"


class _Req:
    def __init__(self, headers: dict):
        # Starlette headers are case-insensitive; mimic with lowercased keys.
        self.headers = {k.lower(): v for k, v in headers.items()}


def test_traceparent_trace_id_is_used():
    assert resolve_correlation_id(_Req({"traceparent": _VALID_TP})) == _TRACE_ID


def test_all_zero_traceparent_is_ignored_falls_back_generated():
    out = resolve_correlation_id(_Req({"traceparent": _ZERO_TP}))
    assert out != "0" * 32 and _HEX32.match(out)   # generated fallback, not the invalid sentinel


def test_x_correlation_id_used_when_no_traceparent():
    assert resolve_correlation_id(_Req({"X-Correlation-Id": "my-corr_123.4"})) == "my-corr_123.4"


def test_malformed_x_correlation_id_ignored():
    out = resolve_correlation_id(_Req({"X-Correlation-Id": "bad id!! spaces"}))
    assert _HEX32.match(out)                         # generated fallback


def test_overlong_x_correlation_id_ignored():
    out = resolve_correlation_id(_Req({"X-Correlation-Id": "a" * 129}))
    assert _HEX32.match(out)


def test_traceparent_precedence_over_x_correlation_id():
    out = resolve_correlation_id(_Req({"traceparent": _VALID_TP, "X-Correlation-Id": "other"}))
    assert out == _TRACE_ID


def test_no_headers_generates_hex_id():
    assert _HEX32.match(resolve_correlation_id(_Req({})))


# ---- Feature 023 US2 (T023): caller-supplied vs generated + provider id derivation (FR-014) ----------------
from api.correlation import provider_correlation_id_for, resolve_correlation  # noqa: E402


def test_resolve_correlation_flags_supplied():
    assert resolve_correlation(_Req({"traceparent": _VALID_TP})) == (_TRACE_ID, True)
    assert resolve_correlation(_Req({"X-Correlation-Id": "demo-123"})) == ("demo-123", True)
    cid, supplied = resolve_correlation(_Req({}))
    assert supplied is False and _HEX32.match(cid)
    cid, supplied = resolve_correlation(_Req({"X-Correlation-Id": "bad id with spaces"}))
    assert supplied is False and _HEX32.match(cid)


def test_provider_id_reuses_generated_mints_fresh_for_supplied():
    assert provider_correlation_id_for("f" * 32, False) == "f" * 32
    fresh = provider_correlation_id_for("demo-123", True)
    assert _HEX32.match(fresh) and fresh != "demo-123"
    # a supplied id that HAPPENS to be 32-hex is still replaced (never forward caller data)
    assert provider_correlation_id_for(_TRACE_ID, True) != _TRACE_ID
