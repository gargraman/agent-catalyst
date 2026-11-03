"""Transport-seam tests (M1) — the single LiteLLM boundary. `litellm.acompletion` is monkeypatched; no
network, no API key use. These pin the code-review fixes F1 (usage must be requested) and F3/F5 (every
provider failure is wrapped as TransportError with a retryable hint, so the gateway can tell a provider
failure from a bug in our own code)."""
from __future__ import annotations

import asyncio

import litellm
import pytest

from runtime.providers import transport


def _drive(**kwargs):
    async def go():
        return [chunk async for chunk in transport.stream_chunks(**kwargs)]
    return asyncio.run(go())


def test_transport_requests_usage_and_keeps_params(monkeypatch):
    # F1: we MUST ask litellm for usage on the stream (OpenAI-style providers omit it otherwise), or cost
    # cannot be computed and every stream would read as degraded. drop_params stays False (R2).
    captured: dict = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)

        async def gen():
            if False:
                yield  # empty async iterator

        return gen()

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    _drive(model="gpt-5-mini", messages=[{"role": "user", "content": "hi"}], api_key="k")
    assert captured["stream"] is True
    assert captured["drop_params"] is False
    assert captured["stream_options"] == {"include_usage": True}


def test_transport_wraps_provider_error_as_transport_error(monkeypatch):
    # F3/F5: any litellm failure becomes a TransportError (no raw SDK type escapes the seam) with a
    # retryable hint classified from the status code.
    class FakeRateLimit(Exception):
        status_code = 429

    async def fake_acompletion(**kwargs):
        raise FakeRateLimit("slow down")

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    with pytest.raises(transport.TransportError) as ei:
        _drive(model="m", messages=[], api_key="k")
    assert ei.value.retryable is True and ei.value.status == 429


def test_transport_classifies_client_error_non_retryable(monkeypatch):
    class FakeBadRequest(Exception):
        status_code = 400

    async def fake_acompletion(**kwargs):
        raise FakeBadRequest("bad request")

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    with pytest.raises(transport.TransportError) as ei:
        _drive(model="m", messages=[], api_key="k")
    assert ei.value.retryable is False and ei.value.status == 400


def test_transport_classifies_statusless_connection_error_retryable(monkeypatch):
    # F5: a transient failure with no status code (class name signals connection/network) is retryable.
    class RemoteProtocolError(Exception):
        pass

    async def fake_acompletion(**kwargs):
        raise RemoteProtocolError("server disconnected")

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    with pytest.raises(transport.TransportError) as ei:
        _drive(model="m", messages=[], api_key="k")
    assert ei.value.retryable is True
