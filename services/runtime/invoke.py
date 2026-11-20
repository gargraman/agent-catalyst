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
from contextlib import aclosing
from dataclasses import asdict, dataclass, field, replace
from decimal import Decimal

from runtime.retrieval.render import CONTEXT_PREFIX
from runtime.io import (
    build_correction_messages,
    build_normalized_request,
    insert_context_message,
    latest_user_text,
    tool_schemas_from_snapshot,
    validate_input,
)
from runtime.providers import gateway
from runtime.providers.resilience import traffic_class
from runtime.providers.ir import EventType, NormalizedStreamEvent, NormalizedUsage
from runtime.providers.profiles import Substitution, _profile_from_json
from runtime.providers.validation import (
    RepairNeeded,
    ToolCallRejected,
    validate_output,
    validate_tool_call,
)
from runtime.retrieval.step import RetrievalStepResult, retrieve, shrink
from runtime.run import BindingSummary, InvokeResult, ModelCallRecord, Outcome, RetrievalSummary, RunRecord
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
    turns: list = field(default_factory=list)   # every model turn actually sent to the gateway (023 per-call records)
    # feature 024 (M7) — always-on retrieval
    retrieval: RetrievalStepResult | None = None
    retrieval_ms: int = 0
    retrieval_query: str = ""
    retrieval_subs: tuple = ()
    retrieval_flags: list = field(default_factory=list)   # run-level degraded markers, e.g. "context_trimmed"
    citations: tuple = ()
    citations_emitted: bool = False
    run_id: str = ""              # minted once per run at pipeline start (US3 detections need it before _build_run)


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
    # 023 per-call bookkeeping (not part of validation logic)
    input_text: str = ""
    correction: bool = False
    t_start: float = 0.0
    t_end: float | None = None
    first_token_ms: int | None = None
    provider_request_id: str | None = None
    done: bool = False                     # the gateway stream ran to completion (not cut / disconnected)
    attempts: int = 0                      # feature 025: transport attempts the gateway reported for this turn


def _turn_cost(turn: "_Turn") -> Decimal:
    """The gateway-attached turn cost as Decimal (0 when the turn reported no usage)."""
    return Decimal(str(turn.usage.cost_usd)) if turn.usage is not None else Decimal(0)


def _render_input(req) -> str:
    """The conversation sent in one turn (incl. repair messages on correction turns), in-process only (023).
    EXCLUDES the system prompt — it is agent config, already identified by `prompt_version` (analysis U2), so it
    neither inflates fingerprints/char counts nor lands in opt-in text. Never raises (FR-009)."""
    try:
        parts = []
        for m in req.messages or ():
            c = m.get("content") if isinstance(m, dict) else m
            if isinstance(c, str) and c.startswith(CONTEXT_PREFIX):
                # 024: retrieved KB passages are customer-confidential and never logged, even under the 023 opt-in
                parts.append(f"[retrieved context withheld: {len(c)} chars]")
                continue
            parts.append(c if isinstance(c, str) else json.dumps(c, sort_keys=True, default=str))
        return "\n".join(parts)
    except Exception:  # noqa: BLE001
        return ""


def _start_turn(state: "_State", req, *, correction: bool, render: bool = True) -> "_Turn":
    # `render=False` when no call sink is wired: the rendered input only feeds per-call records.
    t = _Turn(input_text=_render_input(req) if render else "", correction=correction, t_start=time.perf_counter())
    state.turns.append(t)
    return t


def _profile_hash(profile_snapshot: dict) -> str:
    blob = json.dumps(profile_snapshot or {}, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def _build_run(snapshot, ctx, correlation_id, outcome, state, latency_ms) -> RunRecord:
    usage = state.usage
    # Carried, not recomputed (FR-014). The run cost is the Decimal SUM of the per-turn gateway costs, so the
    # per-call records sum to it exactly (023 FR-005) — a float sum then Decimal would drift (0.1 + 0.2).
    # Feature 024: + every query-embedding cost (embeddings are model calls too; SC-007 cost invariant).
    turn_cost = sum((_turn_cost(t) for t in state.turns), Decimal(0)) if usage is not None else None
    embeds = state.retrieval.embeddings if state.retrieval is not None else ()
    embed_cost = sum((Decimal(e.cost_usd) for e in embeds), Decimal(0))
    cost = (turn_cost or Decimal(0)) + embed_cost if (turn_cost is not None or embeds) else None
    estimate = state.cost_is_estimate or any(e.cost_is_estimate for e in embeds)
    subs = state.substitutions + tuple(x for x in state.retrieval_subs if x not in state.substitutions)
    retrieval = None
    if state.retrieval is not None:
        rr = state.retrieval
        flags = [b.degraded_reason for b in rr.summaries if b.degraded_reason] + state.retrieval_flags
        retrieval = RetrievalSummary(bindings=rr.summaries, searches=rr.searches, embed_calls=len(embeds),
                                     retrieval_ms=state.retrieval_ms, degraded=tuple(dict.fromkeys(flags)))
    # Feature 025 (FR-015): retries are disclosed, never priced — failed attempts reported no usage.
    retries = sum(max(t.attempts - 1, 0) for t in state.turns)
    retries += sum(max(int(getattr(e, "attempts", 1) or 1) - 1, 0) for e in embeds)
    if state.retrieval is not None:
        retries += int(getattr(state.retrieval, "failed_embed_retries", 0) or 0)
    mc = snapshot.model_config or {}
    return RunRecord(
        run_id=state.run_id or ctx.run_id or uuid.uuid4().hex,   # caller-supplied id wins (set into state)
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
        cost_is_estimate=estimate,
        substitutions=subs,
        stop_reason=state.stop_reason,
        error=state.error,
        # 023 US3: first token of the first turn — only set in pass-through (validated turns are buffered).
        first_token_ms=state.turns[0].first_token_ms if state.turns else None,
        retrieval=retrieval,
        retries=retries,
    )


async def _emit_detections(ctx, snapshot, state: "_State", rres) -> None:
    """US3: one record per withheld passage via `ctx.record_detection` — ids + categories only. Best-effort: a failing
    sink never affects the run (contracts/runtime.md §7)."""
    if ctx.record_detection is None:
        return
    for d in getattr(rres, "detections", ()) or ():
        try:
            out = ctx.record_detection({"run_id": state.run_id,
                                        "workspace_id": ctx.workspace_id or snapshot.workspace_id, **d})
            if inspect.isawaitable(out):
                await out
        except Exception:  # noqa: BLE001
            pass


def _citations_event(state: _State) -> NormalizedStreamEvent:
    """Once per run, after the final content and before USAGE (contracts/runtime.md §5). References only — never
    passage text. `knowledge_status` per binding; the API masks denied KBs and drops it on the published plane."""
    state.citations_emitted = True
    rr = state.retrieval
    return NormalizedStreamEvent(EventType.CITATIONS, {
        "knowledge_used": bool(state.citations),
        "citations": [asdict(c) for c in state.citations],
        "knowledge_status": [{"kb_id": b.kb_id, "status": b.status} for b in (rr.summaries if rr else ())],
    })


def _embedding_records(snapshot, ctx, run: RunRecord, provider_corr: str, state: _State) -> list:
    """One ModelCallRecord(role="embedding") per query-embedding call, numbered AFTER all model turns so model-turn
    numbering is unchanged (023 key set unchanged). Text only in-process; the API sink decides what leaves."""
    rr = state.retrieval
    if rr is None:
        return []
    base = len(state.turns)
    return [ModelCallRecord(
        run_id=run.run_id, correlation_id=run.correlation_id, provider_correlation_id=provider_corr,
        workspace_id=run.workspace_id, agent_id=snapshot.agent_id, version_id=snapshot.version_id,
        source=ctx.source, role="embedding", call_seq=base + i, correction=False, provider=prov, model=model,
        prompt_version=snapshot.prompt_hash, input_text=state.retrieval_query, output_text="", latency_ms=lat,
        first_token_ms=None, cost_usd=Decimal(res.cost_usd), cost_known=True, success=True, error_code=None,
        provider_request_id=res.provider_request_id)
        for i, (res, (prov, model, lat)) in enumerate(zip(rr.embeddings, rr.embedding_calls))]


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


async def _collect_turn(req, ctx, profile, deadline, t: _Turn) -> _Turn:
    """Consume ONE full gateway turn into the given buffered _Turn (no yielding — validated mode). Mirrors the
    pass-through event handling so a turn's usage/substitutions/error are captured identically. The _Turn is
    supplied by the caller so a turn cut mid-stream (deadline) still leaves its partial state for its record."""
    async with aclosing(gateway.stream(req, api_key=ctx.api_key, profile=profile, timeout=deadline,
                                       guard=ctx.guard, traffic_class=traffic_class(ctx.source))) as events:
        async for ev in events:
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
                t.provider_request_id = ev.data.get("provider_request_id")
                t.substitutions = tuple(ev.data.get("substitutions", ()))
                t.cost_is_estimate = bool(ev.data.get("cost_is_estimate", False))
                t.attempts = int(ev.data.get("attempts", 1) or 1)
            elif et is EventType.ERROR:
                t.error = ev.data.get("error") or {}
                t.attempts = int(ev.data.get("attempts", 1) or 1)
                err_subs = t.error.get("substitutions")
                if err_subs:
                    t.substitutions = tuple(err_subs)
    t.done = True
    t.t_end = time.perf_counter()
    return t


def _error_outcome(etype) -> Outcome:
    """Gateway ERROR type → run outcome (contract runtime-resilience §8). A failure after the first surfaced event
    stays PROVIDER_ERROR (FR-005); DEGRADED is reserved for usage-less streams."""
    if etype == "degraded":
        return Outcome.DEGRADED
    if etype == "provider_unavailable":
        return Outcome.PROVIDER_UNAVAILABLE
    return Outcome.PROVIDER_ERROR


def _call_record(snapshot, ctx, run: RunRecord, provider_corr: str, seq: int, t: _Turn,
                 outcome: Outcome, now: float) -> ModelCallRecord:
    mc = snapshot.model_config or {}
    if t.error:
        error_code = str(t.error.get("type") or "provider_error")
    elif not t.done:
        error_code = "timeout" if outcome is Outcome.TIMEOUT else "interrupted"
    else:
        error_code = None
    end = t.t_end if t.t_end is not None else now
    return ModelCallRecord(
        run_id=run.run_id,
        correlation_id=run.correlation_id,
        provider_correlation_id=provider_corr,
        workspace_id=run.workspace_id,
        agent_id=snapshot.agent_id,
        version_id=snapshot.version_id,
        source=ctx.source,
        role="judge" if snapshot.version_id == "judge" else "candidate",
        call_seq=seq,
        correction=t.correction,
        provider=mc.get("provider", ""),
        model=mc.get("model", ""),
        prompt_version=snapshot.prompt_hash,
        input_text=t.input_text,
        output_text=t.text,
        latency_ms=max(0, int((end - t.t_start) * 1000)),
        first_token_ms=t.first_token_ms,
        cost_usd=_turn_cost(t),
        cost_known=t.usage is not None,
        success=t.done and t.usage is not None and not t.error,
        error_code=error_code,
        provider_request_id=t.provider_request_id,
    )


async def _emit_calls(ctx, records) -> None:
    """Hand each per-call record to ctx.record_call. Best-effort (FR-009): ANY sink failure is swallowed so
    telemetry can never fail, delay beyond the sink's own work, or alter the run / its stream / its RunRecord."""
    if ctx.record_call is None:
        return
    for rec in records:
        try:
            result = ctx.record_call(rec)
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001 — telemetry is best-effort by contract
            pass


async def _pipeline(snapshot, inputs, ctx, state: _State):
    state.run_id = ctx.run_id or uuid.uuid4().hex     # per run — never written back to a (possibly shared) ctx
    """Core generator: yields the surfaced events; builds + emits the RunRecord exactly once at the end."""
    t0 = time.perf_counter()
    correlation_id = ctx.correlation_id or uuid.uuid4().hex
    # 023 FR-014: providers only ever see a PLATFORM-GENERATED id. Prefer the edge's; else reuse the id we just
    # minted (it is ours); else (caller-supplied correlation id, no edge provider id) mint a fresh one.
    provider_corr = ctx.provider_correlation_id or (
        correlation_id if ctx.correlation_id is None else uuid.uuid4().hex)

    emitted = False

    async def _finish(outcome):
        nonlocal emitted
        if emitted:  # exactly one Run per invocation, even if called from both a guard and the finally
            return
        emitted = True
        now = time.perf_counter()
        latency_ms = int((now - t0) * 1000)
        state.run = _build_run(snapshot, ctx, correlation_id, outcome, state, latency_ms)
        # The billable RunRecord goes first: per-call telemetry is best-effort (FR-009) and must never delay or
        # prevent it — so record construction is guarded too, not just the sink call.
        await _emit(ctx, state.run)
        if (state.turns or state.retrieval is not None) and ctx.record_call is not None:
            try:
                records = [_call_record(snapshot, ctx, state.run, provider_corr, i, t, outcome, now)
                           for i, t in enumerate(state.turns)]
                records += _embedding_records(snapshot, ctx, state.run, provider_corr, state)
            except Exception:  # noqa: BLE001 — telemetry is best-effort by contract
                records = []
            await _emit_calls(ctx, records)

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
    req = replace(req, provider_correlation_id=provider_corr)   # 023: platform id rides every turn (incl. corrections)
    try:
        profile = _profile_from_json(snapshot.profile_snapshot)
    except (KeyError, TypeError) as exc:
        state.error = {"type": "config_error", "retryable": False,
                       "message": f"invalid profile_snapshot: {exc}"}
        await _finish(Outcome.CONFIG_ERROR)
        return

    # Feature 024 — always-on retrieval, ONCE, before the first model turn (contracts/runtime.md §3). Query = the
    # latest user message; the rendered context goes in as a user-role message right before it. Bounded by the run
    # deadline; any problem degrades the bindings, never the run (FR-020). Tool-mode bindings retrieve always-on
    # (recorded substitution) until the tool loop exists (US4).
    refs = tuple(snapshot.retrieval or ())
    base_messages = req.messages
    if refs:
        if any(r.injection == "tool" for r in refs):
            state.retrieval_subs += (Substitution("retrieval_tool", "downgraded",
                                                  "tool-mode retrieval not available yet; retrieved always-on"),)
        state.retrieval_query = latest_user_text(base_messages)
        tr = time.perf_counter()
        billed: list = []                              # embeddings that completed before any cancellation
        try:
            rres = await asyncio.wait_for(retrieve(state.retrieval_query, refs, ctx, sink=billed), timeout=deadline)
        except TimeoutError:
            rres = RetrievalStepResult("", (), (), tuple(
                BindingSummary(r.kb_id, r.kb_index_version, "degraded", degraded_reason="retrieval_timeout")
                for r in refs), (), tuple(e for e, _ in billed), embedding_calls=tuple(c for _, c in billed))
        elapsed = time.perf_counter() - tr
        state.retrieval_ms = int(elapsed * 1000)
        state.retrieval_subs += tuple(rres.substitutions)
        state.retrieval = rres
        await _emit_detections(ctx, snapshot, state, rres)
        req = replace(req, messages=insert_context_message(base_messages, rres.rendered))
        deadline = deadline - elapsed                 # retrieval spends from the same wall-clock budget
        if deadline <= 0:
            state.error = {"type": "timeout", "retryable": False, "message": "deadline elapsed during retrieval"}
            await _finish(Outcome.TIMEOUT)
            return

    # Context-budget guard — fail closed before any provider call (FR-005, SC-005). Feature 024 (owner decision
    # 2026-11-14): retrieved context is trimmed FIRST — drop the lowest-scoring passages until it fits; only a
    # request that overflows with zero passages fails.
    limit = (profile.limits or {}).get("context_tokens")
    if limit:
        est = count_tokens(req.model, req.messages, tools=req.tools, system=req.system)
        while est > limit and state.retrieval is not None and state.retrieval.passages_used:
            state.retrieval = shrink(state.retrieval, len(state.retrieval.passages_used) - 1)
            req = replace(req, messages=insert_context_message(base_messages, state.retrieval.rendered))
            est = count_tokens(req.model, req.messages, tools=req.tools, system=req.system)
            if "context_trimmed" not in state.retrieval_flags:
                state.retrieval_flags.append("context_trimmed")
        if est > limit:
            state.error = {"type": "context_overflow", "retryable": False,
                           "message": f"estimated {est} tokens exceeds context budget {limit}"}
            await _finish(Outcome.CONTEXT_OVERFLOW)
            return
    if state.retrieval is not None:
        state.citations = state.retrieval.citations

    # Trust-boundary mode (M2b): validate when the agent declares JSON output OR binds tools; otherwise
    # pass-through (plain text chat) streams live, unchanged from M2a.
    io = snapshot.io_contract or {}
    output_format = io.get("output_format")
    output_schema = io.get("output_schema")
    validated = output_format == "json" or bool(snapshot.tools)
    tool_schemas = tool_schemas_from_snapshot(snapshot) if snapshot.tools else {}

    outcome = Outcome.SUCCESS
    terminal = False     # set once the model reached a terminal answer — a later consumer cancel doesn't undo it

    try:
        # Use the coerced `deadline` (not raw ctx.deadline_s) so a None deadline is still bounded (review
        # F5). The SAME bound wraps the whole invocation — including every correction turn (FR-011) — and is
        # threaded into the gateway/transport as a wire-level timeout (T012 / research D5 — defense in depth).
        async with asyncio.timeout(deadline):
            if not validated:
                # ---- PASS-THROUGH (M2a behavior, unchanged) ----
                turn = _start_turn(state, req, correction=False, render=ctx.record_call is not None)
                async with aclosing(gateway.stream(req, api_key=ctx.api_key, profile=profile, timeout=deadline,
                                                   guard=ctx.guard, traffic_class=traffic_class(ctx.source))) as events:
                    async for ev in events:
                        if ev.type is EventType.TEXT_DELTA:
                            if turn.first_token_ms is None:   # 023 US3: first streamed token of this turn
                                turn.first_token_ms = int((time.perf_counter() - turn.t_start) * 1000)
                            state.text += ev.data.get("text", "")
                            turn.text += ev.data.get("text", "")
                        elif ev.type is EventType.REASONING_DELTA:
                            state.reasoning_parts.append(ev.data.get("text", ""))
                        elif ev.type is EventType.TOOL_CALL_END:
                            state.tool_calls.append(ev.data)
                        elif ev.type is EventType.STOP:
                            state.stop_reason = ev.data.get("reason")
                        if (state.retrieval is not None and not state.citations_emitted
                                and ev.type in (EventType.USAGE, EventType.ERROR, EventType.STOP)):
                            yield _citations_event(state)
                        if ev.type is EventType.USAGE:
                            state.usage = ev.data.get("usage")
                            turn.usage = state.usage
                            turn.provider_request_id = ev.data.get("provider_request_id")
                            state.substitutions = tuple(ev.data.get("substitutions", ()))
                            state.cost_is_estimate = bool(ev.data.get("cost_is_estimate", False))
                            turn.attempts = int(ev.data.get("attempts", 1) or 1)
                        elif ev.type is EventType.ERROR:
                            state.error = ev.data.get("error") or {}
                            turn.error = state.error
                            turn.attempts = int(ev.data.get("attempts", 1) or 1)
                            err_subs = state.error.get("substitutions")
                            if err_subs:
                                state.substitutions = tuple(err_subs)
                            etype = state.error.get("type")
                            outcome = _error_outcome(etype)
                        yield ev
                turn.done = True
                turn.t_end = time.perf_counter()
                if state.retrieval is not None and not state.citations_emitted:
                    yield _citations_event(state)
            else:
                # ---- BUFFERED VALIDATED MODE (M2b trust boundary) ----
                messages = req.messages
                budget = ctx.max_corrections
                while True:
                    turn_req = replace(req, messages=messages)
                    turn = await _collect_turn(turn_req, ctx, profile, deadline,
                                               _start_turn(state, turn_req, correction=bool(state.turns),
                                                           render=ctx.record_call is not None))
                    _accumulate(state, turn)

                    # A gateway failure on any turn fails closed (never recorded as success) (P8).
                    if turn.error:
                        state.error = turn.error
                        etype = turn.error.get("type")
                        outcome = _error_outcome(etype)
                        if state.retrieval is not None and not state.citations_emitted:
                            yield _citations_event(state)
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
                        terminal = True
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
                        if state.retrieval is not None and not state.citations_emitted:
                            yield _citations_event(state)
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
                        if state.retrieval is not None and not state.citations_emitted:
                            yield _citations_event(state)
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
        # The gateway raises BEFORE any provider call, so the turn just opened made no model call (023: no record).
        if state.turns and not state.turns[-1].done:
            state.turns.pop()
        outcome = Outcome.CONFIG_ERROR
        state.error = {"type": "config_error", "retryable": False, "message": str(exc)}
    finally:
        # Emit exactly one Run on normal completion, a handled terminal (timeout/config), OR a consumer
        # cancel (GeneratorExit when the stream is aclose()'d early) — review F4, FR-011/P8. But if an
        # UNEXPECTED exception is propagating (our own bug), do NOT emit a misleading run — let it surface
        # uncaught (honest failure). `emitted` guards double-emit.
        # 026 review-2 #5: a task cancel (CancelledError — e.g. the ASGI server cancelling on client disconnect) is
        # a consumer cancel too, and the provider may already have billed: emit it, honestly labelled `cancelled`
        # unless the run had already reached a terminal outcome.
        _inflight = sys.exc_info()[1]
        if isinstance(_inflight, (GeneratorExit, asyncio.CancelledError)):
            if outcome is Outcome.SUCCESS and not terminal:
                outcome = Outcome.CANCELLED
            await _finish(outcome)
        elif _inflight is None:
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
        citations=state.citations,
    )
