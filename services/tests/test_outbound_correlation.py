"""Feature 023 US2 (T023): only a PLATFORM-GENERATED id reaches the provider (FR-012/014, analysis U1).

Pure: the real gateway + adapter run; only the LiteLLM transport seam is stubbed and its kwargs captured.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from runtime.invoke import invoke_result
from runtime.providers import transport
from tests._invoke_helpers import JSON_IO, make_context, make_snapshot

pytestmark = pytest.mark.asyncio

HEX32 = re.compile(r"^[0-9a-f]{32}$")
P = "0123456789abcdef0123456789abcdef"
INPUTS = {"message": "go"}


def _chunks(text, *, request_id=None):
    hp = {"additional_headers": {"llm_provider-x-request-id": request_id}} if request_id else {}
    delta = SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))], usage=None,
                            _hidden_params=hp)
    usage = SimpleNamespace(prompt_tokens=5, completion_tokens=2, prompt_tokens_details=None,
                            completion_tokens_details=None)
    final = SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None))], usage=usage,
                            _hidden_params=hp)
    return [delta, final]


def _capture(monkeypatch, texts=("hi",), request_id=None):
    calls: list[dict] = []
    state = {"n": 0}

    async def _fake(**kwargs):
        calls.append(kwargs)
        t = texts[min(state["n"], len(texts) - 1)]
        state["n"] += 1
        for c in _chunks(t, request_id=request_id):
            yield c
    monkeypatch.setattr(transport, "stream_chunks", _fake)
    return calls


def _ctx(**kw):
    recs: list = []
    return make_context(record_call=recs.append, **kw), recs


async def test_caller_id_never_sent_platform_id_is(monkeypatch):
    calls = _capture(monkeypatch)
    ctx, recs = _ctx(correlation_id="demo-123", provider_correlation_id=P)
    res = await invoke_result(make_snapshot(), INPUTS, ctx)
    assert res.run.correlation_id == "demo-123"
    assert calls and all(c.get("extra_headers") == {"X-Correlation-Id": P} for c in calls)
    assert "demo-123" not in repr(calls)
    assert [r.provider_correlation_id for r in recs] == [P]
    assert recs[0].correlation_id == "demo-123"


async def test_no_ids_runtime_mints_one_used_everywhere(monkeypatch):
    calls = _capture(monkeypatch)
    ctx, recs = _ctx(correlation_id=None, provider_correlation_id=None)
    res = await invoke_result(make_snapshot(), INPUTS, ctx)
    cid = res.run.correlation_id
    assert HEX32.match(cid)
    assert calls[0]["extra_headers"] == {"X-Correlation-Id": cid}
    assert recs[0].correlation_id == recs[0].provider_correlation_id == cid


async def test_caller_id_without_edge_provider_id_gets_fresh_platform_id(monkeypatch):
    calls = _capture(monkeypatch)
    ctx, recs = _ctx(correlation_id="demo-123", provider_correlation_id=None)
    await invoke_result(make_snapshot(), INPUTS, ctx)
    sent = calls[0]["extra_headers"]["X-Correlation-Id"]
    assert HEX32.match(sent) and sent != "demo-123"
    assert recs[0].provider_correlation_id == sent


@pytest.mark.parametrize("bad", ["demo-123", "a" * 200, P[:-1] + "\n", P.upper()])
async def test_non_hex32_provider_id_header_omitted_call_still_made(monkeypatch, bad):
    calls = _capture(monkeypatch)
    ctx, _ = _ctx(correlation_id=None, provider_correlation_id=bad)
    res = await invoke_result(make_snapshot(), INPUTS, ctx)
    assert len(calls) == 1 and "extra_headers" not in calls[0]
    assert res.run.outcome.value == "success"


async def test_correction_turns_carry_same_id(monkeypatch):
    calls = _capture(monkeypatch, texts=("{bad", '{"severity": "low", "score": 1}'))
    ctx, recs = _ctx(correlation_id="demo-123", provider_correlation_id=P)
    res = await invoke_result(make_snapshot(io_contract=JSON_IO), INPUTS, ctx)
    assert res.run.correction_turns == 1 and len(calls) == 2
    assert all(c["extra_headers"] == {"X-Correlation-Id": P} for c in calls)
    assert [r.provider_correlation_id for r in recs] == [P, P]


async def test_provider_response_request_id_captured(monkeypatch):
    _capture(monkeypatch, request_id="req_OPENAI123")
    ctx, recs = _ctx(provider_correlation_id=P)
    await invoke_result(make_snapshot(), INPUTS, ctx)
    assert recs[0].provider_request_id == "req_OPENAI123"


async def test_provider_response_request_id_bounded(monkeypatch):
    _capture(monkeypatch, request_id="bad id\nwith newline")
    ctx, recs = _ctx(provider_correlation_id=P)
    await invoke_result(make_snapshot(), INPUTS, ctx)
    assert recs[0].provider_request_id is None


async def test_absent_response_id_is_none(monkeypatch):
    _capture(monkeypatch)
    ctx, recs = _ctx(provider_correlation_id=P)
    await invoke_result(make_snapshot(), INPUTS, ctx)
    assert recs[0].provider_request_id is None
