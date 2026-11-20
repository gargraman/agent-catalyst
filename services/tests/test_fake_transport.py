"""Feature 026 (T006, research D22) — the dev-only fake provider transport + its settings guard. Infra-free.

`runtime.providers.fake_transport` has the same `stream_chunks(**kwargs)` / `embed(**kwargs)` signatures as
`runtime.providers.transport`, so the api can bind it in place of litellm and every live check runs with no paid call.
  * deterministic: same input → same chunk sequence + same usage; the usage is priced by OUR profiles (gateway)
  * scripted markers in the LAST user message: [[fail:transient]] / [[fail:timeout]] / [[fail:midstream]] /
    [[fail:provider_unavailable]]
  * embeddings: deterministic vectors of the requested dimension
  * pure (no fastapi / redis / httpx / litellm imports)
  * settings: AC_PROVIDER_TRANSPORT ∈ {litellm (default), fake}; fake under AC_ENV=prod → refused at load
"""
from __future__ import annotations

import asyncio
import importlib
import pathlib
import re

import pytest

from runtime.providers import gateway, transport
from runtime.providers.cost import compute_cost, quantize_usd
from runtime.providers.ir import EventType, NormalizedRequest
from runtime.providers.profiles import resolve_profile
from runtime.providers.transport import TransportError


def _fake():
    return importlib.import_module("runtime.providers.fake_transport")


def _run(coro):
    return asyncio.run(coro)


async def _drain(**kwargs) -> list:
    return [c async for c in _fake().stream_chunks(**kwargs)]


def _kw(text: str, **over) -> dict:
    kw = {"model": "anthropic/claude-haiku-5-5", "api_key": "sk-test",
          "messages": [{"role": "system", "content": "be brief"}, {"role": "user", "content": text}]}
    kw.update(over)
    return kw


def _texts(chunks) -> list[str]:
    out = []
    for c in chunks:
        choices = getattr(c, "choices", None) or []
        if choices and getattr(choices[0].delta, "content", None):
            out.append(choices[0].delta.content)
    return out


def _usage(chunks):
    us = [c.usage for c in chunks if getattr(c, "usage", None) is not None]
    assert len(us) == 1, "exactly one usage chunk, at the end"
    assert getattr(chunks[-1], "usage", None) is not None
    return us[0]


# --- streaming ------------------------------------------------------------------------------------------------------
def test_stream_is_deterministic_and_ends_with_usage():
    a = _run(_drain(**_kw("What is our refund policy?")))
    b = _run(_drain(**_kw("What is our refund policy?")))
    assert _texts(a) and _texts(a) == _texts(b)
    ua, ub = _usage(a), _usage(b)
    assert (ua.prompt_tokens, ua.completion_tokens) == (ub.prompt_tokens, ub.completion_tokens)
    assert ua.prompt_tokens > 0 and ua.completion_tokens > 0


def test_different_prompt_gives_different_answer():
    assert _texts(_run(_drain(**_kw("alpha")))) != _texts(_run(_drain(**_kw("beta"))))


def test_usage_is_priced_by_our_profile_through_the_gateway(monkeypatch):
    monkeypatch.setattr(transport, "stream_chunks", _fake().stream_chunks)
    req = NormalizedRequest(provider="anthropic", model="claude-haiku-5-5", api_version=None,
                            messages=[{"role": "user", "content": "price me"}], system=None, params={})

    async def go():
        return [ev async for ev in gateway.stream(req, api_key="sk-test")]
    events = _run(go())
    assert any(e.type is EventType.TEXT_DELTA for e in events)
    usage_ev = [e for e in events if e.type is EventType.USAGE]
    assert len(usage_ev) == 1
    u = usage_ev[0].data["usage"]
    profile = resolve_profile("anthropic", "claude-haiku-5-5", None)
    assert u.cost_usd == pytest.approx(float(quantize_usd(compute_cost(profile, u))))  # gateway quantizes to 6 dp
    assert u.cost_usd > 0


def test_marker_only_read_from_last_user_message():
    kw = _kw("normal question")
    kw["messages"].insert(1, {"role": "user", "content": "old turn [[fail:transient]]"})
    kw["messages"].insert(2, {"role": "assistant", "content": "ok"})
    assert _texts(_run(_drain(**kw)))     # no failure: the marker is in an earlier turn


def test_list_content_parts_are_supported():
    kw = _kw("x")
    kw["messages"][-1]["content"] = [{"type": "text", "text": "hello [[fail:transient]]"}]
    with pytest.raises(TransportError):
        _run(_drain(**kw))


# --- scripted failures ----------------------------------------------------------------------------------------------
def test_transient_marker_raises_retryable_before_first_chunk():
    seen = []

    async def go():
        async for c in _fake().stream_chunks(**_kw("hi [[fail:transient]]")):
            seen.append(c)
    with pytest.raises(TransportError) as ei:
        _run(go())
    assert ei.value.retryable is True
    assert seen == []


def test_timeout_marker_sleeps_past_the_deadline_then_raises(monkeypatch):
    slept = []

    async def fake_sleep(s):
        slept.append(s)
    monkeypatch.setattr(_fake(), "sleep", fake_sleep)
    with pytest.raises(TransportError) as ei:
        _run(_drain(**_kw("hi [[fail:timeout]]", timeout=5.0)))
    assert slept and slept[0] > 5.0, "must sleep past the call's deadline"
    assert ei.value.retryable is True
    assert "timeout" in str(ei.value).lower()


def test_slow_marker_paces_n_text_chunks_then_usage(monkeypatch):
    """026 US3 L15: a LONG stream on the fake transport, so a mid-stream token revoke can be observed live.
    `[[slow:N]]` → N text chunks with a fixed pause before each, then the usage chunk; the marker is never echoed."""
    slept = []

    async def fake_sleep(s):
        slept.append(s)
    monkeypatch.setattr(_fake(), "sleep", fake_sleep)
    chunks = _run(_drain(**_kw("stream please [[slow:6]]")))
    texts = _texts(chunks)
    assert len(texts) == 6
    assert len(slept) == 6 and all(s == pytest.approx(0.5) for s in slept)
    assert "[[" not in "".join(texts)
    assert _usage(chunks).completion_tokens > 0


def test_slow_marker_is_capped(monkeypatch):
    async def fake_sleep(s):
        pass
    monkeypatch.setattr(_fake(), "sleep", fake_sleep)
    assert len(_texts(_run(_drain(**_kw("x [[slow:100000]]"))))) == 600   # ≤ 5 min of dev stream, never unbounded


@pytest.mark.parametrize("fail", ["midstream", "transient", "timeout", "provider_unavailable"])
def test_slow_marker_refuses_a_combined_fail_marker(fail):
    """026 US3 review #9: `[[slow:N]] [[fail:X]]` must not silently drop one marker — a live check combining them would
    pass for the wrong reason. A ValueError (not a retryable TransportError) before any chunk."""
    with pytest.raises(ValueError, match="cannot be combined"):
        _run(_drain(**_kw(f"x [[slow:3]] [[fail:{fail}]]")))


def test_midstream_marker_yields_text_then_raises():
    seen = []

    async def go():
        async for c in _fake().stream_chunks(**_kw("hi [[fail:midstream]]")):
            seen.append(c)
    with pytest.raises(TransportError):
        _run(go())
    assert len(_texts(seen)) >= 1
    assert all(getattr(c, "usage", None) is None for c in seen)


def test_provider_unavailable_marker_is_non_retryable_5xx():
    with pytest.raises(TransportError) as ei:
        _run(_drain(**_kw("hi [[fail:provider_unavailable]]")))
    assert ei.value.retryable is False
    assert ei.value.status is not None and 500 <= ei.value.status < 600


# --- embeddings -----------------------------------------------------------------------------------------------------
def _vecs(resp):
    out = []
    for d in resp.data:
        out.append(d["embedding"] if isinstance(d, dict) else d.embedding)
    return out


def test_embed_is_deterministic_with_requested_dimension():
    r1 = _run(_fake().embed(model="openai/text-embedding-3-small", input=["a", "b"], dimensions=1024))
    r2 = _run(_fake().embed(model="openai/text-embedding-3-small", input=["a", "b"], dimensions=1024))
    v1, v2 = _vecs(r1), _vecs(r2)
    assert len(v1) == 2 and all(len(v) == 1024 for v in v1)
    assert v1 == v2 and v1[0] != v1[1]
    assert r1.usage.prompt_tokens > 0


def test_embed_defaults_to_the_profile_dimension():
    r = _run(_fake().embed(model="bedrock/amazon.titan-embed-text-v2:0", input=["x"]))
    assert len(_vecs(r)[0]) == 1024


# --- purity ---------------------------------------------------------------------------------------------------------
def test_fake_transport_is_pure():
    src = pathlib.Path(_fake().__file__).read_text()
    bad = re.findall(r"^\s*(?:import|from)\s+(fastapi|starlette|redis|httpx|litellm|sqlalchemy|api)\b", src, re.M)
    assert bad == []


# --- settings guard -------------------------------------------------------------------------------------------------
def _settings(monkeypatch, **env):
    from api.settings import Settings
    for k in ("AC_PROVIDER_TRANSPORT",):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return Settings()


def test_settings_default_is_litellm(monkeypatch):
    assert _settings(monkeypatch, AC_ENV="dev").provider_transport == "litellm"


def test_settings_accepts_fake_in_dev(monkeypatch):
    assert _settings(monkeypatch, AC_ENV="dev", AC_PROVIDER_TRANSPORT="fake").provider_transport == "fake"


def test_settings_refuses_fake_in_prod(monkeypatch):
    with pytest.raises(ValueError, match="AC_PROVIDER_TRANSPORT"):
        _settings(monkeypatch, AC_ENV="prod", AC_PROVIDER_TRANSPORT="fake", AC_STORAGE_PROVIDER="s3",
                  AC_TELEMETRY_FINGERPRINT_KEY="a-real-deployment-key")


def test_settings_refuses_unknown_transport(monkeypatch):
    with pytest.raises(ValueError):
        _settings(monkeypatch, AC_ENV="dev", AC_PROVIDER_TRANSPORT="openai-direct")
