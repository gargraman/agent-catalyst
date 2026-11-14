"""Per-call model log sink (feature 023, spec A2; contract §1; research D4/D5/D9).

The runtime hands one pure `ModelCallRecord` per model turn to `RunContext.record_call`; this module decides
what leaves the process. Policy lives here (P6), never in the runtime:

  * **Metadata by default** (Q1 = A): ids, model, prompt version, latency, cost, outcome, character counts, and
    keyed per-workspace content fingerprints. No prompt/output text.
  * **Redacted text only on opt-in** (workspace admin setting, or the dev profile): `redact()` first, THEN
    truncate to 2,048 chars (a secret split by the cut can't leak); redactor failure → `[redaction-failed]`.
  * **Never a credential**: the run's provider api key is scrubbed by exact match from the serialized line
    (content on or off), on top of the redactor's own exact-match pass.
  * **Best-effort** (FR-009): every stage is guarded; a failure is counted in `ac_call_log_errors_total{stage}`
    and never raised into the run.

Lines go to a dedicated `ac.call_log` logger → stdout, one JSON object per line, `propagate=False` so app log
config can't reformat or duplicate them. Not a source of truth — the `runs` row remains the billable record.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import sys
from datetime import datetime, timezone
from functools import lru_cache

from api import metrics
from api.settings import get_settings
from runtime.guardrails.redact import redact

TEXT_CAP = 2048
REDACTION_FAILED = "[redaction-failed]"

_log = logging.getLogger("ac.call_log")
if not _log.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(message)s"))
    _log.addHandler(_h)
_log.setLevel(logging.INFO)
_log.propagate = False


@lru_cache(maxsize=1024)
def _workspace_key(root: str, workspace_id: str) -> bytes:
    # Per-workspace derived key: fingerprints match within a tenant but cannot be correlated across tenants (D4).
    return hmac.new(root.encode(), workspace_id.encode(), hashlib.sha256).digest()


def fingerprint(workspace_id: str, text: str) -> str:
    """Keyed, non-reversible content fingerprint: HMAC-SHA256(HMAC-SHA256(root, ws), text), 32 hex; "" if empty."""
    if not text:
        return ""
    root = get_settings().telemetry_fingerprint_key.get_secret_value()
    return hmac.new(_workspace_key(root, workspace_id), text.encode(), hashlib.sha256).hexdigest()[:32]


def _dumps(obj: dict) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, default=str)


def _safe_text(text: str, api_key: str | None) -> str:
    try:
        return redact(text, known_secrets=[api_key] if api_key else ())[:TEXT_CAP]
    except Exception:  # noqa: BLE001 — fail closed: never fall back to raw text (FR-007)
        metrics.record_call_log_error("redact")
        return REDACTION_FAILED


def build_line(rec, *, workspace_id: str, api_key: str | None, content_logging: bool) -> dict:
    """Project a ModelCallRecord onto the contract §1 line (pure apart from settings/metrics)."""
    ws = workspace_id or rec.workspace_id
    line = {
        "event": "model_call",
        "ts": datetime.now(timezone.utc).isoformat(),
        "run_id": rec.run_id,
        "correlation_id": rec.correlation_id,
        "provider_correlation_id": rec.provider_correlation_id,
        "workspace_id": rec.workspace_id,
        "agent_id": rec.agent_id,
        "version_id": rec.version_id,
        "source": rec.source,
        "role": rec.role,
        "call_seq": rec.call_seq,
        "correction": rec.correction,
        "provider": rec.provider,
        "model": rec.model,
        "prompt_version": rec.prompt_version,
        "latency_ms": rec.latency_ms,
        "first_token_ms": rec.first_token_ms,
        "cost_usd": str(rec.cost_usd),
        "cost_known": rec.cost_known,
        "success": rec.success,
        "error_code": rec.error_code,
        "provider_request_id": rec.provider_request_id,
        "input_chars": len(rec.input_text or ""),
        "output_chars": len(rec.output_text or ""),
    }
    fps = {"input_fp": "", "output_fp": ""}
    fp_failed = False
    for key, text in (("input_fp", rec.input_text), ("output_fp", rec.output_text)):
        if text:
            try:
                fps[key] = fingerprint(ws, text)
            except Exception:  # noqa: BLE001
                fp_failed = True
    if fp_failed:
        metrics.record_call_log_error("fingerprint")
    line.update(fps)
    if content_logging:
        line["input_text"] = _safe_text(rec.input_text, api_key)
        line["output_text"] = _safe_text(rec.output_text, api_key)
    return line


def make_call_sink(*, workspace_id: str, api_key: str | None, content_logging: bool):
    """A `CallSink` for one run: writes one JSON line per ModelCallRecord. Never raises (FR-009)."""

    def _sink(rec) -> None:
        try:
            line = build_line(rec, workspace_id=workspace_id, api_key=api_key, content_logging=content_logging)
        except Exception:  # noqa: BLE001
            metrics.record_call_log_error("serialize")
            return
        try:
            out = _dumps(line)
            if api_key and api_key in out:      # exact-match credential scrub over the whole line (FR-008)
                out = out.replace(api_key, "[REDACTED:secret]")
        except Exception:  # noqa: BLE001
            metrics.record_call_log_error("serialize")
            return
        try:
            _log.info(out)
        except Exception:  # noqa: BLE001
            metrics.record_call_log_error("write")

    return _sink


async def effective_content_logging(session, workspace_id: str) -> tuple[bool, bool]:
    """(stored, effective) for a workspace (T018). stored = `workspace_settings.content_logging` (missing row ⇒
    False); effective = stored OR the non-prod dev-profile override (never under prod)."""
    import uuid

    from sqlalchemy import select

    from api.models import WorkspaceSettings

    stored = (await session.execute(
        select(WorkspaceSettings.content_logging).where(WorkspaceSettings.workspace_id == uuid.UUID(workspace_id))
    )).scalar_one_or_none()
    stored = bool(stored)
    return stored, stored or get_settings().telemetry_content_override
