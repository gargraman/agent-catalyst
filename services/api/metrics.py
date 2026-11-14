"""Run-observability metrics (feature 010, M6 / finding F12; rate-limit counters added by feature 016 / F5).

Process-local Prometheus counters, scraped at an unauthenticated `/metrics`:
  - `ac_runs_total{status, source}`         — +1 per recorded run (status = run outcome, source = playground|eval)
  - `ac_requests_refused_total{code}`       — +1 per admission refusal that never becomes a run (budget.exceeded,
    access.denied), observed at the error handler.
  - `ac_rate_limited_total{route_class}`    — +1 per 429 from the rate limiter, by route class (feature 016).
  - `ac_rate_limit_degraded_total`          — +1 per limiter check that failed open on a Redis outage (the
    alert signal; feature 016, SC-016a).

Aggregate labels only — no tenant id tied to content, no prompt/response, no secrets (P5/SC-009). Increments
are best-effort: a metrics failure MUST NOT fail a run, its stream, or its persistence (P8/FR-009). A custom
registry keeps the surface to exactly this set of counters and lets tests read exact values.
"""
from __future__ import annotations

import logging

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest

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
ac_rate_limited_total = Counter(
    "ac_rate_limited_total", "Requests rejected by the rate limiter (429), by route class",
    ["route_class"], registry=REGISTRY,
)
ac_rate_limit_degraded_total = Counter(
    "ac_rate_limit_degraded_total",
    "Limiter checks that failed open because the Redis backend was unavailable (alert signal)",
    registry=REGISTRY,
)
ac_egress_blocked_total = Counter(
    "ac_egress_blocked_total",
    "Outbound tool egress attempts refused by the SSRF/egress guard, by coarse reason (feature 017, F19)",
    ["reason"], registry=REGISTRY,
)


ac_call_log_errors_total = Counter(
    "ac_call_log_errors_total",
    "Per-call log emission failures by stage (feature 023; fingerprint|redact|serialize|write) — never a run failure",
    ["stage"], registry=REGISTRY,
)


# Feature 023 US3 latency SLIs — labeled by caller source only (FR-018).
ac_first_token_seconds = Histogram(
    "ac_first_token_seconds", "Time to first streamed token per pass-through run (feature 023)",
    ["source"], buckets=(0.25, 0.5, 1, 1.5, 2, 3, 5, 8, 13, 30), registry=REGISTRY,
)
ac_run_latency_seconds = Histogram(
    "ac_run_latency_seconds", "End-to-end run latency per recorded run (feature 023; p99 <= 30s deadline SLO)",
    ["source"], buckets=(0.5, 1, 2, 5, 10, 15, 20, 25, 30, 45), registry=REGISTRY,
)
ac_deadline_exceeded_total = Counter(
    "ac_deadline_exceeded_total", "Runs cut by the wall-clock deadline (outcome timeout), by source (feature 023)",
    ["source"], registry=REGISTRY,
)


def record_run_slis(rec) -> None:
    """Observe the 023 SLIs for one recorded RunRecord — called at the same exactly-once site as `record_run`.
    Best-effort — never raises (FR-009)."""
    try:
        # Same fallback as the record_run call site so ac_runs_total and the SLIs label a run identically.
        source = getattr(rec, "source", None) or "playground"
        ac_run_latency_seconds.labels(source=source).observe(max(0, int(rec.latency_ms or 0)) / 1000.0)
        ft = getattr(rec, "first_token_ms", None)
        if ft is not None:
            ac_first_token_seconds.labels(source=source).observe(max(0, int(ft)) / 1000.0)
        outcome = rec.outcome.value if hasattr(rec.outcome, "value") else str(rec.outcome)
        if outcome == "timeout":
            ac_deadline_exceeded_total.labels(source=source).inc()
    except Exception as e:  # noqa: BLE001 — telemetry must never break the run path
        _log.debug("SLI observation failed: %s", e)


def record_call_log_error(stage: str) -> None:
    """Count one call-log sink failure by stage (023 FR-009 alert signal). Best-effort — never raises."""
    try:
        ac_call_log_errors_total.labels(stage=stage or "unknown").inc()
    except Exception as e:  # noqa: BLE001
        _log.debug("ac_call_log_errors_total increment failed: %s", e)


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


def record_rate_limited(route_class: str) -> None:
    """Count one 429 rejection from the limiter, by route class. Best-effort — never raises (FR-009)."""
    try:
        ac_rate_limited_total.labels(route_class=route_class or "unknown").inc()
    except Exception as e:  # noqa: BLE001 — telemetry must never break the request path
        _log.debug("ac_rate_limited_total increment failed: %s", e)


def record_rate_limit_degraded() -> None:
    """Count one fail-open (Redis unavailable) limiter check — the alert signal (SC-016a). Never raises."""
    try:
        ac_rate_limit_degraded_total.inc()
    except Exception as e:  # noqa: BLE001
        _log.debug("ac_rate_limit_degraded_total increment failed: %s", e)


def record_egress_blocked(reason: str) -> None:
    """Count one egress attempt refused by the SSRF guard, by coarse reason (FR-009 diagnostics). The label is
    a fixed coarse category (never a host/IP/secret — SC-003/P5). Best-effort — never raises (FR-027 must not
    let a telemetry failure turn a block into an allow)."""
    try:
        ac_egress_blocked_total.labels(reason=reason or "unknown").inc()
    except Exception as e:  # noqa: BLE001 — telemetry must never break the guard's fail-closed decision
        _log.debug("ac_egress_blocked_total increment failed: %s", e)


def render() -> tuple[bytes, str]:
    """Prometheus text exposition of the registry + its content-type (for the /metrics endpoint)."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
