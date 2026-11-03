"""OpenAI provider adapter (M1) — LiteLLM transport, OpenAI-shaped usage.

Translates a NormalizedRequest to LiteLLM kwargs and streams normalized events via the shared
transport plumbing. supports() delegates to the single substitution rule set. Cost is attached by the
gateway (never here); litellm's own cost field is never read.
"""
from __future__ import annotations

from typing import AsyncIterator

from runtime.providers import _common
from runtime.providers.base import register_adapter
from runtime.providers.ir import NormalizedRequest, NormalizedStreamEvent
from runtime.providers.profiles import CapabilityProfile, Substitution, preflight_substitutions


class OpenAIAdapter:
    provider = "openai"

    def supports(self, profile: CapabilityProfile, req: NormalizedRequest) -> list[Substitution]:
        return preflight_substitutions(profile, req)

    def stream(
        self, req: NormalizedRequest, *, api_key: str, profile: CapabilityProfile
    ) -> AsyncIterator[NormalizedStreamEvent]:
        # LiteLLM routes OpenAI models by bare id (e.g. "gpt-5-mini").
        messages, params, subs = _common.build_wire_request(profile, req)
        return _common.stream_via_transport(
            model=req.model,
            messages=messages,
            api_key=api_key,
            params=params,
            tools=req.tools or None,
            substitutions=subs,
        )


register_adapter(OpenAIAdapter())
