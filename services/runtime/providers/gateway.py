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
from runtime.providers import resilience, transport
from runtime.providers._common import _response_request_id
from runtime.providers.ir import (
    EmbeddingError,
    EmbeddingResult,
    EventType,
    NormalizedRequest,
    NormalizedStreamEvent,
    NormalizedUsage,
)
from runtime.providers.profiles import (
    CapabilityProfile,
    Substitution,
    resolve_embedding_profile,
    resolve_profile,
)
from runtime.providers.resilience import Admission, Guard, TrafficClass
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


# Once any of these reached the caller, a retry would duplicate output (F10 / spec FR-005) — no retry after it.
_UNAVAILABLE = {"type": "provider_unavailable", "retryable": True, "attempts": 0,
                "message": "provider temporarily unavailable (circuit open)"}

_SURFACED = frozenset({EventType.TEXT_DELTA, EventType.REASONING_DELTA, EventType.TOOL_CALL_START,
                       EventType.TOOL_CALL_DELTA, EventType.TOOL_CALL_END})


def _labels(provider: str, model: str, cls: str) -> dict:
    return {"provider": provider, "model": model, "cls": cls}


async def stream(
    req: NormalizedRequest, *, api_key: str, profile: CapabilityProfile | None = None,
    timeout: float | None = None, guard: Guard | None = None, traffic_class: TrafficClass = "live",
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
    the transport try/except so a bug here is never misattributed as a provider error (honest failure).

    `timeout` (seconds) is threaded to the transport/litellm call so the provider call itself is bounded
    (research D5); `None` (the default) leaves transport behavior unchanged — M2a's deadline enforcement
    also lives in the invoke pipeline, and this is defense in depth at the wire.

    Feature 025 (US1, contract runtime-resilience §2): a RETRYABLE TransportError that happens before any content
    event surfaced is retried by `guard` (default: the process guard) — bounded by `max_attempts`, a capped wait, and
    the `timeout` treated as the call's deadline (D3: no doomed attempt). A failure after content surfaced is never
    retried (no duplicated output). The terminal USAGE / ERROR carries `"attempts"` so the run record can report
    retries honestly; a failed attempt reports no usage and therefore adds no cost (FR-015)."""
    profile = profile or resolve_profile(req.provider, req.model, req.api_version)
    if getattr(profile, "availability", "active") == "retired":
        raise ModelRetiredError(
            f"model {req.model!r} (provider {req.provider!r}) is retired and must not be executed"
        )
    adapter = adapter_for(req.provider)
    preflight = adapter.supports(profile, req)

    guard = guard or resilience.default_guard()
    labels = _labels(req.provider, req.model, traffic_class)
    started = guard.clock()
    # Breaker admission once per call, AFTER the config checks above (S10: they never touch the breaker).
    admission = await guard.admit(req.provider, req.model, traffic_class, timeout)
    if admission is Admission.REFUSED:      # S9: fail fast, zero transport calls
        yield NormalizedStreamEvent(EventType.ERROR, {"error": dict(_UNAVAILABLE), "attempts": 0})
        return
    was_trial = admission is Admission.TRIAL
    attempt = 0
    while True:
        attempt += 1
        wire_timeout = timeout if attempt == 1 else guard.remaining(started, timeout)
        stream_iter = adapter.stream(req, api_key=api_key, profile=profile, timeout=wire_timeout)
        saw_usage = False
        saw_error = False
        surfaced = False
        failure: TransportError | None = None
        while True:
            try:
                event = await stream_iter.__anext__()
            except StopAsyncIteration:
                break
            except TransportError as exc:  # ONLY transport failures normalize; our own bugs propagate (P8/F3)
                failure = exc
                break

            # Post-processing deliberately OUTSIDE the except: a failure here is our bug, not a provider error.
            if event.type is EventType.USAGE:
                saw_usage = True
                usage = event.data["usage"]
                # OUR cost, from OUR profile (never litellm's). NormalizedUsage.cost_usd is a float by IR
                # design — a convenience/display value; the authoritative numeric(12,6) Decimal is applied at
                # persistence (M2) via quantize_usd straight off compute_cost, not round-tripped from this float.
                priced = replace(usage, cost_usd=float(quantize_usd(compute_cost(profile, usage))))
                substitutions = _merge_substitutions(preflight, event.data.get("substitutions", []))
                data = {**event.data, "usage": priced, "substitutions": substitutions, "attempts": attempt}
                if getattr(profile, "provisional", False):
                    data["cost_is_estimate"] = True
                event = NormalizedStreamEvent(EventType.USAGE, data)
            elif event.type is EventType.ERROR:
                saw_error = True  # the adapter surfaced its own failure — don't append a spurious degraded (F2)
                event = NormalizedStreamEvent(EventType.ERROR, {**event.data, "attempts": attempt})
            elif event.type in _SURFACED:
                surfaced = True
            yield event

        if failure is None:
            # The provider answered (usage, adapter ERROR or degraded end) — healthy from the breaker's view.
            await guard.record(req.provider, req.model, traffic_class, success=True, was_trial=was_trial)
            break
        if failure.retryable:               # only transient failures count (S3: non-transient leaves the breaker alone)
            await guard.record(req.provider, req.model, traffic_class, success=False, was_trial=was_trial)
        # A failed half-open trial re-opened the breaker — do not retry it (owner-approved 2026-11-15).
        if failure.retryable and not surfaced and not was_trial and attempt < guard.policy.max_attempts:
            wait = guard.retry_wait(attempt + 1, getattr(failure, "retry_after", None))
            if guard.fits_deadline(started, wait, timeout):
                guard.emit("retry", labels)
                await guard.sleep(wait)
                continue
        error = _normalize_error(failure, api_key)
        error["attempts"] = attempt
        yield NormalizedStreamEvent(EventType.ERROR, {"error": error, "attempts": attempt})
        return

    if not saw_usage and not saw_error:
        # Completed with neither a usage report nor an adapter-surfaced error → degraded, not a silent
        # success (P8). retryable=False: output may have been produced and billed, so do not auto-retry (S8).
        error: dict = {"type": "degraded", "retryable": False,
                       "message": "provider stream ended without a usage report", "attempts": attempt}
        if preflight:
            # Best-effort ledger: the preflight downgrades still happened on the wire (F4). Runtime wire-gap
            # extras ride the USAGE event, so they are unavailable on a usage-less stream.
            error["substitutions"] = list(preflight)
        yield NormalizedStreamEvent(EventType.ERROR, {"error": error, "attempts": attempt})


def _embedding_item(item) -> tuple[int | None, list[float]]:
    """litellm 1.106.6 returns `data[i]` as a dict (spike T005); tolerate attribute access too."""
    if isinstance(item, dict):
        return item.get("index"), item["embedding"]
    return getattr(item, "index", None), item.embedding


async def embed(
    texts: list[str], *, provider: str, model: str, api_key: str | None, timeout: float | None = None,
    region: str | None = None, guard: Guard | None = None, traffic_class: TrafficClass = "live",
) -> EmbeddingResult:
    """Embed `texts` with OUR cost accounting (feature 024, contracts/runtime.md §8, research D2).

    1. Resolve the EMBEDDING profile — unknown or chat model → FileNotFoundError before any transport call (fail
       closed; the caller maps it to a config error).
    2. Batch by the profile's `max_batch`; vectors are returned in input order across batches.
    3. Usage = Σ `usage.prompt_tokens`; cost = compute_cost(profile, usage) quantized ONCE (never per batch, never
       litellm's response_cost — R3). `cost_is_estimate` mirrors the profile's `provisional` flag.
    4. Every vector must have the profile's `dimension`, else EmbeddingError("dimension_mismatch") — storing a wrong
       length would corrupt the index (the column is vector(1024), research D3). When the profile declares
       `features.dimensions_param`, the deployment dimension is REQUESTED (`dimensions=`), e.g. text-embedding-3-small
       natively emits 1536 and is asked for 1024.
    5. Transport failures → EmbeddingError(type="provider_error", retryable=…), message scrubbed of the api key.

    Bedrock (the customer-data provider, decision 2026-11-14) authenticates with the process's AWS credential chain:
    `api_key` is None, `region` becomes litellm's `aws_region_name`, and no api key is ever sent.

    Feature 025 (contract §5): each BATCH request runs the same bounded attempt loop as `stream` (retryable
    TransportError only; capped wait; `timeout` doubles as the deadline, `None` => attempts x cap only). Vectors are
    appended only after a batch succeeds, so a retry never duplicates or reorders them. `attempts` is summed over
    batches on both the result and the error.
    """
    profile = resolve_embedding_profile(provider, model)
    guard = guard or resilience.default_guard()
    labels = _labels(provider, model, traffic_class)
    started = guard.clock()
    total_attempts = 0
    batch_size = max(1, profile.max_batch or len(texts) or 1)
    vectors: list[tuple[float, ...]] = []
    input_tokens = 0
    request_id: str | None = None
    for start in range(0, len(texts), batch_size):
        batch = texts[start:start + batch_size]
        kwargs: dict = {"model": f"{provider}/{model}", "input": batch}
        if api_key:
            kwargs["api_key"] = api_key
        if region:
            kwargs["aws_region_name"] = region
        if profile.feature("dimensions_param") and profile.dimension:
            kwargs["dimensions"] = profile.dimension
        if timeout is not None:
            kwargs["timeout"] = timeout
        admission = await guard.admit(provider, model, traffic_class, timeout)
        if admission is Admission.REFUSED:  # §5: refusal ⇒ typed, retryable EmbeddingError, zero transport calls
            raise EmbeddingError(_UNAVAILABLE["message"], type="provider_unavailable", retryable=True,
                                 attempts=total_attempts)
        was_trial = admission is Admission.TRIAL
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = await transport.embed(**kwargs)
                await guard.record(provider, model, traffic_class, success=True, was_trial=was_trial)
                break
            except TransportError as exc:
                if exc.retryable:
                    await guard.record(provider, model, traffic_class, success=False, was_trial=was_trial)
                if exc.retryable and not was_trial and attempt < guard.policy.max_attempts:
                    wait = guard.retry_wait(attempt + 1, getattr(exc, "retry_after", None))
                    if guard.fits_deadline(started, wait, timeout):
                        guard.emit("retry", labels)
                        await guard.sleep(wait)
                        continue
                err = _normalize_error(exc, api_key or "")
                raise EmbeddingError(err["message"], type=err["type"], retryable=err["retryable"],
                                     attempts=total_attempts + attempt) from None
        total_attempts += attempt
        items = sorted((_embedding_item(d) for d in resp.data), key=lambda t: (t[0] is None, t[0] or 0))
        if len(items) != len(batch):
            raise EmbeddingError(f"provider returned {len(items)} vectors for {len(batch)} inputs",
                                 type="count_mismatch", retryable=True)
        for _, vec in items:
            if profile.dimension is not None and len(vec) != profile.dimension:
                raise EmbeddingError(f"vector length {len(vec)} != profile dimension {profile.dimension}",
                                     type="dimension_mismatch")
            vectors.append(tuple(float(x) for x in vec))
        usage = getattr(resp, "usage", None)
        input_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
        if request_id is None:
            request_id = _response_request_id(resp)
    usage_total = NormalizedUsage(input_tokens=input_tokens, output_tokens=0)
    return EmbeddingResult(
        vectors=tuple(vectors),
        usage=usage_total,
        cost_usd=quantize_usd(compute_cost(profile, usage_total)),
        cost_is_estimate=profile.provisional,
        provider_request_id=request_id,
        attempts=total_attempts,
    )
