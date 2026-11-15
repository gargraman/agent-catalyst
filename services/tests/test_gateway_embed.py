"""Gateway embedding path (feature 024, tasks.md T007; contracts/runtime.md §8; research D2).

`gateway.embed(texts, *, provider, model, api_key, timeout=None) -> EmbeddingResult`:
  resolve the EMBEDDING profile (fail closed on unknown / chat model) → batch by `max_batch` → transport.embed →
  usage from `usage.prompt_tokens` → cost from OUR profile (never litellm's response_cost) → quantize ONCE →
  validate each vector's length == profile `dimension` → normalize transport errors (key scrubbed).
NO live calls: `runtime.providers.transport.embed` is replaced with a stub shaped like the T005 spike fixture
(`tests/fixtures/litellm_embedding_response.json`: data items are dicts; request id in _hidden_params headers).
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from runtime.providers import gateway, transport
from runtime.providers.cost import compute_cost, quantize_usd
from runtime.providers.ir import EmbeddingError, EmbeddingResult, NormalizedUsage
from runtime.providers.profiles import resolve_profile
from runtime.providers.transport import TransportError

_KEY = "sk-test-SECRET-123"
_LITELLM_COST_SENTINEL = 987.654321


def _resp(n: int, *, tokens_per_text: int = 10, dim: int = 1024, request_id: str | None = "req_embed_123"):
    headers = {"llm_provider-x-request-id": request_id} if request_id else {}
    return SimpleNamespace(
        data=[{"object": "embedding", "index": i, "embedding": [0.001 * (i + 1)] * dim} for i in range(n)],
        usage=SimpleNamespace(prompt_tokens=tokens_per_text * n, total_tokens=tokens_per_text * n, completion_tokens=0,
                              cost=_LITELLM_COST_SENTINEL),
        _hidden_params={"additional_headers": headers, "response_cost": _LITELLM_COST_SENTINEL},
    )


def _patch(monkeypatch, *, calls: list, make=None, raise_exc: Exception | None = None):
    async def _fake_embed(**kwargs):
        calls.append(kwargs)
        if raise_exc is not None:
            raise raise_exc
        inp = kwargs["input"]
        if make is not None:
            return make(inp)
        # encode the input position into the vector so ordering across batches is checkable
        return SimpleNamespace(
            data=[{"index": i, "embedding": [float(int(t.split("-")[1]))] * 1024} for i, t in enumerate(inp)],
            usage=SimpleNamespace(prompt_tokens=10 * len(inp), total_tokens=10 * len(inp), completion_tokens=0,
                                  cost=_LITELLM_COST_SENTINEL),
            _hidden_params={"additional_headers": {}, "response_cost": _LITELLM_COST_SENTINEL},
        )
    monkeypatch.setattr(transport, "embed", _fake_embed)


def _profile():
    return resolve_profile("openai", "text-embedding-3-small")


async def test_embed_two_texts_returns_vectors_usage_and_our_cost(monkeypatch):
    calls: list = []
    _patch(monkeypatch, calls=calls, make=lambda inp: _resp(len(inp), tokens_per_text=25_000))
    res = await gateway.embed(["a", "b"], provider="openai", model="text-embedding-3-small", api_key=_KEY)
    assert isinstance(res, EmbeddingResult)
    assert len(res.vectors) == 2 and all(len(v) == 1024 for v in res.vectors)
    assert res.usage.input_tokens == 50_000 and res.usage.output_tokens == 0
    expected = quantize_usd(compute_cost(_profile(), NormalizedUsage(50_000, 0)))
    assert isinstance(res.cost_usd, Decimal) and res.cost_usd == expected == Decimal("0.001000")
    assert res.cost_is_estimate is True                     # profile is provisional
    assert res.provider_request_id == "req_embed_123"
    assert len(calls) == 1 and calls[0]["input"] == ["a", "b"]
    assert calls[0]["model"].endswith("text-embedding-3-small") and calls[0]["api_key"] == _KEY


async def test_litellm_cost_is_never_read(monkeypatch):
    calls: list = []
    _patch(monkeypatch, calls=calls, make=lambda inp: _resp(len(inp), tokens_per_text=10))
    res = await gateway.embed(["only"], provider="openai", model="text-embedding-3-small", api_key=_KEY)
    assert res.cost_usd != quantize_usd(_LITELLM_COST_SENTINEL)
    assert res.cost_usd == quantize_usd(compute_cost(_profile(), NormalizedUsage(10, 0)))


async def test_batching_by_profile_max_batch_preserves_order_and_quantizes_once(monkeypatch):
    calls: list = []
    _patch(monkeypatch, calls=calls)
    texts = [f"t-{i}" for i in range(250)]
    res = await gateway.embed(texts, provider="openai", model="text-embedding-3-small", api_key=_KEY)
    assert [len(c["input"]) for c in calls] == [100, 100, 50]          # max_batch = 100 from the profile
    assert [v[0] for v in res.vectors] == [float(i) for i in range(250)]  # input order across batches
    assert res.usage.input_tokens == 2500
    assert res.cost_usd == quantize_usd(compute_cost(_profile(), NormalizedUsage(2500, 0)))


@pytest.mark.parametrize("model", ["nope-embedding", "gpt-5"])   # unknown model; a CHAT model is not an embedder
async def test_unknown_or_chat_model_fails_closed(monkeypatch, model):
    calls: list = []
    _patch(monkeypatch, calls=calls)
    with pytest.raises(FileNotFoundError):
        await gateway.embed(["x"], provider="openai", model=model, api_key=_KEY)
    assert calls == [], "no transport call may be made for an unresolvable embedding model"


async def test_dimension_mismatch_is_a_typed_error(monkeypatch):
    calls: list = []
    _patch(monkeypatch, calls=calls, make=lambda inp: _resp(len(inp), dim=1536))
    with pytest.raises(EmbeddingError) as ei:
        await gateway.embed(["x"], provider="openai", model="text-embedding-3-small", api_key=_KEY)
    assert ei.value.type == "dimension_mismatch"


@pytest.mark.parametrize("retryable", [True, False])
async def test_transport_error_normalized_and_key_scrubbed(monkeypatch, retryable):
    calls: list = []
    boom = TransportError(f"upstream said no for key {_KEY}", retryable=retryable, status=503 if retryable else 401)
    _patch(monkeypatch, calls=calls, raise_exc=boom)
    with pytest.raises(EmbeddingError) as ei:
        await gateway.embed(["x"], provider="openai", model="text-embedding-3-small", api_key=_KEY)
    assert ei.value.retryable is retryable
    assert isinstance(ei.value.type, str) and ei.value.type
    assert _KEY not in str(ei.value) and _KEY not in repr(ei.value.args)


async def test_missing_request_id_is_none(monkeypatch):
    calls: list = []
    _patch(monkeypatch, calls=calls, make=lambda inp: _resp(len(inp), request_id=None))
    res = await gateway.embed(["x"], provider="openai", model="text-embedding-3-small", api_key=_KEY)
    assert res.provider_request_id is None


# ---- decision 2026-11-14 (T083): 1024 dims for every provider; Bedrock for customer data ---------------------------
async def test_openai_call_requests_the_deployment_dimension(monkeypatch):
    calls: list = []
    _patch(monkeypatch, calls=calls, make=lambda inp: _resp(len(inp)))
    await gateway.embed(["x"], provider="openai", model="text-embedding-3-small", api_key=_KEY)
    assert calls[0]["dimensions"] == 1024          # profile declares features.dimensions_param


async def test_bedrock_titan_uses_region_and_no_api_key(monkeypatch):
    calls: list = []
    _patch(monkeypatch, calls=calls, make=lambda inp: _resp(len(inp)))
    res = await gateway.embed(["x", "y"], provider="bedrock", model="amazon.titan-embed-text-v2:0", api_key=None,
                              region="eu-west-1")
    assert len(res.vectors) == 2 and all(len(v) == 1024 for v in res.vectors)
    c = calls[0]
    assert c["model"] == "bedrock/amazon.titan-embed-text-v2:0"
    assert c["aws_region_name"] == "eu-west-1" and c["dimensions"] == 1024
    assert "api_key" not in c, "Bedrock authenticates with the AWS credential chain; no api key is ever sent"
    assert res.cost_is_estimate is True


async def test_bedrock_dimension_mismatch_is_typed(monkeypatch):
    calls: list = []
    _patch(monkeypatch, calls=calls, make=lambda inp: _resp(len(inp), dim=1536))
    with pytest.raises(EmbeddingError) as ei:
        await gateway.embed(["x"], provider="bedrock", model="amazon.titan-embed-text-v2:0", api_key=None, region="us-east-1")
    assert ei.value.type == "dimension_mismatch"
