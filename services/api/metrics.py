"""Run-observability metrics (feature 010, M6 / finding F12).

Process-local Prometheus counters, scraped at an unauthenticated `/metrics`:
  - `ac_runs_total{status, source}`     — +1 per recorded run (status = run outcome, source = playground|eval)
  - `ac_requests_refused_total{code}`   — +1 per admission refusal that never becomes a run (budget.exceeded,
    access.denied), observed at the error handler.

Aggregate labels only — no tenant id tied to content, no prompt/response, no secrets (P5/SC-009). Increments
are best-effort: a metrics failure MUST NOT fail a run, its stream, or its persistence (P8/FR-009). A custom
registry keeps the surface to exactly these two counters and lets tests read exact values.
"""
from __future__ import annotations

import logging

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, generate_latest

_log = logging.getLogger("api.metrics")

REGISTRY = CollectorRegistry()

ac_runs_total = Counter(
    "ac_runs_total", "Completed runs by outcome status and source",
    ["status", "source"], registry=REGISTRY,
)
ac_requests_refused_total = Counter(
    "ac_requests_refused_total", "Requests refused by a deterministic control, by error code",
    ["code"], registry=REGISTRY,
)


def record_run(status: str, source: str) -> None:
    """Count one completed run. Best-effort — never raises (telemetry is not load-bearing, FR-009)."""
    try:
        ac_runs_total.labels(status=status or "unknown", source=source or "unknown").inc()
    except Exception as e:  # noqa: BLE001 — telemetry must never break the run path
        _log.debug("ac_runs_total increment failed: %s", e)


def record_refusal(code: str) -> None:
    """Count one admission refusal (e.g. budget.exceeded / access.denied). Best-effort — never raises."""
    try:
        ac_requests_refused_total.labels(code=code or "unknown").inc()
    except Exception as e:  # noqa: BLE001
        _log.debug("ac_requests_refused_total increment failed: %s", e)


def render() -> tuple[bytes, str]:
    """Prometheus text exposition of the registry + its content-type (for the /metrics endpoint)."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
