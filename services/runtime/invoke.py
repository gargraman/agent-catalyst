"""The runtime invoke spine (M2a, feature 003) — the single pure execution path every caller shares.

`runtime.invoke(snapshot, inputs, ctx)` reads an IMMUTABLE VersionSnapshot (never a live row), validates
inputs, runs the call through the M1 gateway, re-surfaces the normalized event stream, bounds the work by
a deadline, and produces exactly ONE honest RunRecord per invocation via the context sink. Pure library
(P6): no web framework, no DB. Fully mocked in M2a — no live/paid calls.

Analyze findings folded in: F1 — the CapabilityProfile is built from the FROZEN `snapshot.profile_snapshot`
and passed to the gateway, never re-resolved from live `profiles_data/` (upholds P6 immutability + P7).
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from runtime.io import build_normalized_request, validate_input
from runtime.providers import gateway
from runtime.providers.ir import EventType, NormalizedUsage
from runtime.providers.profiles import Substitution, _profile_from_json
from runtime.run import InvokeResult, Outcome, RunRecord
from runtime.tokens import count_tokens


@dataclass
class _State:
    text: str = ""
    reasoning_parts: list = field(default_factory=list)
    tool_calls: list = field(default_factory=list)
    usage: NormalizedUsage | None = None
    cost_is_estimate: bool = False
    substitutions: tuple[Substitution, ...] = ()
    stop_reason: str | None = None
    error: dict | None = None
    run: RunRecord | None = None


def _profile_hash(profile_snapshot: dict) -> str:
    blob = json.dumps(profile_snapshot or {}, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def _build_run(snapshot, ctx, correlation_id, outcome, state, latency_ms) -> RunRecord:
    usage = state.usage
    cost = Decimal(str(usage.cost_usd)) if usage is not None else None  # carried, not recomputed (FR-014)
    mc = snapshot.model_config or {}
    return RunRecord(
        run_id=uuid.uuid4().hex,
        correlation_id=correlation_id,
        workspace_id=ctx.workspace_id or snapshot.workspace_id,
        agent_id=snapshot.agent_id,
        version_id=snapshot.version_id,
        version_label=snapshot.label,
        provider=mc.get("provider", ""),
        model=mc.get("model", ""),
        profile_hash=_profile_hash(snapshot.profile_snapshot),
        prompt_hash=snapshot.prompt_hash,
        snapshot_hash=snapshot.snapshot_hash,
        source=ctx.source,
        caller=ctx.caller,
        outcome=outcome,
        latency_ms=latency_ms,
        max_tool_calls=ctx.max_tool_calls,
        usage=usage,
        cost_usd=cost,
        cost_is_estimate=state.cost_is_estimate,
        substitutions=state.substitutions,
        stop_reason=state.stop_reason,
        error=state.error,
    )


async def _emit(ctx, run: RunRecord) -> None:
    result = ctx.record_run(run)
    if inspect.isawaitable(result):
        await result


async def _pipeline(snapshot, inputs, ctx, state: _State):
    """Core generator: yields the surfaced events; builds + emits the RunRecord exactly once at the end."""
    t0 = time.perf_counter()
    correlation_id = ctx.correlation_id or uuid.uuid4().hex

    emitted = False

    async def _finish(outcome):
        nonlocal emitted
        if emitted:  # exactly one Run per invocation, even if called from both a guard and the finally
            return
        emitted = True
        latency_ms = int((time.perf_counter() - t0) * 1000)
        state.run = _build_run(snapshot, ctx, correlation_id, outcome, state, latency_ms)
        await _emit(ctx, state.run)

    # Tenancy well-formedness — a run must be attributable to a workspace (P5; data-model: workspace_id
    # MUST be non-empty). Fail closed BEFORE any provider call rather than silently emit an untenanted Run
    # (converge C1). Mirrors the _build_run fallback (ctx → snapshot); rejects only when both are empty.
    if not (ctx.workspace_id or snapshot.workspace_id):
        state.error = {"type": "config_error", "retryable": False,
                       "message": "workspace_id is required (empty on both context and snapshot)"}
        await _finish(Outcome.CONFIG_ERROR)
        return

    # Coerce an unset deadline to the default so the bound is always active (review F5); None would
    # otherwise make asyncio.timeout(None) unbounded, contradicting "the deadline is the enforced bound".
    deadline = ctx.deadline_s if ctx.deadline_s is not None else 30.0

    # Deadline already elapsed → fail closed before any provider call (FR-009 edge).
    if deadline <= 0:
        state.error = {"type": "timeout", "retryable": False,
                       "message": "deadline elapsed before execution"}
        await _finish(Outcome.TIMEOUT)
        return

    # Input validation — fail closed before any provider call, zero spend (FR-004, SC-005).
    errs = validate_input(inputs, snapshot.io_contract)
    if errs:
        state.error = {"type": "invalid_input", "retryable": False, "message": "; ".join(errs[:5])}
        await _finish(Outcome.INVALID_INPUT)
        return

    # Assemble the request + build the FROZEN profile (F1 — never re-resolve from live profiles_data).
    try:
        req = build_normalized_request(snapshot, inputs)
    except ValueError as exc:                      # malformed inputs shape
        state.error = {"type": "invalid_input", "retryable": False, "message": str(exc)}
        await _finish(Outcome.INVALID_INPUT)
        return
    except (KeyError, TypeError) as exc:           # malformed snapshot config
        state.error = {"type": "config_error", "retryable": False, "message": str(exc)}
        await _finish(Outcome.CONFIG_ERROR)
        return
    try:
        profile = _profile_from_json(snapshot.profile_snapshot)
    except (KeyError, TypeError) as exc:
        state.error = {"type": "config_error", "retryable": False,
                       "message": f"invalid profile_snapshot: {exc}"}
        await _finish(Outcome.CONFIG_ERROR)
        return

    # Context-budget guard — fail closed before any provider call (FR-005, SC-005).
    limit = (profile.limits or {}).get("context_tokens")
    if limit:
        est = count_tokens(req.model, req.messages, tools=req.tools, system=req.system)
        if est > limit:
            state.error = {"type": "context_overflow", "retryable": False,
                           "message": f"estimated {est} tokens exceeds context budget {limit}"}
            await _finish(Outcome.CONTEXT_OVERFLOW)
            return

    outcome = Outcome.SUCCESS

    try:
        # Use the coerced `deadline` (not raw ctx.deadline_s) so a None deadline is still bounded (review
        # F5 — the earlier fix coerced the guard but left this wrapper on the raw value). The same bound is
        # threaded into the gateway/transport as a wire-level timeout (T012 / research D5 — defense in depth).
        async with asyncio.timeout(deadline):
            async for ev in gateway.stream(req, api_key=ctx.api_key, profile=profile, timeout=deadline):
                if ev.type is EventType.TEXT_DELTA:
                    state.text += ev.data.get("text", "")
                elif ev.type is EventType.REASONING_DELTA:
                    # Accumulate reasoning text (US4/FR-007). Re-yielded unchanged below for streaming
                    # consumers; the collected InvokeResult joins the parts.
                    state.reasoning_parts.append(ev.data.get("text", ""))
                elif ev.type is EventType.TOOL_CALL_END:
                    # A completed tool call carries the full {id, name, args}; START/DELTA are streaming
                    # fragments we re-yield but do not re-accumulate. A tool-call-only response is therefore
                    # a non-empty success, not an empty/degraded one (US4/AC2, review C4).
                    state.tool_calls.append(ev.data)
                elif ev.type is EventType.STOP:
                    state.stop_reason = ev.data.get("reason")
                elif ev.type is EventType.USAGE:
                    state.usage = ev.data.get("usage")
                    state.substitutions = tuple(ev.data.get("substitutions", ()))
                    state.cost_is_estimate = bool(ev.data.get("cost_is_estimate", False))
                elif ev.type is EventType.ERROR:
                    # A gateway ERROR is never recorded as success and never dropped (FR-013/P8). A
                    # degraded stream (no usage) is distinct from a provider failure.
                    state.error = ev.data.get("error") or {}
                    # The gateway attaches best-effort preflight downgrades to a usage-less ERROR
                    # (gateway.py degraded branch) — carry them into the structured ledger too so a
                    # degraded run's substitutions are not lost (FR-017, review F1).
                    err_subs = state.error.get("substitutions")
                    if err_subs:
                        state.substitutions = tuple(err_subs)
                    etype = state.error.get("type")
                    outcome = Outcome.DEGRADED if etype == "degraded" else Outcome.PROVIDER_ERROR
                yield ev
    except TimeoutError:
        # Deadline hit mid-stream: stop, keep best-effort partial, record a timeout (FR-009/SC-003/D5).
        outcome = Outcome.TIMEOUT
        state.error = {"type": "timeout", "retryable": False,
                       "message": f"deadline of {deadline}s exceeded"}
    except (gateway.ModelRetiredError, FileNotFoundError) as exc:
        # Snapshot names a retired/unknown model — a caller-visible config error, not a provider failure.
        outcome = Outcome.CONFIG_ERROR
        state.error = {"type": "config_error", "retryable": False, "message": str(exc)}
    finally:
        # Emit exactly one Run on normal completion, a handled terminal (timeout/config), OR a consumer
        # cancel (GeneratorExit when the stream is aclose()'d early) — review F4, FR-011/P8. But if an
        # UNEXPECTED exception is propagating (our own bug), do NOT emit a misleading run — let it surface
        # uncaught (honest failure). `emitted` guards double-emit.
        _inflight = sys.exc_info()[1]
        if _inflight is None or isinstance(_inflight, GeneratorExit):
            await _finish(outcome)


async def invoke(snapshot, inputs, ctx):
    """Streaming entry point — re-surfaces the normalized event stream; emits the RunRecord via the sink.

    The pipeline generator is explicitly closed in a finally so that a consumer which stops early (breaks
    then aclose()s this generator) still triggers the pipeline's run-emitting cleanup (review F4)."""
    state = _State()
    gen = _pipeline(snapshot, inputs, ctx, state)
    try:
        async for ev in gen:
            yield ev
    finally:
        await gen.aclose()


async def invoke_result(snapshot, inputs, ctx) -> InvokeResult:
    """Collecting entry point for non-streaming callers; returns the accumulated InvokeResult."""
    state = _State()
    gen = _pipeline(snapshot, inputs, ctx, state)
    try:
        async for _ in gen:
            pass
    finally:
        await gen.aclose()
    return InvokeResult(
        text=state.text,
        run=state.run,
        tool_calls=tuple(state.tool_calls),
        reasoning="".join(state.reasoning_parts) or None,
        stop_reason=state.stop_reason,
    )
