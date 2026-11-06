"""The runtime invoke spine (M2a feature 003 + M2b feature 007) — the single pure execution path every caller shares.

`runtime.invoke(snapshot, inputs, ctx)` reads an IMMUTABLE VersionSnapshot (never a live row), validates
inputs, runs the call through the M1 gateway, re-surfaces the normalized event stream, bounds the work by
a deadline, and produces exactly ONE honest RunRecord per invocation via the context sink. Pure library
(P6): no web framework, no DB. Fully mocked in tests — no live/paid calls.

M2b (feature 007) — the AI-output trust boundary. Two modes:
  - PASS-THROUGH (text output, no bound tools): stream live, exactly as M2a — no validation, no buffering.
  - BUFFERED VALIDATED (json output OR bound tools): buffer each model turn, validate the final output
    (F1/F2) and each tool call (F3), run a bounded self-correction loop (one repair re-ask for bad output,
    one feed-back re-ask for a rejected tool call; dedicated `ctx.max_corrections` budget, default 2), and
    emit ONLY the final validated events — intermediate correction turns are suppressed from the stream
    (FR-015a) but fully accounted in the one RunRecord (summed usage/cost + `correction_turns`). Unresolved
    defects fail closed with a typed outcome (`output_invalid` / `tool_call_rejected`).

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
from dataclasses import dataclass, field, replace
from decimal import Decimal

from runtime.io import (
    build_correction_messages,
    build_normalized_request,
    tool_schemas_from_snapshot,
    validate_input,
)
from runtime.providers import gateway
from runtime.providers.ir import EventType, NormalizedStreamEvent, NormalizedUsage
from runtime.providers.profiles import Substitution, _profile_from_json
from runtime.providers.validation import (
    RepairNeeded,
    ToolCallRejected,
    validate_output,
    validate_tool_call,
)
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
    correction_turns: int = 0
    run: RunRecord | None = None


@dataclass
class _Turn:
    """One buffered model turn in validated mode (not streamed; collected then validated)."""
    text: str = ""
    reasoning_parts: list = field(default_factory=list)
    tool_calls: list = field(default_factory=list)
    usage: NormalizedUsage | None = None
    substitutions: tuple = ()
    cost_is_estimate: bool = False
    stop_reason: str | None = None
    error: dict | None = None


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
        correction_turns=state.correction_turns,
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


def _sum_usage(a: NormalizedUsage | None, b: NormalizedUsage | None) -> NormalizedUsage | None:
    """Sum two per-turn usages (P7/FR-012 — account every turn, original + corrections)."""
    if a is None:
        return b
    if b is None:
        return a
    return NormalizedUsage(
        input_tokens=a.input_tokens + b.input_tokens,
        output_tokens=a.output_tokens + b.output_tokens,
        cached_input_tokens=a.cached_input_tokens + b.cached_input_tokens,
        cache_write_tokens=a.cache_write_tokens + b.cache_write_tokens,
        reasoning_tokens=a.reasoning_tokens + b.reasoning_tokens,
        cost_usd=a.cost_usd + b.cost_usd,
    )


def _accumulate(state: _State, turn: _Turn) -> None:
    state.usage = _sum_usage(state.usage, turn.usage)
    if turn.substitutions:
        # Dedupe across correction turns: a downgrade that recurs every turn is ONE honest ledger entry,
        # matching the single-turn pass-through path (FR-017) — don't inflate it per correction turn.
        seen = set(state.substitutions)
        state.substitutions = state.substitutions + tuple(s for s in turn.substitutions if s not in seen)
    if turn.cost_is_estimate:
        state.cost_is_estimate = True


def _usage_event(state: _State) -> NormalizedStreamEvent:
    return NormalizedStreamEvent(EventType.USAGE, {
        "usage": state.usage, "substitutions": list(state.substitutions),
        "cost_is_estimate": state.cost_is_estimate,
    })


def _error_event(err: dict) -> NormalizedStreamEvent:
    return NormalizedStreamEvent(EventType.ERROR, {"error": err})


async def _collect_turn(req, ctx, profile, deadline) -> _Turn:
    """Consume ONE full gateway turn into a buffered _Turn (no yielding — validated mode). Mirrors the
    pass-through event handling so a turn's usage/substitutions/error are captured identically."""
    t = _Turn()
    async for ev in gateway.stream(req, api_key=ctx.api_key, profile=profile, timeout=deadline):
        et = ev.type
        if et is EventType.TEXT_DELTA:
            t.text += ev.data.get("text", "")
        elif et is EventType.REASONING_DELTA:
            t.reasoning_parts.append(ev.data.get("text", ""))
        elif et is EventType.TOOL_CALL_END:
            t.tool_calls.append(ev.data)
        elif et is EventType.STOP:
            t.stop_reason = ev.data.get("reason")
        elif et is EventType.USAGE:
            t.usage = ev.data.get("usage")
            t.substitutions = tuple(ev.data.get("substitutions", ()))
            t.cost_is_estimate = bool(ev.data.get("cost_is_estimate", False))
        elif et is EventType.ERROR:
            t.error = ev.data.get("error") or {}
            err_subs = t.error.get("substitutions")
            if err_subs:
                t.substitutions = tuple(err_subs)
    return t


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

    # Trust-boundary mode (M2b): validate when the agent declares JSON output OR binds tools; otherwise
    # pass-through (plain text chat) streams live, unchanged from M2a.
    io = snapshot.io_contract or {}
    output_format = io.get("output_format")
    output_schema = io.get("output_schema")
    validated = output_format == "json" or bool(snapshot.tools)
    tool_schemas = tool_schemas_from_snapshot(snapshot) if snapshot.tools else {}

    outcome = Outcome.SUCCESS

    try:
        # Use the coerced `deadline` (not raw ctx.deadline_s) so a None deadline is still bounded (review
        # F5). The SAME bound wraps the whole invocation — including every correction turn (FR-011) — and is
        # threaded into the gateway/transport as a wire-level timeout (T012 / research D5 — defense in depth).
        async with asyncio.timeout(deadline):
            if not validated:
                # ---- PASS-THROUGH (M2a behavior, unchanged) ----
                async for ev in gateway.stream(req, api_key=ctx.api_key, profile=profile, timeout=deadline):
                    if ev.type is EventType.TEXT_DELTA:
                        state.text += ev.data.get("text", "")
                    elif ev.type is EventType.REASONING_DELTA:
                        state.reasoning_parts.append(ev.data.get("text", ""))
                    elif ev.type is EventType.TOOL_CALL_END:
                        state.tool_calls.append(ev.data)
                    elif ev.type is EventType.STOP:
                        state.stop_reason = ev.data.get("reason")
                    elif ev.type is EventType.USAGE:
                        state.usage = ev.data.get("usage")
                        state.substitutions = tuple(ev.data.get("substitutions", ()))
                        state.cost_is_estimate = bool(ev.data.get("cost_is_estimate", False))
                    elif ev.type is EventType.ERROR:
                        state.error = ev.data.get("error") or {}
                        err_subs = state.error.get("substitutions")
                        if err_subs:
                            state.substitutions = tuple(err_subs)
                        etype = state.error.get("type")
                        outcome = Outcome.DEGRADED if etype == "degraded" else Outcome.PROVIDER_ERROR
                    yield ev
            else:
                # ---- BUFFERED VALIDATED MODE (M2b trust boundary) ----
                messages = req.messages
                budget = ctx.max_corrections
                while True:
                    turn = await _collect_turn(replace(req, messages=messages), ctx, profile, deadline)
                    _accumulate(state, turn)

                    # A gateway failure on any turn fails closed (never recorded as success) (P8).
                    if turn.error:
                        state.error = turn.error
                        etype = turn.error.get("type")
                        outcome = Outcome.DEGRADED if etype == "degraded" else Outcome.PROVIDER_ERROR
                        if state.usage is not None:
                            yield _usage_event(state)
                        yield _error_event(turn.error)
                        break

                    # F3: validate every tool call against the frozen bound-tool schemas.
                    tool_rejections = []
                    for tc in turn.tool_calls:
                        r = validate_tool_call(tc.get("name", ""), tc.get("args") or {}, tool_schemas)
                        if isinstance(r, ToolCallRejected):
                            tool_rejections.append((tc, r))

                    # F1/F2: validate the final json output. A tool-call turn is NOT a final message
                    # (the model chose to call a tool, not answer) — skip output validation so a valid tool
                    # call in a json-mode agent is surfaced, not mis-flagged invalid_json (converge F1,
                    # FR-001 "final model message" / FR-009 valid-call-surfaced).
                    output_defect = None
                    if output_format == "json" and not turn.tool_calls:
                        ov = validate_output(turn.text, output_format="json", output_schema=output_schema)
                        if isinstance(ov, RepairNeeded):
                            output_defect = ov

                    if not tool_rejections and output_defect is None:
                        # SUCCESS — surface only the final validated events; populate state for invoke_result.
                        outcome = Outcome.SUCCESS
                        state.text = turn.text
                        state.tool_calls = list(turn.tool_calls)
                        state.reasoning_parts = turn.reasoning_parts
                        state.stop_reason = turn.stop_reason
                        # Re-emit buffered reasoning so a streaming consumer sees what invoke_result returns
                        # (entry-point parity, FR-016; code-review F3).
                        if turn.reasoning_parts:
                            yield NormalizedStreamEvent(
                                EventType.REASONING_DELTA, {"text": "".join(turn.reasoning_parts)})
                        if turn.text:
                            yield NormalizedStreamEvent(EventType.TEXT_DELTA, {"text": turn.text})
                        for tc in turn.tool_calls:
                            yield NormalizedStreamEvent(
                                EventType.TOOL_CALL_END,
                                {k: tc[k] for k in ("id", "name", "args") if k in tc})
                        if state.usage is not None:
                            yield _usage_event(state)
                        yield NormalizedStreamEvent(EventType.STOP, {"reason": turn.stop_reason or "stop"})
                        break

                    if budget <= 0:
                        # Budget exhausted → fail closed. Note: since output validation is skipped on a
                        # tool-call turn (the `not turn.tool_calls` guard above), output_defect and
                        # tool_rejections are mutually exclusive per turn — this if/else dispatches on which
                        # defect kind actually occurred; the FR-014 "tool over output" precedence is retained
                        # defensively (code-review F5).
                        if tool_rejections:
                            outcome = Outcome.TOOL_CALL_REJECTED
                            rej = tool_rejections[0][1]
                            state.error = {"type": "tool_call_rejected", "retryable": False,
                                           "message": rej.detail}
                        else:
                            outcome = Outcome.OUTPUT_INVALID
                            state.error = {"type": "output_invalid", "retryable": False,
                                           "message": output_defect.detail}
                        if state.usage is not None:   # spend still reported on fail-closed (FR-012/P7)
                            yield _usage_event(state)
                        yield _error_event(state.error)
                        break

                    # Issue one bounded correction turn (suppressed from the stream, FR-015a).
                    budget -= 1
                    state.correction_turns += 1
                    defects = {"output": output_defect, "tools": tool_rejections}
                    messages = build_correction_messages(messages, turn.text, defects,
                                                         available_tools=tuple(tool_schemas))
    except TimeoutError:
        # Deadline hit mid-stream (incl. during a correction): stop, keep best-effort partial, record timeout.
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
