"""Inbound correlation-id resolution (feature 010, M6 / finding F13).

Ties an inbound request to the run it produces. Precedence (D1): W3C `traceparent` trace-id → a plain
`X-Correlation-Id` → a generated id. An untrusted inbound value is shape-validated and ignored if malformed,
so nothing unbounded lands in the run record / logs (P3, FR-004). Pure of DB/metrics; the control decision
lives at the API boundary (P6 — the runtime already carries correlation_id on its RunRecord).
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from fastapi import Request

# traceparent: version "-" trace-id(32 hex) "-" parent-id(16 hex) "-" flags(2 hex). We use the trace-id.
_TRACEPARENT = re.compile(r"^[0-9a-f]{2}-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}$")
_ZERO_TRACE = "0" * 32  # W3C "invalid" trace-id sentinel
_XCORR = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def resolve_correlation(request) -> tuple[str, bool]:
    """Resolve `(correlation_id, supplied)`: `supplied` is True when the id came from the caller (a valid
    traceparent / X-Correlation-Id), False when we generated it. Feature 023 needs the distinction because a
    caller-supplied value is never forwarded to a model provider (FR-014)."""
    headers = request.headers
    tp = headers.get("traceparent")
    if tp:
        m = _TRACEPARENT.match(tp.strip())
        if m and m.group(1) != _ZERO_TRACE:
            return m.group(1), True
    xc = headers.get("x-correlation-id")   # Starlette headers are case-insensitive
    if xc and _XCORR.match(xc.strip()):
        return xc.strip(), True
    return uuid.uuid4().hex, False


def resolve_correlation_id(request) -> str:
    """Resolve the correlation id for a request from its headers; always returns a non-empty id (FR-003).

    `request` only needs a case-insensitive `.headers` mapping (Starlette Request satisfies this; tests pass
    a lightweight fake)."""
    return resolve_correlation(request)[0]


def provider_correlation_id_for(corr: str, supplied: bool) -> str:
    """The id sent to model providers (023 FR-014): ours when we generated `corr`, else a fresh 32-hex id —
    even if the caller's value happens to look like one. Never caller data."""
    return uuid.uuid4().hex if supplied else corr


@dataclass(frozen=True)
class Correlation:
    id: str            # caller-facing correlation id (echoed in X-Correlation-Id, stored on the run)
    provider_id: str   # platform-generated id forwarded to providers (FR-014)


def _resolve_cached(request) -> Correlation:
    # One resolution per request, so `correlation_id` and `correlation` dependencies agree on a generated id.
    cached = getattr(request.state, "ac_correlation", None) if hasattr(request, "state") else None
    if cached is None:
        cid, supplied = resolve_correlation(request)
        cached = Correlation(cid, provider_correlation_id_for(cid, supplied))
        if hasattr(request, "state"):
            request.state.ac_correlation = cached
    return cached


async def correlation_id(request: Request) -> str:
    """FastAPI dependency wrapper (handlers `Depends(correlation_id)`); the Request is injected."""
    return _resolve_cached(request).id


async def correlation(request: Request) -> Correlation:
    """FastAPI dependency: caller-facing id + the platform-generated provider id (feature 023)."""
    return _resolve_cached(request)
