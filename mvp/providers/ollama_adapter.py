"""Ollama /api/chat adapter — mirrors docs/backend/02-provider-gateway.md §3 (LiteLLM/self-host slot).

Local, $0-marginal, maximally-different-from-OpenAI runtime — the strong H2 test. The profile marks
reasoning_control / prompt_caching / forced_tool_choice as unsupported, so this adapter is where most
of the capability substitutions show up. Stdlib only (urllib); Ollama streams newline-delimited JSON
(not SSE), so each line is a full object.
"""

import json
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

_API_URL = "http://localhost:11434/api/chat"


def _build_body(req: NormalizedRequest) -> dict:
    """NormalizedRequest → Ollama /api/chat wire body (downgrades applied: no reasoning, no cache)."""
    messages: list[dict] = []
    if req.system:
        messages.append({"role": "system", "content": req.system})
    messages.extend(req.messages)

    body: dict = {"model": req.model, "messages": messages, "stream": True}
    if req.tools:
        body["tools"] = req.tools                  # OpenAI-style function format — Ollama accepts it
    if req.response_format is not None:
        body["format"] = "json"                    # structured output → JSON mode
    options: dict = {}
    if "temperature" in req.params:
        options["temperature"] = req.params["temperature"]
    if "max_tokens" in req.params:
        options["num_predict"] = req.params["max_tokens"]
    if options:
        body["options"] = options
    return body


def _open_ndjson(url: str, headers: dict, body: dict):
    """Open the streaming request; yield decoded JSON lines. Isolated so tests can monkeypatch it."""
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request) as resp:
        for raw in resp:
            yield raw.decode("utf-8").rstrip("\n")


def _parse_usage(chunk: dict) -> NormalizedUsage:
    # Ollama reports no cached/reasoning token breakdown → 0 (honest absence, not a guess).
    return NormalizedUsage(
        input_tokens=chunk.get("prompt_eval_count", 0),
        output_tokens=chunk.get("eval_count", 0),
    )


class OllamaAdapter:
    provider = "ollama"

    def supports(self, profile: CapabilityProfile, req: NormalizedRequest) -> list[Substitution]:
        subs: list[Substitution] = []
        if req.reasoning and not profile.feature("reasoning_control"):
            subs.append(Substitution("reasoning_control", "dropped",
                                      "no reasoning effort/budget control on this model"))
        if req.cache_hints and not profile.feature("prompt_caching"):
            subs.append(Substitution("prompt_caching", "dropped",
                                      "prompt-cache breakpoints unsupported — hints discarded"))
        if isinstance(req.tool_choice, dict) and not profile.feature("forced_tool_choice"):
            subs.append(Substitution("forced_tool_choice", "emulated_via_prompt",
                                      "cannot force a specific tool — nudged via prompt instead"))
        if req.response_format and not profile.feature("structured_output"):
            subs.append(Substitution("structured_output", "emulated_via_prompt",
                                      "JSON enforced via instruction, not format:json"))
        return subs

    async def stream(
        self, req: NormalizedRequest, *, api_key: str, profile: CapabilityProfile
    ) -> AsyncIterator[NormalizedStreamEvent]:
        headers = {"Content-Type": "application/json"}
        body = _build_body(req)
        usage: NormalizedUsage | None = None
        for line in _open_ndjson(_API_URL, headers, body):
            if not line:
                continue
            chunk = json.loads(line)
            message = chunk.get("message") or {}
            text = message.get("content")
            if text:
                yield NormalizedStreamEvent(EventType.TEXT, {"text": text})
            for tc in message.get("tool_calls") or []:   # surface tool calls as text (minimal EventType)
                fn = tc.get("function") or {}
                args = fn.get("arguments")
                args = args if isinstance(args, str) else json.dumps(args)
                yield NormalizedStreamEvent(
                    EventType.TEXT, {"text": f"[tool_call] {fn.get('name', '')}({args})"}
                )
            if chunk.get("done"):
                usage = _parse_usage(chunk)
        yield NormalizedStreamEvent(
            EventType.USAGE,
            {"usage": usage or NormalizedUsage(0, 0), "substitutions": []},
        )


register_adapter(OllamaAdapter())
