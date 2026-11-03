"""Provider gateway (M1) — the orchestrator every caller (M2 runtime, M3 eval, Phase-4 API, channels)
uses. Resolves the capability profile, picks the adapter for the request's provider, and streams
normalized events through. No caller sees a provider-specific type.

M1 build order (TDD): US1 = passthrough (this file now). US2 adds our-cost attachment on the terminal
USAGE (compute_cost/quantize_usd; litellm's cost is never used). US3 merges preflight + runtime-
discovered substitutions. Polish adds fail-closed unknown/retired model, transport-error normalization,
and secret non-leakage. See contracts/provider-adapter.md.
"""
from __future__ import annotations

from dataclasses import replace
from typing import AsyncIterator

from runtime.providers.base import adapter_for
from runtime.providers.cost import compute_cost, quantize_usd
from runtime.providers.ir import EventType, NormalizedRequest, NormalizedStreamEvent
from runtime.providers.profiles import CapabilityProfile, Substitution, resolve_profile


def _merge_substitutions(
    preflight: list[Substitution], discovered: list[Substitution]
) -> list[Substitution]:
    """Preflight downgrades first, then runtime-discovered ones, deduped by (feature, action)."""
    merged: list[Substitution] = []
    seen: set[tuple[str, str]] = set()
    for sub in (*preflight, *discovered):
        key = (sub.feature, sub.action)
        if key not in seen:
            seen.add(key)
            merged.append(sub)
    return merged


async def stream(
    req: NormalizedRequest, *, api_key: str, profile: CapabilityProfile | None = None
) -> AsyncIterator[NormalizedStreamEvent]:
    """Resolve the profile (fail-closed on unknown model — hardened in Polish), select the provider
    adapter, and stream its normalized events. On the terminal USAGE event, attach OUR cost (US2):
    computed from the capability profile via compute_cost, quantized once (never from litellm). Cost
    from a provisional/unreconciled profile is flagged `cost_is_estimate` (FR-010/P7). Substitution
    merge (US3) and fail-closed/error/secret hardening (Polish) arrive in later phases."""
    profile = profile or resolve_profile(req.provider, req.model, req.api_version)
    adapter = adapter_for(req.provider)
    preflight = adapter.supports(profile, req)
    async for event in adapter.stream(req, api_key=api_key, profile=profile):
        if event.type is EventType.USAGE:
            usage = event.data["usage"]
            # OUR cost, from OUR profile (never litellm's). NormalizedUsage.cost_usd is a float by IR
            # design — a convenience/display value; the authoritative numeric(12,6) Decimal is applied at
            # persistence (M2) via quantize_usd straight off compute_cost, not round-tripped from this float.
            priced = replace(usage, cost_usd=float(quantize_usd(compute_cost(profile, usage))))
            # Every downgrade surfaces explicitly (US3/P7): preflight (supports()) merged with any
            # runtime-discovered downgrades the adapter attached, deduped by (feature, action).
            substitutions = _merge_substitutions(preflight, event.data.get("substitutions", []))
            data = {**event.data, "usage": priced, "substitutions": substitutions}
            if getattr(profile, "provisional", False):
                data["cost_is_estimate"] = True
            event = NormalizedStreamEvent(EventType.USAGE, data)
        yield event
