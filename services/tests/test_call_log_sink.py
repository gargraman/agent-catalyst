"""Feature 023 US1 (T008): the API call-log sink `api.call_log.make_call_sink` (contract §1).

Pure (no DB). Captures the dedicated `ac.call_log` logger with its own handler (it does not propagate to root).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import replace
from decimal import Decimal

import pytest

from api import call_log, metrics
from runtime.run import ModelCallRecord

API_KEY = "sk-" + "live-" + "Q1w2E3r4T5y6U7i8O9p0"   # synthetic

ALWAYS_KEYS = {
    "event", "ts", "run_id", "correlation_id", "provider_correlation_id", "workspace_id", "agent_id", "version_id",
    "source", "role", "call_seq", "correction", "provider", "model", "prompt_version", "latency_ms",
    "first_token_ms", "cost_usd", "cost_known", "success", "error_code", "provider_request_id",
    "input_chars", "output_chars", "input_fp", "output_fp",
}

WS_A = "11111111-1111-1111-1111-111111111111"
WS_B = "22222222-2222-2222-2222-222222222222"


def _rec(**kw) -> ModelCallRecord:
    base = dict(
        run_id="run-1", correlation_id="demo-123", provider_correlation_id="a" * 32, workspace_id=WS_A,
        agent_id="agt", version_id="ver", source="playground", role="candidate", call_seq=0, correction=False,
        provider="openai", model="gpt-5-mini", prompt_version="ph", input_text="hello world",
        output_text="hi there", latency_ms=12, first_token_ms=None, cost_usd=Decimal("0.000132"),
        cost_known=True, success=True, error_code=None,
    )
    base.update(kw)
    return ModelCallRecord(**base)


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def cap():
    h = _Capture()
    lg = logging.getLogger("ac.call_log")
    lg.addHandler(h)
    yield h
    lg.removeHandler(h)


def _errs(stage):
    return metrics.REGISTRY.get_sample_value("ac_call_log_errors_total", {"stage": stage}) or 0.0


def _one(cap) -> dict:
    assert len(cap.lines) == 1
    assert "\n" not in cap.lines[0]
    return json.loads(cap.lines[0])


def test_content_off_line_has_exactly_the_always_keys(cap):
    call_log.make_call_sink(workspace_id=WS_A, api_key=API_KEY, content_logging=False)(_rec())
    line = _one(cap)
    assert set(line) == ALWAYS_KEYS
    assert line["event"] == "model_call"
    assert line["cost_usd"] == "0.000132"
    assert line["input_chars"] == len("hello world") and line["output_chars"] == len("hi there")
    assert re.fullmatch(r"[0-9a-f]{32}", line["input_fp"])
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|\+00:00)", line["ts"])


def test_fingerprints_stable_per_workspace_distinct_across_workspaces():
    a1 = call_log.fingerprint(WS_A, "same prompt")
    a2 = call_log.fingerprint(WS_A, "same prompt")
    b1 = call_log.fingerprint(WS_B, "same prompt")
    assert a1 == a2 and a1 != b1
    assert re.fullmatch(r"[0-9a-f]{32}", a1)
    assert call_log.fingerprint(WS_A, "") == ""


def test_content_on_redacts_then_truncates(cap):
    secret = "sk-ant-" + "api03-" + "Z" * 40
    # Put the secret straddling the 2,048-char cut: it must be redacted before truncation.
    text = "x" * 2040 + " " + secret + " tail"
    call_log.make_call_sink(workspace_id=WS_A, api_key=API_KEY, content_logging=True)(
        _rec(input_text=text, output_text="mail me at jane.doe@example.com"))
    line = _one(cap)
    assert set(line) == ALWAYS_KEYS | {"input_text", "output_text"}
    assert len(line["input_text"]) <= 2048
    assert "Z" * 10 not in line["input_text"]
    assert "jane.doe@example.com" not in line["output_text"] and "[REDACTED:email]" in line["output_text"]


@pytest.mark.parametrize("content", [False, True])
def test_api_key_never_appears(cap, content):
    rec = _rec(input_text=f"my key is {API_KEY}", output_text=API_KEY, error_code=f"provider_error {API_KEY}")
    call_log.make_call_sink(workspace_id=WS_A, api_key=API_KEY, content_logging=content)(rec)
    assert API_KEY not in cap.lines[0]


def test_redactor_failure_writes_marker(cap, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("regex blew up")
    monkeypatch.setattr(call_log, "redact", boom)
    before = _errs("redact")
    call_log.make_call_sink(workspace_id=WS_A, api_key=API_KEY, content_logging=True)(_rec())
    line = _one(cap)
    assert line["input_text"] == "[redaction-failed]" and line["output_text"] == "[redaction-failed]"
    assert "hello world" not in cap.lines[0]
    assert _errs("redact") >= before + 1


@pytest.mark.parametrize("stage, target", [
    ("fingerprint", "fingerprint"),
    ("serialize", "_dumps"),
])
def test_stage_failures_never_raise_and_are_counted(cap, monkeypatch, stage, target):
    def boom(*a, **k):
        raise RuntimeError(f"{stage} down")
    monkeypatch.setattr(call_log, target, boom)
    before = _errs(stage)
    call_log.make_call_sink(workspace_id=WS_A, api_key=API_KEY, content_logging=False)(_rec())   # must not raise
    assert _errs(stage) == before + 1


def test_write_failure_never_raises_and_is_counted(monkeypatch):
    def boom(*a, **k):
        raise OSError("stdout closed")
    monkeypatch.setattr(call_log._log, "info", boom)
    before = _errs("write")
    call_log.make_call_sink(workspace_id=WS_A, api_key=API_KEY, content_logging=False)(_rec())
    assert _errs("write") == before + 1


def test_one_line_per_record(cap):
    sink = call_log.make_call_sink(workspace_id=WS_A, api_key=API_KEY, content_logging=False)
    for i in range(3):
        sink(replace(_rec(), call_seq=i))
    assert [json.loads(x)["call_seq"] for x in cap.lines] == [0, 1, 2]


def test_logger_is_isolated_from_root():
    lg = logging.getLogger("ac.call_log")
    assert lg.propagate is False and lg.level == logging.INFO
