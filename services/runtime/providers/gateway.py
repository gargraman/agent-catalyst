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
from runtime.providers.transport import TransportError


class ModelRetiredError(RuntimeError):
    """The resolved capability profile is retired; it must not be executed as if active (gateway
    behavior #2). Distinct from an unknown model (resolve_profile raises FileNotFoundError, FR-008)."""


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


def _normalize_error(exc: TransportError, api_key: str) -> dict:
    """Map a TransportError (the transport seam's single wrapper for any provider/SDK/network failure) to a
    normalized, SDK-agnostic error payload (FR-009/P8): a stable `type`, the `retryable` hint already
    classified at the seam, and a message scrubbed of the api_key (FR-012/P5). Only TransportError reaches
    here — our own adapter/mapping bugs raise their own types and propagate uncaught (honest failure, F3)."""
    message = str(exc)
    if api_key and api_key in message:
        message = message.replace(api_key, "***")
    error: dict = {"type": "provider_error", "retryable": bool(getattr(exc, "retryable", False)),
                   "message": message}
    status = getattr(exc, "status", None)
    if isinstance(status, int):
        error["status"] = status
    return error


async def stream(
    req: NormalizedRequest, *, api_key: str, profile: CapabilityProfile | None = None
) -> AsyncIterator[NormalizedStreamEvent]:
    """Resolve the profile, select the provider adapter, and stream normalized events.

    Fail-closed before any call: an unknown (provider, model) raises FileNotFoundError from
    resolve_profile (FR-008 — never a guessed model/pricing); a `retired` profile raises
    ModelRetiredError (behavior #2 — never run as if active). These are caller-visible config errors,
    distinct from runtime failures.

    On the terminal USAGE event, attach OUR cost (US2: compute_cost/quantize_usd, never litellm's; flagged
    `cost_is_estimate` for a provisional profile, FR-010/P7) and merge preflight + runtime-discovered
    substitutions (US3/P7). Runtime hardening (P8): a transport/adapter failure is normalized to a single
    ERROR event with no raw SDK type and the api_key scrubbed (FR-009/FR-012); a stream that ends without a
    USAGE is reported as a degraded ERROR, never a silent success. Our own post-processing is kept OUT of
    the transport try/except so a bug here is never misattributed as a provider error (honest failure)."""
    profile = profile or resolve_profile(req.provider, req.model, req.api_version)
    if getattr(profile, "availability", "active") == "retired":
        raise ModelRetiredError(
            f"model {req.model!r} (provider {req.provider!r}) is retired and must not be executed"
        )
    adapter = adapter_for(req.provider)
    preflight = adapter.supports(profile, req)

    stream_iter = adapter.stream(req, api_key=api_key, profile=profile)
    saw_usage = False
    saw_error = False
    while True:
        try:
            event = await stream_iter.__anext__()
        except StopAsyncIteration:
            break
        except TransportError as exc:  # ONLY transport failures normalize; our own bugs propagate (P8/F3)
            yield NormalizedStreamEvent(EventType.ERROR, {"error": _normalize_error(exc, api_key)})
            return

        # Post-processing deliberately OUTSIDE the except: a failure here is our bug, not a provider error.
        if event.type is EventType.USAGE:
            saw_usage = True
            usage = event.data["usage"]
            # OUR cost, from OUR profile (never litellm's). NormalizedUsage.cost_usd is a float by IR
            # design — a convenience/display value; the authoritative numeric(12,6) Decimal is applied at
            # persistence (M2) via quantize_usd straight off compute_cost, not round-tripped from this float.
            priced = replace(usage, cost_usd=float(quantize_usd(compute_cost(profile, usage))))
            substitutions = _merge_substitutions(preflight, event.data.get("substitutions", []))
            data = {**event.data, "usage": priced, "substitutions": substitutions}
            if getattr(profile, "provisional", False):
                data["cost_is_estimate"] = True
            event = NormalizedStreamEvent(EventType.USAGE, data)
        elif event.type is EventType.ERROR:
            saw_error = True  # the adapter surfaced its own failure — don't append a spurious degraded (F2)
        yield event

    if not saw_usage and not saw_error:
        # Completed with neither a usage report nor an adapter-surfaced error → degraded, not a silent
        # success (P8). retryable=False: output may have been produced and billed, so do not auto-retry.
        error: dict = {"type": "degraded", "retryable": False,
                       "message": "provider stream ended without a usage report"}
        if preflight:
            # Best-effort ledger: the preflight downgrades still happened on the wire (F4). Runtime wire-gap
            # extras ride the USAGE event, so they are unavailable on a usage-less stream.
            error["substitutions"] = list(preflight)
        yield NormalizedStreamEvent(EventType.ERROR, {"error": error})
