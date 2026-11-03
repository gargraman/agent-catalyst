"""ProviderAdapter Protocol + registry (02 §3) — one interface, N implementations.

Each adapter translates NormalizedRequest ⇄ provider wire (via transport.py / LiteLLM), normalizes raw
usage into NormalizedUsage, and reports the capability substitutions it must make. Cost is attached by
the GATEWAY (cost.compute_cost), never inside an adapter — so the adapter stays the single place that
knows a provider exists, and cost stays the single authority (02 §4.1).

Note vs the mvp port: `Substitution` lives in `profiles` here (not `ir`); adapters delegate supports()
to `profiles.preflight_substitutions`.
"""
from __future__ import annotations

import importlib
from typing import AsyncIterator, Protocol, runtime_checkable

from runtime.providers.ir import NormalizedRequest, NormalizedStreamEvent
from runtime.providers.profiles import CapabilityProfile, Substitution


@runtime_checkable
class ProviderAdapter(Protocol):
    provider: str

    def supports(self, profile: CapabilityProfile, req: NormalizedRequest) -> list[Substitution]:
        """Preflight downgrades THIS provider needs for THIS request (delegates to
        profiles.preflight_substitutions). Empty when the profile supports everything the request uses."""
        ...

    def stream(
        self, req: NormalizedRequest, *, api_key: str, profile: CapabilityProfile
    ) -> AsyncIterator[NormalizedStreamEvent]:
        """Call the provider (via transport.stream_chunks) and yield normalized events: TEXT_DELTA as
        content streams, then exactly one final USAGE event carrying a NormalizedUsage with RAW token
        counts (cost attached by the gateway, not here)."""
        ...


_ADAPTERS: dict[str, ProviderAdapter] = {}

# provider → module that registers its adapter at import. Only the requested provider's module is
# imported (lazily), so a not-yet-built adapter never blocks another provider's resolution.
_ADAPTER_MODULES = {
    "openai": "runtime.providers.openai_adapter",
    "ollama": "runtime.providers.ollama_adapter",
    "anthropic": "runtime.providers.anthropic_adapter",
}


def register_adapter(adapter: ProviderAdapter) -> None:
    _ADAPTERS[adapter.provider] = adapter


def adapter_for(provider: str) -> ProviderAdapter:
    """Return the registered adapter for `provider`, importing its module on first use. Fails closed
    (KeyError) for a provider with no adapter — never silently substitutes another provider."""
    if provider not in _ADAPTERS:
        module = _ADAPTER_MODULES.get(provider)
        if module:
            try:
                importlib.import_module(module)
            except ModuleNotFoundError:
                pass  # listed but not yet implemented (e.g. anthropic pre-M1-US4) → fail closed below
    if provider not in _ADAPTERS:
        raise KeyError(f"no provider adapter registered for {provider!r}")
    return _ADAPTERS[provider]
