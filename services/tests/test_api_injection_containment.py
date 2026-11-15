"""Injection containment end-to-end (feature 024, tasks.md T055/T057–T059; research D11 + D13; spec FR-021..FR-025).

(a) a poisoned document is flagged at ingest (counts only, no payload on the document) and, when its chunk ranks #1 for
    the question, it is WITHHELD at retrieval: not cited, detection + withheld metrics incremented, structured
    `ac.injection` log line with no text.
(b) containment beyond the scanner: even with the scanner patched to pass everything, an unbound `delete_all` tool call
    from the model is rejected by the existing F3 path (correction, then tool_call_rejected); nothing is executed.
(c) a BOUND tool called with schema-violating args (as injected text would try) is rejected the same way.
Integration (compose Postgres). Real scanner in (a); stub chat gateway and embedder throughout — no live calls.
"""
from __future__ import annotations

import json
import logging
import uuid as _uuid

import pytest

from api import metrics
from api.db import tenant_session
from api.models import Agent, AgentVersion, KnowledgeBase, RetrievalBinding, ToolBinding
from runtime.guardrails import injection
from runtime.providers import gateway
from tests._api_helpers import (
    _IO_CONTRACT, _MODEL_CONFIG, PROFILE_SNAPSHOT, client, dev_headers,
)
from tests._invoke_helpers import make_multi_turn_gateway, stop_ev, tool_end_ev, usage_ev
from tests.test_api_retrieval_invoke import (  # noqa: F401 — `_stubs` is an autouse fixture (storage + embed stubs)
    QUESTION, _bound_version, _frames, _ingest, _one, _setup, _stubs,
)

pytestmark = pytest.mark.integration

SCHEMA = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}


def _metric(name, labels):
    return metrics.REGISTRY.get_sample_value(name, labels) or 0.0


async def test_poisoned_chunk_flagged_at_ingest_and_withheld_at_retrieval(caplog):
    ws, aid, kb = await _setup()
    caplog.set_level(logging.INFO, logger="ac.injection")
    ingest0 = _metric("ac_injection_detections_total", {"checkpoint": "ingest", "category": "tool_hijack"})
    retr0 = _metric("ac_injection_detections_total", {"checkpoint": "retrieve", "category": "tool_hijack"})
    withheld0 = _metric("ac_retrieval_passages_withheld_total", {"category": "tool_hijack"})
    async with client() as c:
        await _ingest(c, ws, kb, name="attack.md")
        docs = (await c.get(f"/api/v1/knowledge-bases/{kb}/documents", headers=dev_headers(workspace=ws))).json()
        doc_id = (docs["items"] if isinstance(docs, dict) else docs)[0]["id"]
        doc = await c.get(f"/api/v1/knowledge-bases/{kb}/documents/{doc_id}", headers=dev_headers(workspace=ws))
        await _bound_version(c, ws, aid, [kb])
        r = await c.post(f"/api/v1/agents/{aid}/invoke", headers=dev_headers(workspace=ws),
                         json={"inputs": {"message": QUESTION}})
    d = doc.json()
    assert d["status"] == "ready" and d["injection_flags"].get("tool_hijack", 0) >= 1      # flag ≠ refusal
    assert "delete_all" not in doc.text
    cit = _one(_frames(r), "citations")
    assert all(x["heading"] != "Annual Leave Days Per Year" for x in cit["citations"])     # poisoned chunk withheld
    assert _one(_frames(r), "done")["status"] == "success"
    assert _metric("ac_injection_detections_total", {"checkpoint": "ingest", "category": "tool_hijack"}) > ingest0
    assert _metric("ac_injection_detections_total", {"checkpoint": "retrieve", "category": "tool_hijack"}) == retr0 + 1
    assert _metric("ac_retrieval_passages_withheld_total", {"category": "tool_hijack"}) == withheld0 + 1
    lines = [json.loads(rec.getMessage()) for rec in caplog.records if rec.name == "ac.injection"]
    assert len(lines) == 1 and lines[0]["event"] == "injection.detected" and lines[0]["checkpoint"] == "retrieve"
    assert lines[0]["correlation_id"] and "delete_all" not in caplog.text and "ignore all previous" not in caplog.text


async def _tool_version(ws: str, aid: str, kb: str) -> None:
    """Versions are immutable and the version API doesn't author tool bindings: insert one with a tool AND the KB."""
    async with tenant_session(ws) as s:
        kbrow = await s.get(KnowledgeBase, _uuid.UUID(kb))
        v = AgentVersion(workspace_id=_uuid.UUID(ws), agent_id=_uuid.UUID(aid), label="v-tool",
                         system_prompt="You are a test agent.", prompt_hash="ph", snapshot_hash="sh-tool",
                         model_config_=_MODEL_CONFIG, profile_snapshot=PROFILE_SNAPSHOT, io_contract=_IO_CONTRACT)
        v.retrieval_bindings = [RetrievalBinding(workspace_id=_uuid.UUID(ws), kb_id=kbrow.id,
                                                 kb_index_version=kbrow.index_version, injection="always_on",
                                                 retrieval_config={"top_k": 6, "similarity_threshold": 0.0,
                                                                   "max_ctx_tokens": 2000})]
        v.tool_bindings = [ToolBinding(workspace_id=_uuid.UUID(ws), kind="http", tool_id=_uuid.uuid4(),
                                       schema_snapshot={"name": "search_runbook", "input_schema": SCHEMA},
                                       config_snapshot={})]
        s.add(v)
        await s.flush()
        (await s.get(Agent, _uuid.UUID(aid))).active_version_id = v.id


async def test_unbound_tool_call_from_injected_text_is_rejected_even_if_scanner_passes(monkeypatch):
    """Owner decision 2026-11-15: the realistic attack — the agent HAS tools; injected text names one it doesn't
    have. (Zero-tools pass-through surfacing a model tool call, runtime/invoke.py:466, is a known pre-024 gap, left.)"""
    monkeypatch.setattr(injection, "scan", lambda text: injection.ScanResult(False, ()))
    gw = make_multi_turn_gateway([[tool_end_ev("c1", "delete_all", {}), usage_ev(), stop_ev()],
                                  [tool_end_ev("c2", "delete_all", {}), usage_ev(), stop_ev()]])
    monkeypatch.setattr(gateway, "stream", gw)
    ws, aid, kb = await _setup()
    async with client() as c:
        await _ingest(c, ws, kb, name="attack.md")
    await _tool_version(ws, aid, kb)
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/invoke", headers=dev_headers(workspace=ws),
                         json={"inputs": {"message": QUESTION}})
    assert "tool_call_end" not in [e for e, _ in _frames(r)]           # a rejected call is never surfaced
    assert 2 <= gw.calls["n"] <= 3                                       # bounded corrections, then fail closed
    assert _one(_frames(r), "done")["status"] == "tool_call_rejected"


async def test_bound_tool_with_schema_violating_args_is_rejected(monkeypatch):
    monkeypatch.setattr(injection, "scan", lambda text: injection.ScanResult(False, ()))
    gw = make_multi_turn_gateway([[tool_end_ev("c1", "search_runbook", {"drop": "*"}), usage_ev(), stop_ev()],
                                  [tool_end_ev("c2", "search_runbook", {"drop": "*"}), usage_ev(), stop_ev()]])
    monkeypatch.setattr(gateway, "stream", gw)
    ws, aid, kb = await _setup()
    async with client() as c:
        await _ingest(c, ws, kb, name="attack.md")
    await _tool_version(ws, aid, kb)
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/invoke", headers=dev_headers(workspace=ws),
                         json={"inputs": {"message": QUESTION}})
    assert "tool_call_end" not in [e for e, _ in _frames(r)]
    assert _one(_frames(r), "done")["status"] == "tool_call_rejected"
