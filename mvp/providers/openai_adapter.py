"""OpenAI chat/completions adapter — mirrors docs/backend/02-provider-gateway.md §3 (native-SDK slot).

Stdlib only (urllib) so the MVP has no runtime deps. The gpt-5 reasoning-model param quirks are the
point of H2: these models use `max_completion_tokens` (not `max_tokens`) and reject a non-default
`temperature`. We translate the former, and on a temperature rejection we DROP it and record a
Substitution rather than failing — the capability leak, made visible (02 §5).
"""

import json
import urllib.error
import urllib.request
from typing import AsyncIterator

from ir import (
    EventType,
    NormalizedRequest,
    NormalizedStreamEvent,
    NormalizedUsage,
    Substitution,
)
from profiles.loader import CapabilityProfile
from providers.base import register_adapter

_API_URL = "https://api.openai.com/v1/chat/completions"


def _build_body(req: NormalizedRequest, *, drop_temperature: bool) -> dict:
    """NormalizedRequest → OpenAI chat/completions wire body."""
    messages: list[dict] = []
    if req.system:
        messages.append({"role": "system", "content": req.system})
    messages.extend(req.messages)

    body: dict = {
        "model": req.model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if req.tools:
        body["tools"] = req.tools                       # OpenAI tool format — passthrough
    if req.tool_choice is not None:
        body["tool_choice"] = req.tool_choice
    if req.response_format is not None:
        body["response_format"] = req.response_format   # structured output — passthrough
    if req.reasoning and "effort" in req.reasoning:
        body["reasoning_effort"] = req.reasoning["effort"]

    params = dict(req.params)
    if "max_tokens" in params:                          # gpt-5 quirk: reasoning models need this name
        params["max_completion_tokens"] = params.pop("max_tokens")
    if drop_temperature:
        params.pop("temperature", None)
    body.update(params)
    return body


def _open_sse(url: str, headers: dict, body: dict):
    """Open the streaming request; yield decoded SSE lines. Isolated so tests can monkeypatch it."""
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request) as resp:
        for raw in resp:
            yield raw.decode("utf-8").rstrip("\n")


def _parse_usage(u: dict) -> NormalizedUsage:
    # OpenAI nests the fine-grained counts; completion_tokens is INCLUSIVE of reasoning_tokens and
    # prompt_tokens is INCLUSIVE of cached_tokens (see report caveat on additive vs inclusive pricing).
    ptd = u.get("prompt_tokens_details") or {}
    ctd = u.get("completion_tokens_details") or {}
    return NormalizedUsage(
        input_tokens=u.get("prompt_tokens", 0),
        output_tokens=u.get("completion_tokens", 0),
        cached_input_tokens=ptd.get("cached_tokens", 0),
        reasoning_tokens=ctd.get("reasoning_tokens", 0),
    )


def _short(text: str, n: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n] + "…"


class OpenAIAdapter:
    provider = "openai"

    def supports(self, profile: CapabilityProfile, req: NormalizedRequest) -> list[Substitution]:
        subs: list[Substitution] = []
        if req.response_format and not profile.feature("structured_output"):
            subs.append(Substitution("structured_output", "emulated_via_prompt",
                                      "JSON enforced via instruction, not response_format"))
        if isinstance(req.tool_choice, dict) and not profile.feature("forced_tool_choice"):
            subs.append(Substitution("forced_tool_choice", "emulated_via_prompt",
                                      "tool forced via prompt, not tool_choice"))
        if req.reasoning and not profile.feature("reasoning_control"):
            subs.append(Substitution("reasoning_control", "dropped",
                                      "reasoning effort/budget not controllable on this model"))
        if req.cache_hints and not profile.feature("prompt_caching"):
            subs.append(Substitution("prompt_caching", "dropped",
                                      "prompt-cache breakpoints unsupported"))
        return subs

    async def stream(
        self, req: NormalizedRequest, *, api_key: str, profile: CapabilityProfile
    ) -> AsyncIterator[NormalizedStreamEvent]:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        runtime_subs: list[Substitution] = []
        drop_temperature = False

        for attempt in range(2):
            body = _build_body(req, drop_temperature=drop_temperature)
            try:
                usage: NormalizedUsage | None = None
                tool_calls: dict[int, dict] = {}   # index → {name, args} accumulated across deltas
                for line in _open_sse(_API_URL, headers, body):
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[len("data:"):].strip()
                    if payload == "[DONE]":
                        break
                    chunk = json.loads(payload)
                    for choice in chunk.get("choices", []):
                        delta = choice.get("delta") or {}
                        text = delta.get("content")
                        if text:
                            yield NormalizedStreamEvent(EventType.TEXT, {"text": text})
                        for tc in delta.get("tool_calls") or []:
                            slot = tool_calls.setdefault(tc.get("index", 0), {"name": "", "args": ""})
                            fn = tc.get("function") or {}
                            slot["name"] += fn.get("name") or ""
                            slot["args"] += fn.get("arguments") or ""
                    if chunk.get("usage"):
                        usage = _parse_usage(chunk["usage"])
                for slot in tool_calls.values():       # surface tool calls as text (minimal EventType)
                    yield NormalizedStreamEvent(
                        EventType.TEXT, {"text": f"[tool_call] {slot['name']}({slot['args']})"}
                    )
                yield NormalizedStreamEvent(
                    EventType.USAGE,
                    {"usage": usage or NormalizedUsage(0, 0), "substitutions": runtime_subs},
                )
                return
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", "replace")
                if (
                    e.code == 400
                    and attempt == 0
                    and "temperature" in err_body
                    and "temperature" in req.params
                ):
                    drop_temperature = True
                    runtime_subs.append(Substitution(
                        "temperature", "dropped",
                        f"model rejected non-default temperature → dropped: {_short(err_body)}",
                    ))
                    continue
                raise


register_adapter(OpenAIAdapter())
