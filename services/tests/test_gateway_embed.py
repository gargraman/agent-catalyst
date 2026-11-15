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


# ======================================================================================================================
# Feature 025 (US1, T010) — per-batch bounded retry on the embedding path (contract runtime-resilience §5).
# Injected Guard: sleep advances a fake clock (0 wall time). Vectors are appended only after a batch succeeds, so a
# retried batch can never duplicate or reorder vectors.
# ======================================================================================================================
import random  # noqa: E402

from runtime.providers import resilience  # noqa: E402


class _Clock:
    def __init__(self):
        self.t, self.waits = 0.0, []

    def now(self):
        return self.t

    async def sleep(self, s):
        self.waits.append(s)
        self.t += s


def _guard025(clock=None, **policy):
    clock = clock or _Clock()
    return resilience.Guard(policy=resilience.ResiliencePolicy(**policy), clock=clock.now, sleep=clock.sleep,
                            rng=random.Random(3))


def _patch_flaky(monkeypatch, *, calls: list, fail_on: dict[int, TransportError]):
    """Call #i (0-based, over ALL transport calls) raises fail_on[i]; others return order-encoding vectors."""
    async def _fake_embed(**kwargs):
        idx = len(calls)
        calls.append(kwargs)
        if idx in fail_on:
            raise fail_on[idx]
        inp = kwargs["input"]
        return SimpleNamespace(
            data=[{"index": i, "embedding": [float(int(t.split("-")[1]))] * 1024} for i, t in enumerate(inp)],
            usage=SimpleNamespace(prompt_tokens=10 * len(inp)), _hidden_params={})
    monkeypatch.setattr(transport, "embed", _fake_embed)


def _transient():
    return TransportError("injected 503", retryable=True, status=503)


async def test_attempt_transient_batch_failure_is_retried_once(monkeypatch):
    calls: list = []
    _patch_flaky(monkeypatch, calls=calls, fail_on={0: _transient()})
    res = await gateway.embed(["t-0", "t-1"], provider="openai", model="text-embedding-3-small", api_key=_KEY,
                              guard=_guard025())
    assert len(calls) == 2
    assert [v[0] for v in res.vectors] == [0.0, 1.0]
    assert res.attempts == 2
    assert res.usage.input_tokens == 20          # usage only from the SUCCESSFUL batch response (FR-015 basis)


async def test_attempt_retry_mid_batches_keeps_order_and_no_duplicates(monkeypatch):
    calls: list = []
    # 250 texts → batches of 100/100/50. Transport call #1 (= batch 2, first try) fails transiently.
    _patch_flaky(monkeypatch, calls=calls, fail_on={1: _transient()})
    texts = [f"t-{i}" for i in range(250)]
    res = await gateway.embed(texts, provider="openai", model="text-embedding-3-small", api_key=_KEY,
                              guard=_guard025())
    assert [len(c["input"]) for c in calls] == [100, 100, 100, 50]
    assert [v[0] for v in res.vectors] == [float(i) for i in range(250)]   # exactly once, input order
    assert res.attempts == 4                                                # summed over batches: 1 + 2 + 1
    assert res.usage.input_tokens == 2500


async def test_attempt_exhaustion_raises_typed_error_with_attempts(monkeypatch):
    calls: list = []
    _patch_flaky(monkeypatch, calls=calls, fail_on={i: _transient() for i in range(10)})
    with pytest.raises(EmbeddingError) as ei:
        await gateway.embed(["t-0"], provider="openai", model="text-embedding-3-small", api_key=_KEY,
                            guard=_guard025())
    assert len(calls) == 3
    assert ei.value.type == "provider_error" and ei.value.retryable is True and ei.value.attempts == 3


async def test_attempt_non_transient_is_not_retried(monkeypatch):
    calls: list = []
    _patch_flaky(monkeypatch, calls=calls, fail_on={0: TransportError("bad key", retryable=False, status=401)})
    clock = _Clock()
    with pytest.raises(EmbeddingError) as ei:
        await gateway.embed(["t-0"], provider="openai", model="text-embedding-3-small", api_key=_KEY,
                            guard=_guard025(clock))
    assert len(calls) == 1 and clock.waits == []
    assert ei.value.retryable is False and ei.value.attempts == 1


async def test_attempt_clean_embed_reports_one_attempt_per_batch(monkeypatch):
    calls: list = []
    _patch_flaky(monkeypatch, calls=calls, fail_on={})
    texts = [f"t-{i}" for i in range(150)]
    res = await gateway.embed(texts, provider="openai", model="text-embedding-3-small", api_key=_KEY,
                              guard=_guard025())
    assert res.attempts == 2          # 2 batches, no retries → Σ(attempts) = 2, retries contribution = 0


async def test_attempt_huge_retry_after_is_capped_on_embed(monkeypatch):
    calls: list = []
    err = _transient()
    err.retry_after = 3600.0
    _patch_flaky(monkeypatch, calls=calls, fail_on={0: err})
    clock = _Clock()
    await gateway.embed(["t-0"], provider="openai", model="text-embedding-3-small", api_key=_KEY,
                        guard=_guard025(clock))
    assert clock.waits == [8.0]


def test_attempt_ir_defaults_are_backward_compatible():
    assert EmbeddingError("x", type="provider_error").attempts == 1
    res = EmbeddingResult(vectors=(), usage=NormalizedUsage(0, 0), cost_usd=Decimal("0"), cost_is_estimate=False)
    assert res.attempts == 1
