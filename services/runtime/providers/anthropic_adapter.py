"""Anthropic provider adapter (M1 US4) — net-new. LiteLLM transport, Anthropic usage shape.

Proves provider-agnosticism: a third provider slots in behind the same ProviderAdapter contract with no
caller change. It shares the content-stream loop with the OpenAI/Ollama adapters but supplies its own
usage mapper, because Anthropic reports token counts differently (cache_read/cache_creation, and
input_tokens EXCLUSIVE of cache — see _common.map_anthropic_usage / contracts/usage-mapping.md). The judge
model (claude-haiku-5-5) runs through this adapter.

supports() delegates to the single substitution rule set; cost is attached by the gateway (never here),
and litellm's own cost field is never read.
"""
from __future__ import annotations

from typing import AsyncIterator

from runtime.providers import _common
from runtime.providers.base import register_adapter
from runtime.providers.ir import NormalizedRequest, NormalizedStreamEvent
from runtime.providers.profiles import CapabilityProfile, Substitution, preflight_substitutions


class AnthropicAdapter:
    provider = "anthropic"

    def supports(self, profile: CapabilityProfile, req: NormalizedRequest) -> list[Substitution]:
        return preflight_substitutions(profile, req)

    def stream(
        self, req: NormalizedRequest, *, api_key: str, profile: CapabilityProfile,
        timeout: float | None = None,
    ) -> AsyncIterator[NormalizedStreamEvent]:
        # LiteLLM routes Anthropic by the "anthropic/<model>" id.
        messages, params, subs = _common.build_wire_request(profile, req)
        return _common.stream_via_transport(
            model=f"anthropic/{req.model}",
            messages=messages,
            api_key=api_key,
            params=params,
            tools=req.tools or None,
            usage_mapper=_common.map_anthropic_usage,
            substitutions=subs,
            timeout=timeout,
            provider_correlation_id=req.provider_correlation_id,
        )


register_adapter(AnthropicAdapter())
