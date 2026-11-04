"""Ollama provider adapter (M1) — LiteLLM transport, OpenAI-shaped usage (local, $0-marginal).

LiteLLM routes Ollama by the "ollama/<model>" id. LiteLLM normalizes Ollama's usage into the OpenAI
shape, so the shared usage mapper applies (cache/reasoning naturally absent → 0). supports() delegates
to the single substitution rule set; cost (which is 0 for a local model) is attached by the gateway.
"""
from __future__ import annotations

from typing import AsyncIterator

from runtime.providers import _common
from runtime.providers.base import register_adapter
from runtime.providers.ir import NormalizedRequest, NormalizedStreamEvent
from runtime.providers.profiles import CapabilityProfile, Substitution, preflight_substitutions


class OllamaAdapter:
    provider = "ollama"

    def supports(self, profile: CapabilityProfile, req: NormalizedRequest) -> list[Substitution]:
        return preflight_substitutions(profile, req)

    def stream(
        self, req: NormalizedRequest, *, api_key: str, profile: CapabilityProfile,
        timeout: float | None = None,
    ) -> AsyncIterator[NormalizedStreamEvent]:
        messages, params, subs = _common.build_wire_request(profile, req)
        return _common.stream_via_transport(
            model=f"ollama/{req.model}",   # LiteLLM provider routing
            messages=messages,
            api_key=api_key or "",
            params=params,
            tools=req.tools or None,
            substitutions=subs,
            timeout=timeout,
        )


register_adapter(OllamaAdapter())
