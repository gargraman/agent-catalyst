"""Dev-only FAKE provider transport (feature 026, research D22) — zero paid calls, fully deterministic.

Same `stream_chunks(**kwargs)` / `embed(**kwargs)` signatures as `runtime.providers.transport`, returning the same
litellm-SHAPED objects, so the api can bind it in place of litellm (`AC_PROVIDER_TRANSPORT=fake`) and the whole
playground → run → eval loop runs end-to-end with real adapters, real gateway resilience and OUR cost profiles —
only the bytes-on-the-wire are scripted. Refused under `AC_ENV=prod` by `api.settings`.

Determinism: the answer + token counts derive from a sha256 of (model, last user message). Same input → same output.

Scripted failure markers — read ONLY from the LAST user message (so an old turn can't poison a conversation):
  [[fail:transient]]             retryable TransportError (status 503) before the first chunk
  [[fail:timeout]]               sleeps past the call's `timeout`, then a retryable "timeout" TransportError
  [[fail:midstream]]             yields some text (no usage), then a retryable TransportError
  [[fail:provider_unavailable]]  NON-retryable TransportError, status 502

Pure: stdlib + runtime.providers only (guarded by tests/test_fake_transport.py + test_runtime_purity.py).
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import re
import struct
from types import SimpleNamespace
from typing import Any, AsyncIterator

from runtime.providers.transport import TransportError

sleep = asyncio.sleep          # module attribute so tests can monkeypatch the timeout path without real waiting

_MARKER = re.compile(r"\[\[fail:([a-z_]+)\]\]")
_WORDS = ("the", "policy", "agent", "answer", "is", "based", "on", "your", "question", "and", "context",
          "we", "recommend", "checking", "details", "carefully", "result", "summary", "for", "this", "case")


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


def _last_user(messages: list[dict] | None) -> str:
    for m in reversed(messages or []):
        if m.get("role") == "user":
            return _text_of(m.get("content"))
    return ""


def _approx_tokens(s: str) -> int:
    return max(1, math.ceil(len(s) / 4))


def _chunk(content: str | None = None, usage: Any = None) -> SimpleNamespace:
    choices = [] if content is None else [SimpleNamespace(delta=SimpleNamespace(content=content), finish_reason=None,
                                                          index=0)]
    return SimpleNamespace(choices=choices, usage=usage, id="fake-resp")


def _answer(seed: bytes, prompt: str) -> list[str]:
    n = 8 + seed[0] % 12
    words = [_WORDS[seed[(i + 1) % len(seed)] % len(_WORDS)] for i in range(n)]
    head = f"[fake] Re: {prompt.strip()[:40]}".rstrip()
    return [head] + [f" {w}" for w in words] + ["."]


async def stream_chunks(**kwargs: Any) -> AsyncIterator[Any]:
    messages = kwargs.get("messages") or []
    last = _last_user(messages)
    marker = (_MARKER.search(last) or [None, None])[1]

    if marker == "transient":
        raise TransportError("fake transient provider failure", retryable=True, status=503)
    if marker == "provider_unavailable":
        raise TransportError("fake provider unavailable", retryable=False, status=502)
    if marker == "timeout":
        budget = float(kwargs.get("timeout") or 30.0)
        await sleep(budget + 1.0)
        raise TransportError("fake provider timeout", retryable=True, status=None)

    seed = hashlib.sha256(f"{kwargs.get('model')}\x00{last}".encode()).digest()
    parts = _answer(seed, _MARKER.sub("", last))

    if marker == "midstream":
        for p in parts[:2]:
            yield _chunk(p)
        raise TransportError("fake stream interrupted mid-response", retryable=True, status=None)

    for p in parts:
        yield _chunk(p)
    prompt_text = " ".join(_text_of(m.get("content")) for m in messages)
    usage = SimpleNamespace(
        prompt_tokens=_approx_tokens(prompt_text),
        completion_tokens=_approx_tokens("".join(parts)),
        total_tokens=None,
        prompt_tokens_details=SimpleNamespace(cached_tokens=0),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=0),
    )
    usage.total_tokens = usage.prompt_tokens + usage.completion_tokens
    yield _chunk(None, usage=usage)


def _dimension(model: str | None, requested: Any) -> int:
    if requested:
        return int(requested)
    from runtime.providers.profiles import resolve_embedding_profile_by_model
    bare = (model or "").split("/", 1)[-1]
    try:
        dim = resolve_embedding_profile_by_model(bare).dimension
    except FileNotFoundError:
        dim = None
    return int(dim or 1024)


def _vector(model: str, text: str, dim: int) -> list[float]:
    """Deterministic unit vector: sha256 counter-mode → floats in [-1, 1) → L2-normalized."""
    out: list[float] = []
    i = 0
    while len(out) < dim:
        block = hashlib.sha256(f"{model}\x00{text}\x00{i}".encode()).digest()
        out.extend((v / 2**31) - 1.0 for v in struct.unpack(">8I", block))
        i += 1
    out = out[:dim]
    norm = math.sqrt(sum(x * x for x in out)) or 1.0
    return [x / norm for x in out]


async def embed(**kwargs: Any) -> Any:
    model = kwargs.get("model") or ""
    inputs = kwargs.get("input") or []
    if isinstance(inputs, str):
        inputs = [inputs]
    dim = _dimension(model, kwargs.get("dimensions"))
    data = [{"index": i, "object": "embedding", "embedding": _vector(model, t, dim)} for i, t in enumerate(inputs)]
    usage = SimpleNamespace(prompt_tokens=sum(_approx_tokens(t) for t in inputs), total_tokens=None)
    usage.total_tokens = usage.prompt_tokens
    return SimpleNamespace(data=data, usage=usage, model=model, id="fake-embed")
