"""H2 orchestration — run_h2 / _run_one / _assert_findings with the HTTP layer mocked (NO live calls).

Monkeypatches each adapter's stream opener (like test_providers) plus log_call, so the whole experiment
runs offline. Crucially: a divergence test proves the ±5% reconciliation is NO LONGER a tautology — if
cost.py regressed to double-counting reasoning, _assert_findings would fail (IMPORTANT #2)."""

import asyncio

import pytest

from experiments import h2_providers
from providers import ollama_adapter, openai_adapter

# Canned streamed responses: openai completion_tokens (400) is INCLUSIVE of reasoning (150).
_OPENAI_SSE = [
    'data: {"choices":[{"delta":{"content":"{\\"severity\\":\\"high\\",'
    '\\"summary\\":\\"x\\",\\"next_step\\":\\"y\\"}"}}]}',
    'data: {"choices":[],"usage":{"prompt_tokens":1200,"completion_tokens":400,'
    '"prompt_tokens_details":{"cached_tokens":300},'
    '"completion_tokens_details":{"reasoning_tokens":150}}}',
    "data: [DONE]",
]

_OLLAMA_NDJSON = [
    '{"message":{"role":"assistant","content":"{\\"severity\\":\\"high\\",'
    '\\"summary\\":\\"x\\",\\"next_step\\":\\"y\\"}"},"done":false}',
    '{"message":{"role":"assistant","content":""},"done":true,'
    '"prompt_eval_count":210,"eval_count":58}',
]


@pytest.fixture
def _mocked_http(monkeypatch):
    monkeypatch.setattr(openai_adapter, "_open_sse", lambda url, headers, body: iter(_OPENAI_SSE))
    monkeypatch.setattr(ollama_adapter, "_open_ndjson", lambda url, headers, body: iter(_OLLAMA_NDJSON))
    # keep the experiment from writing to mvp/logs/ during tests
    monkeypatch.setattr(h2_providers, "log_call", lambda *a, **k: None)


def test_run_h2_offline_passes_findings(_mocked_http):
    results = asyncio.run(h2_providers.run_h2(api_key="k"))

    by = {r["provider"]: r for r in results}
    assert set(by) == {"openai", "ollama"}

    # cost reconciled correctly (reasoning carved out): literal gpt-5-mini expected = 0.001518
    assert by["openai"]["cost"] == pytest.approx(0.001518, rel=0, abs=1e-12)
    assert by["ollama"]["cost"] == 0.0
    assert by["openai"]["usage"].output_tokens == 400
    assert by["ollama"]["usage"].output_tokens == 58

    # the Ollama reasoning downgrade is surfaced as a Substitution (not silent)
    assert "reasoning_control" in {s.feature for s in by["ollama"]["subs"]}


def test_run_one_shape(_mocked_http):
    base = h2_providers.build_request()
    r = asyncio.run(h2_providers._run_one("openai", "gpt-5-mini", base, "k"))
    assert r["provider"] == "openai" and r["model"] == "gpt-5-mini"
    assert r["output"]           # text was reassembled
    assert r["cost"] > 0.0
    assert r["usage"].reasoning_tokens == 150


def test_reconciliation_catches_double_counting(_mocked_http, monkeypatch):
    """IMPORTANT #2: if cost.py regressed to billing reasoning twice, the independent hand-calc would
    diverge beyond ±5% and _assert_findings would raise. This simulates that regression."""
    def _double_counting_cost(profile, u, *, batch=False):
        p = profile.pricing
        billable_input = u.input_tokens - u.cached_input_tokens - u.cache_write_tokens
        return (
            billable_input * p["input"]
            + u.cached_input_tokens * p.get("cached_input", p["input"])
            + u.cache_write_tokens * p.get("cache_write", p["input"])
            + u.output_tokens * p["output"]                       # BUG: reasoning not carved out
            + u.reasoning_tokens * p.get("reasoning", p["output"])
        ) / 1e6

    monkeypatch.setattr(h2_providers, "compute_cost", _double_counting_cost)
    with pytest.raises(AssertionError):
        asyncio.run(h2_providers.run_h2(api_key="k"))
