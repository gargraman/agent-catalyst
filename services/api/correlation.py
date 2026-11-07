"""Inbound correlation-id resolution (feature 010, M6 / finding F13).

Ties an inbound request to the run it produces. Precedence (D1): W3C `traceparent` trace-id → a plain
`X-Correlation-Id` → a generated id. An untrusted inbound value is shape-validated and ignored if malformed,
so nothing unbounded lands in the run record / logs (P3, FR-004). Pure of DB/metrics; the control decision
lives at the API boundary (P6 — the runtime already carries correlation_id on its RunRecord).
"""
from __future__ import annotations

import re
import uuid

from fastapi import Request

# traceparent: version "-" trace-id(32 hex) "-" parent-id(16 hex) "-" flags(2 hex). We use the trace-id.
_TRACEPARENT = re.compile(r"^[0-9a-f]{2}-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}$")
_ZERO_TRACE = "0" * 32  # W3C "invalid" trace-id sentinel
_XCORR = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def resolve_correlation_id(request) -> str:
    """Resolve the correlation id for a request from its headers; always returns a non-empty id (FR-003).

    `request` only needs a case-insensitive `.headers` mapping (Starlette Request satisfies this; tests pass
    a lightweight fake).
    """
    headers = request.headers
    tp = headers.get("traceparent")
    if tp:
        m = _TRACEPARENT.match(tp.strip())
        if m and m.group(1) != _ZERO_TRACE:
            return m.group(1)
    xc = headers.get("x-correlation-id")   # Starlette headers are case-insensitive
    if xc and _XCORR.match(xc.strip()):
        return xc.strip()
    return uuid.uuid4().hex


async def correlation_id(request: Request) -> str:
    """FastAPI dependency wrapper (handlers `Depends(correlation_id)`); the Request is injected."""
    return resolve_correlation_id(request)
