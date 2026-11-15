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


def test_transport_pins_litellm_to_local_cost_map():
    # Importing the transport module MUST force litellm's bundled local cost map, so the seam never makes a
    # surprise outbound fetch of the remote model_prices map on import. We compute cost ourselves (P7), so
    # litellm's map is unused anyway; fetching it adds cold-start latency + egress and fails in an air-gapped
    # pilot. The module sets this before its lazy `import litellm`, so the production import never fetches.
    import os

    assert os.environ.get("LITELLM_LOCAL_MODEL_COST_MAP") == "True"


def test_transport_bypasses_litellm_mcp_gateway_when_tools_are_bound(monkeypatch):
    """G8 (feature 024 research D9, reproduced live 2026-11-14): litellm 1.106.x imports its MCP gateway handler —
    which needs the uninstalled `mcp` package — whenever `tools` is non-empty, so every tool-bound agent failed against
    a real provider. Tool execution is ours (F3 trust boundary); we never want litellm's MCP gateway. The private
    `_skip_mcp_handler` kwarg is pinned here; re-verify on any litellm bump."""
    captured: dict = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)

        async def gen():
            if False:
                yield

        return gen()

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    tools = [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}]
    _drive(model="gpt-5-mini", messages=[{"role": "user", "content": "hi"}], api_key="k", tools=tools)
    assert captured.get("_skip_mcp_handler") is True
    assert captured["tools"] == tools                     # tools still forwarded to the provider


# ---------------------------------------------------------------------------------------------------------------
# Feature 025 — foundational transport changes (tasks T003–T005; contract runtime-resilience §6, research G1/G2/D4)
# ---------------------------------------------------------------------------------------------------------------

def _empty_acompletion(captured: dict):
    async def fake_acompletion(**kwargs):
        captured.update(kwargs)

        async def gen():
            if False:
                yield

        return gen()
    return fake_acompletion


def test_stream_disables_hidden_sdk_and_litellm_retries(monkeypatch):
    """T003 / G1: litellm's OpenAI path defaults the SDK to max_retries=2, so today a 429 is silently retried
    INSIDE the SDK — invisible to our run record and multiplied by any retry loop of ours. The seam must force both
    the SDK (`max_retries`) and litellm's own (`num_retries`) retries to 0; resilience lives in the gateway."""
    captured: dict = {}
    monkeypatch.setattr(litellm, "acompletion", _empty_acompletion(captured))
    _drive(model="gpt-5-mini", messages=[{"role": "user", "content": "hi"}], api_key="k")
    assert captured["max_retries"] == 0
    assert captured["num_retries"] == 0


def test_stream_retry_kwargs_are_setdefault_not_overwrite(monkeypatch):
    # setdefault, so an explicit caller value stays observable (and testable) rather than silently clobbered.
    captured: dict = {}
    monkeypatch.setattr(litellm, "acompletion", _empty_acompletion(captured))
    _drive(model="m", messages=[], api_key="k", max_retries=5, num_retries=4)
    assert captured["max_retries"] == 5 and captured["num_retries"] == 4


def test_embed_disables_hidden_sdk_and_litellm_retries(monkeypatch):
    captured: dict = {}

    async def fake_aembedding(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(litellm, "aembedding", fake_aembedding)
    asyncio.run(transport.embed(model="openai/text-embedding-3-small", input=["a"], api_key="k"))
    assert captured["max_retries"] == 0
    assert captured["num_retries"] == 0


# T004 / G2: Retry-After lives on the httpx response (`exc.response.headers`); litellm deliberately does NOT copy it
# onto `exc.headers` (litellm/exceptions.py RateLimitError). Valid hint -> seconds (float); anything unusable -> None
# (the gateway then falls back to its backoff schedule — never an infinite, negative, or NaN wait).
_NOW = 1_800_000_000.0  # fixed epoch seconds for HTTP-date cases


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("7", 7.0),
        ("1.5", 1.5),
        (" 12 ", 12.0),
        ("Fri, 15 Jan 2027 08:00:30 GMT", 30.0),   # HTTP-date 30 s after _NOW (2027-01-15T08:00:00Z)
        ("Fri, 15 Jan 2027 07:59:00 GMT", None),   # HTTP-date in the past
        ("0", None),
        ("-3", None),
        ("nan", None),
        ("inf", None),
        ("soon", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_retry_after(raw, expected):
    import datetime as _dt

    # Guard the fixture itself: _NOW must be exactly 2027-01-15T08:00:00Z for the HTTP-date rows to mean what they say.
    assert _dt.datetime.fromtimestamp(_NOW, _dt.timezone.utc) == _dt.datetime(2027, 1, 15, 8, 0, 0,
                                                                              tzinfo=_dt.timezone.utc)
    got = transport.parse_retry_after(raw, now=_NOW)
    assert got == expected


class _FakeResponse:
    def __init__(self, headers):
        self.headers = headers


def _raise_with(exc):
    async def fake_acompletion(**kwargs):
        raise exc
    return fake_acompletion


def test_transport_error_carries_retry_after_from_response_headers(monkeypatch):
    class FakeRateLimit(Exception):
        status_code = 429

    exc = FakeRateLimit("slow down")
    exc.response = _FakeResponse({"Retry-After": "7"})           # case-insensitive lookup
    monkeypatch.setattr(litellm, "acompletion", _raise_with(exc))
    with pytest.raises(transport.TransportError) as ei:
        _drive(model="m", messages=[], api_key="k")
    assert ei.value.retryable is True and ei.value.retry_after == 7.0


def test_transport_error_retry_after_none_without_hint(monkeypatch):
    # Both "no response at all" and litellm's synthesized minimal response (no retry-after header) -> None.
    class FakeServerError(Exception):
        status_code = 503

    bare = FakeServerError("down")
    synthesized = FakeServerError("down")
    synthesized.response = _FakeResponse({})
    for exc in (bare, synthesized):
        monkeypatch.setattr(litellm, "acompletion", _raise_with(exc))
        with pytest.raises(transport.TransportError) as ei:
            _drive(model="m", messages=[], api_key="k")
        assert ei.value.retry_after is None


def test_transport_error_default_retry_after_is_none():
    assert transport.TransportError("x").retry_after is None


# T005 / D4: 409 Conflict is a deterministic client-side condition, not a transient provider failure — it must not be
# retried or count toward the circuit breaker. 408/429/5xx and status-less network failures stay retryable.
@pytest.mark.parametrize("status, retryable", [(409, False), (408, True), (429, True), (500, True), (503, True)])
def test_is_retryable_by_status(status, retryable):
    class E(Exception):
        status_code = status

    assert transport._is_retryable(E()) is retryable


@pytest.mark.parametrize("name", ["Timeout", "APIConnectionError"])
def test_is_retryable_by_name_hint(name):
    exc_type = type(name, (Exception,), {})
    assert transport._is_retryable(exc_type()) is True
