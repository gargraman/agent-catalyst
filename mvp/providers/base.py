"""ProviderAdapter Protocol + registry — mirrors docs/backend/02-provider-gateway.md §3 (02:83-106).

One interface, N implementations. Each adapter translates NormalizedRequest ⇄ provider wire format,
normalizes raw usage, and reports the capability substitutions it had to make. Cost is attached by the
caller (via cost.compute_cost), never inside the adapter — so the adapter stays the single place that
knows a provider exists, and cost stays the single authority (02 §4.1).
"""

from typing import AsyncIterator, Protocol, runtime_checkable

from ir import NormalizedRequest, NormalizedStreamEvent, Substitution
from profiles.loader import CapabilityProfile


@runtime_checkable
class ProviderAdapter(Protocol):
    provider: str

    def supports(self, profile: CapabilityProfile, req: NormalizedRequest) -> list[Substitution]:
        """Return the downgrades/rewrites THIS provider needs for THIS request, computed from
        profile.features. May be empty. Discovered-at-call-time downgrades (e.g. a param the provider
        rejects) are surfaced on the final USAGE event instead."""
        ...

    def stream(
        self, req: NormalizedRequest, *, api_key: str, profile: CapabilityProfile
    ) -> AsyncIterator[NormalizedStreamEvent]:
        """Call the provider and yield normalized events: TEXT events as content streams, then a final
        USAGE event carrying a NormalizedUsage with RAW token counts (cost attached by the caller)."""
        ...


_ADAPTERS: dict[str, ProviderAdapter] = {}


def register_adapter(adapter: ProviderAdapter) -> None:
    _ADAPTERS[adapter.provider] = adapter


def adapter_for(provider: str) -> ProviderAdapter:
    if not _ADAPTERS:
        # lazy import so registration happens without a hard import cycle at module load
        import providers.ollama_adapter  # noqa: F401
        import providers.openai_adapter  # noqa: F401
    return _ADAPTERS[provider]
