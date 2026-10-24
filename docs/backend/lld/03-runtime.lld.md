# 03 (LLD) — Runtime (LangGraph orchestration, the shared spine)

> **Parent:** [`../03-runtime.md`](../03-runtime.md) (graph shape, `GraphState`, node bodies, edges,
> streaming bridge, memory, tool execution, sub-agents).
> The parent fully specifies the graph *wiring* (every `add_node`/`add_edge`) and most node bodies, but
> leaves `runtime.invoke()` itself (the top-level entry it defers to `00 §4`), ~20 node helpers, node-level
> error handling, the sub-agent fan-out *mechanism*, and the memory-compaction *trigger* to the implementer —
> and `GraphState` is missing two fields its own nodes read. This doc fills those.

## What this adds over parent `03`

| Gap in `03` | Filled here |
|---|---|
| `GraphState` (`§3`) lacks `pending_tool_calls` (read by `tool_approval_gate`/`execute_tools`, `§4`) and `session_id` (needed by the checkpointer `thread_id`, `§6`) | §1 — the corrected `GraphState` (the subtask-1 amendment, now applied) |
| `runtime.invoke()` referenced (`§5`, `00 §4`) — drains the emit queue, runs the 7-step pipeline — **no body** | §2 — the top-level async generator |
| "`inputs` validated vs `io_contract`" asserted, algorithm absent | §3 — `validate_inputs` with coercion/defaults |
| ~20 node helpers named, undefined (`render_prompt`, `compose_skill_fragments`, `apply_memory_window`, `reduce_model_turn`, `resolve_binding`, `resolve_provider_key`, `redact`, the routing predicates…) | §4 — signatures + the non-obvious bodies |
| no node-level error handling (tool raises / gateway exhausted / checkpointer write fails) | §5 — the failure model per node |
| sub-agent `max_fanout` caps *count*; the concurrent-launch mechanism + tool-time interaction undefined | §6 — bounded `gather`, shared budget |
| memory compaction "summarize oldest turns" — no trigger threshold/formula/model | §7 — the trigger + summarize path |
| crash-mid-run resume vs restart undefined | §8 — resume & idempotency semantics |

Python 3.12, `from __future__ import annotations`. Snippets compile (§9). `VersionSnapshot`/`RunContext`/
`InvokeInputs` (incl. `tool_time_left`/`past_deadline`/`child`/`session_id`): see
[`01-data-model.lld.md`](01-data-model.lld.md). `gateway.stream`/`build_normalized_request`: see
[`02-provider-gateway.lld.md`](02-provider-gateway.lld.md). `InputInvalid`/`ProviderUnavailable`: see
[`00-conventions.lld.md §1`](00-conventions.lld.md). Graph edges + node bodies: **see `03` — not restated.**

---

## 1. Corrected `GraphState` (applies the subtask-1 amendment)

`03 §3`'s `GraphState` is missing two keys its own `§4` nodes read. This is the corrected definition the
`03` graph should use:

```python
from __future__ import annotations
from typing import TypedDict, Annotated
from operator import add


class GraphState(TypedDict):
    snapshot: "VersionSnapshot"            # frozen config (01) — read-only
    ctx: "RunContext"                      # tenancy, budget, trace, session_id (01 LLD §2)
    messages: Annotated[list[dict], add]   # conversation, appended across tool cycles
    inputs: "InvokeInputs"                 # declared typed vars (validated vs io_contract, §3)
    retrieved: list[dict]                  # chunks used (citations, 04)
    pending_tool_calls: list["ToolCall"]   # ADDED — set by model_call, read by tool_approval_gate/execute_tools (03 §4)
    tool_calls_made: int                   # loop guard
    usage: "NormalizedUsage"               # accumulated across model calls (02/07)
    substitutions: list["Substitution"]    # recorded capability leaks (02 §5)
    guardrail_outcome: str | None          # e.g. "PII blocked on input" (06 → runs.guardrail)
    stop_reason: str | None
```

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class ToolCall:
    id: str                 # model-emitted tool-call id (the tool_approvals.tool_call_id it gates, 01 §4.6)
    name: str               # resolved tool ref (matches a ToolBindingSnap.name, 01 LLD §1)
    args: dict
```

- **`session_id`** is **not** a `GraphState` field — it rides on `ctx` (`RunContext.session_id`, `01` LLD §2),
  which the checkpointer reads to build `thread_id = f"{ws}:{agent}:{session}"` (`03 §6`). This resolves the
  subtask-1 flag: one source of truth (`ctx`), not duplicated into state.
- **`pending_tool_calls`** is populated by `reduce_model_turn` (`§4`) from the accumulated `TOOL_CALL_*` events.

> ⚠️ **Amendment to `03 §3`** (first flagged in `01` LLD §2): add `pending_tool_calls` to `GraphState`; source
> `session_id` from `ctx`. Recorded as a callout for the `03` parent reconciliation, applied here.

---

## 2. `runtime.invoke()` — the top-level entry (fills `00 §4` / `03 §5`)

The one function all four callers wrap (`00 §1`). It's an async generator: build state → validate → reserve
budget → run the graph, draining its emit queue → reconcile → persist. The whole thing runs under
`ctx.deadline_s`.

```python
from __future__ import annotations
import asyncio
from collections.abc import AsyncIterator


async def invoke(snapshot, inputs, ctx) -> AsyncIterator["NormalizedStreamEvent"]:
    # 1. validate inputs vs the snapshot IO contract (§3) — raises InputInvalid (400) BEFORE any spend
    validate_inputs(inputs, snapshot.io_contract)
    # 2. budget pre-check + reservation (07 §3) — raises BudgetExceeded (402) before any model call.
    #    reserve() is async (07 §2, Redis-backed) — MUST be awaited or the 402 check never runs.
    reservation = await ctx.budget.reserve(ctx.workspace_id, est_usd=_estimate(snapshot, inputs))
    state = _initial_state(snapshot, inputs, ctx)
    queue: asyncio.Queue = asyncio.Queue()
    graph = build_graph()                               # 03 §4 (compiled with the checkpointer)
    run_rec = _open_run(ctx, snapshot)                  # runs row (01 §4.7), status pending

    async def _run():
        # bind the emit queue so node emit() calls land here; config carries the checkpointer thread_id (§6)
        cfg = {"configurable": {"thread_id": _thread_id(ctx, snapshot), "emit": queue.put}}
        await graph.ainvoke(state, config=cfg)
        await queue.put(_SENTINEL)

    task = asyncio.create_task(_run())
    acc_usage = NormalizedUsage(0, 0)                  # ACCUMULATE across model calls — a running total survives a
                                                       #   mid-run timeout; a single terminal USAGE would be lost on cancel.
    try:
        async with asyncio.timeout(ctx.tool_time_left()):  # remaining run budget from ctx creation (01 LLD §2) —
                                                           #   counts the prelude, so it agrees with past_deadline()
            while (ev := await queue.get()) is not _SENTINEL:
                if ev.type is EventType.USAGE:
                    acc_usage = _add_usage(acc_usage, ev.data["usage"])  # cost already attached by the gateway (02 §7)
                yield ev                                 # HTTP layer (08) re-encodes 1:1 as SSE
    except TimeoutError:
        task.cancel()                                   # abort in-flight stream; acc_usage keeps every completed call (03 §5)
        yield _stop_event("deadline_exceeded")
    finally:
        # 6/7. persist Run + Spans (10), reconcile against actual cost (07 §3) — ALWAYS, even on deadline/
        #      cancel/error, so a partial run is still billed and traced. reconcile() is async (07 §2) → await.
        _close_run(run_rec, state, acc_usage, status=_run_status(state))
        await ctx.budget.reconcile(reservation, actual_usd=_actual_cost(acc_usage))
```

- **Emit-queue drain** (`03 §5`): nodes `emit()` into the queue; `invoke` yields from it as events arrive, so
  first-token streams before the node finishes (p95 < 2s).
- **Deadline** wraps the graph task (`asyncio.timeout`), sized to the *remaining* budget from `ctx` creation
  (`ctx.tool_time_left()`) so it counts the prelude and agrees with the loop-level `past_deadline()`. On expiry
  the task is cancelled and a `deadline_exceeded` stop is emitted — matching `03 §5`.
- **Partial usage on timeout:** `acc_usage` sums every `USAGE` event that *completed* before the cut, so a
  timed-out multi-call run is still billed for the calls that finished (not ~$0). **Residual:** tokens consumed
  inside the *one* model call that was mid-flight when cancelled are not yet in a `USAGE` event and are lost —
  capturing them needs the gateway to flush a partial `USAGE` on cancel (a `02` follow-up, noted here).
- **Budget** brackets the run: `await reserve` before (402 fail-fast), `await reconcile` in `finally` (always) —
  both are async (`07 §2`); un-awaited, neither guard runs.
- **`finally` always persists** — a deadline/cancel/error run is still a `runs` row with partial
  cost/tokens/status, never lost (10).

---

## 3. Input validation vs `io_contract` (fills `03 §3`)

```python
def validate_inputs(inputs: "InvokeInputs", io: "IOContract") -> None:
    """Validate declared typed vars against the snapshot's io_contract (01 LLD §3). Raises InputInvalid (400,
    00-conventions §1) naming the offending var — the published request-schema contract (spec B2.5)."""
    provided = inputs.inputs                             # mutated in place BY DESIGN: resolves defaults into the
                                                        #   inputs the run will use (one resolved dict downstream)
    for var in io.input_variables:                      # InputVariable: name/type/required/default (01 LLD §3)
        if var.name not in provided:
            if var.required and var.default is None:
                raise InputInvalid(f"missing required input: {var.name}", detail={"var": var.name})
            if var.default is None:
                continue                                # optional, no default, not provided → leave absent
            provided[var.name] = var.default            # resolve the default into inputs
        _coerce(var, provided[var.name])                # coerce in BOTH cases — a defaulted value is typed too
    extra = set(provided) - {v.name for v in io.input_variables}
    if extra:
        raise InputInvalid("unknown input variables", detail={"unknown": sorted(extra)})


def _coerce(var, value):
    t = var.type                                        # "string"|"number"|"boolean"|"enum"
    if t == "number" and not isinstance(value, (int, float)):
        raise InputInvalid(f"{var.name} must be a number", detail={"var": var.name, "got": type(value).__name__})
    if t == "boolean" and not isinstance(value, bool):
        raise InputInvalid(f"{var.name} must be a boolean", detail={"var": var.name})
    # "string"/"enum" accept str; enum membership is validated against the declared options (P1: free string)
```

Validation happens in `invoke()` step 1 — **before** budget reservation and any model call, so a malformed
request costs nothing and returns a clean 400 (`08` maps it).

---

## 4. Node helper signatures (the undefined ~20)

The `03 §4` node bodies call these; here are the signatures + the non-obvious bodies. Trivial ones
(`last_user_text`, `sys_msg`, `stuff_context`) are one-liners and omitted.

```python
def render_prompt(system_prompt: str, inputs: "InvokeInputs") -> str:
    """Jinja-SAFE template: fill {{var}} from inputs.inputs with autoescape; undefined → error, never silent
    blank (a missing var is a contract bug, caught in §3 already). No arbitrary code — sandboxed environment."""

def compose_skill_fragments(skills: tuple["SkillSnap", ...]) -> list[dict]:
    """Each bound skill (01 LLD §1 SkillSnap) → an instruction message, injected like system_prompt (03 §4).
    Content is UNTRUSTED (09 §10): no secret interpolation; output redaction (06) still applies downstream."""
    return [{"role": "system", "content": s.instructions} for s in skills]

async def apply_memory_window(messages, mc, *, snapshot, ctx) -> list[dict]:   # full body in §7
    """Trim/compact history to the memory window (01 LLD §3 MemoryConfig). ASYNC (it may summarize via the
    cheap model, §7), so the parent `build_messages` must `await` it and pass snapshot+ctx — see amendment."""

def reduce_model_turn(state: GraphState) -> dict:
    """After model_call streams: if tool calls were emitted, set pending_tool_calls (§1) and loop; else set
    stop_reason='final'. Returns the state delta."""

def resolve_binding(snapshot, call: "ToolCall") -> "ToolBindingSnap":
    """Match a model tool call to its frozen binding (01 LLD §1). Unknown tool name → the model hallucinated a
    tool → return an error result to the model (§5), not a crash."""

async def resolve_provider_key(ctx, snapshot) -> str:
    """Decrypt the workspace provider key from Vault into memory for this call only (09); never logged, never
    persisted. For per-user MCP (17) the key is the caller's vaulted token."""

def redact(args: dict) -> dict:
    """Scrub secrets from tool args before they land in a tool_approvals row / span (09 §9)."""
```

Routing predicates (`03 §4` edges) — the exact conditions:

```python
def _retrieval_route(s: GraphState) -> str:
    # any binding with injection == "always_on" → retrieve+stuff; else tool/none → straight to model
    return "always_on" if any(b.injection == "always_on" for b in s["snapshot"].retrieval_bindings) else "model"

def _after_model(s: GraphState) -> str:
    return "tools" if s["pending_tool_calls"] else "final"

def _after_gate(s: GraphState) -> str:
    return "denied" if s.get("stop_reason") == "approval_denied" else "execute"
```

---

## 5. Node-level error handling (the gap)

`03 §4` node bodies show no `try/except`. The failure model per node:

| Failure | Where | Handling |
|---|---|---|
| `gateway.stream` raises `ProviderUnavailable` (retries + fallback exhausted, `02 §6`) | `model_call` | catch → set `stop_reason="error"`, emit an `ERROR` event, let the run end `run_status='error'`. **Not** an HTTP 4xx/5xx — the stream already started; it's a run outcome (`00-conventions §1` cat. C). |
| a tool raises / times out | `execute_tools` | catch **per tool** → return an error *result* to the model (`{"error": "..."}`) so the model can react/recover, not a run crash. A tool that blows the per-run `tool_time_left()` → `stop_reason="deadline_exceeded"`. |
| `resolve_binding` finds no binding (model hallucinated a tool) | `execute_tools` | return a tool-not-found error result to the model (same as above) — never a 500. |
| checkpointer write fails at an **approval interrupt** | `tool_approval_gate` | fatal for *that* run: a run that can't be durably parked can't be resumed, so fail **closed** — `stop_reason="approval_denied"` (deny the gated tool) rather than execute an unapproved destructive call. Alert ops (10). |
| `OutputBlocked` from a guardrail stage (`06`) | `output_guardrails` | caught → `run_status='blocked'`, `stop_reason='blocked'` (`00-conventions §1` cat. B→C); HTTP stays 200 with a blocked outcome in the stream. |

Principle: **inside the graph, provider/tool failures become run outcomes, not exceptions out of `invoke()`.**
The only things that escape `invoke()` as exceptions are the *pre-stream* guards (`InputInvalid`,
`BudgetExceeded`) — once streaming starts, every failure is a `stop_reason` + the `finally` persist.

---

## 6. Sub-agent fan-out concurrency (fills `03 §8`)

`03 §8`: a sub-agent call is `runtime.invoke(run_source="subagent", ctx=ctx.child())`; `max_depth` (2) and
`max_fanout` (3) cap it. The *mechanism* for launching several children in one turn:

```python
import asyncio

async def run_subagent_calls(calls: list["ToolCall"], snapshot, ctx) -> list[dict]:
    """Launch sub-agent tool calls for ONE turn, bounded by max_fanout and the shared tool-time budget."""
    cfgs = [resolve_subagent_config(snapshot, c) for c in calls]     # subagent_configs (01 §4.6)
    max_fanout = min(cfg.max_fanout for cfg in cfgs) if cfgs else 3
    if len(calls) > max_fanout:
        return [_stop_result("subagent_fanout_exceeded")]           # 03 §8 cap → run stop_reason
    if ctx.depth >= max(cfg.max_depth for cfg in cfgs):
        return [_stop_result("subagent_depth_exceeded")]
    budget = ctx.tool_time_left()                                    # children SHARE the parent's remaining budget (01 LLD §2)

    async def _one(call):
        child = ctx.child()                                         # depth+1, parent_run_id, decremented budget (01 LLD §2)
        async with asyncio.timeout(budget):                        # a child can't outlive the parent deadline
            return await _drain_child(invoke(_child_snapshot(call), _child_inputs(call), child))

    # No semaphore: the fanout cap above already guarantees len(calls) ≤ max_fanout, so all survivors run
    # concurrently. return_exceptions=True → one slow/failed child can't wedge the turn (becomes a result).
    return await asyncio.gather(*(_one(c) for c in calls), return_exceptions=True)
```

- **Concurrency = `asyncio.gather`** over the survivors. The `max_fanout` cap is a **hard guard** (exceeding it
  is a `stop_reason`, `03 §8`), not a throttle — so a semaphore would be dead weight; the ≤`max_fanout` children
  simply run concurrently.
- **Shared budget:** every child runs under `asyncio.timeout(ctx.tool_time_left())`, so fan-out can't exceed
  the parent's remaining wall-clock (the exact concern `03 §8` raises — 8 serial tools × 10s would blow 30s).
- **`return_exceptions=True`**: a child crash is a result the parent model sees, not a turn-killer (mirrors the
  per-tool error handling, §5).
- **Tenant/depth** inherited via `ctx.child()` (`01` LLD §2) — a sub-agent can never cross tenant (`09 §8`).

---

## 7. Memory compaction (fills `03 §6`)

`03 §6`: "compaction runs in `build_messages` when the window would exceed the context budget — summarize
oldest turns with the cheap tier model." The trigger + path:

```python
COMPACTION_HEADROOM = 0.7      # compact when history would use > 70% of the context budget

async def apply_memory_window(messages: list[dict], mc: "MemoryConfig", *, snapshot, ctx) -> list[dict]:
    recent = messages[-mc.window_turns:] if mc.window_turns else messages   # simple cap first (MemoryConfig, 01 LLD §3)
    budget = _context_budget(snapshot)                                       # profile.limits["context_tokens"] (dict, 02) minus headroom; honors context_config.window_limit if set (01 LLD §3)
    if _tokens(recent) <= budget * COMPACTION_HEADROOM:
        return recent                                                       # common case: no compaction
    if mc.compaction == "drop_oldest":
        return _drop_oldest_until(recent, budget * COMPACTION_HEADROOM)     # cheap, lossy
    # "summarize": fold the oldest turns into one summary message using the CHEAP tier model (07), not the
    # agent's model — compaction cost must not scale with the agent's per-token price.
    old, keep = _split_for_summary(recent, budget * COMPACTION_HEADROOM)
    summary = await _summarize(old, model=_cheap_tier_model(ctx), gateway_stream=gateway.stream)
    return [{"role": "system", "content": f"[earlier conversation summary] {summary}"}, *keep]
```

- **Trigger:** `tokens(history) > 0.7 × context_budget`, where the budget is the model profile's
  `limits["context_tokens"]` (a **dict**, `02 §4` — not attribute access) minus retrieved-context + tool-schema
  headroom, and capped by `context_config.window_limit` when the version sets one (`01` LLD §3, `03 §7`).
- **Model:** the cheap tier (`07`), via the same `gateway.stream` — so a summary is costed/logged like any
  call, and compaction never pays the agent-model token price.
- **`drop_oldest`** is the lossy fallback (`MemoryConfig.compaction`); default is `summarize`.
- A `context_truncation` span attr is recorded either way (`03 §7`).

> ⚠️ **Amendment to `03 §4`:** `build_messages` calls `apply_memory_window(state["messages"],
> state["snapshot"].memory_config)` **synchronously**. Since compaction may summarize via the cheap model, the
> helper is `async` and needs `snapshot`+`ctx` — so `build_messages` must become
> `history = await apply_memory_window(state["messages"], mc, snapshot=state["snapshot"], ctx=state["ctx"])`.
> Flagged for the `03` parent reconciliation, not edited there.

---

## 8. Resume & idempotency semantics (fills `03 §6` durability)

`03 §6` says the Postgres checkpointer makes a run durable; it doesn't say when a crashed run *resumes* vs
*restarts*. The rules, by caller:

| Caller | Crash mid-run | Why |
|---|---|---|
| **Playground** (`stream=True`, interactive) | **not** auto-resumed — the client reconnects → a fresh run | a dangling half-stream has no consumer; the human just re-sends |
| **Published invoke** with `Idempotency-Key` | a client retry **replays** the stored result (if complete) or runs fresh (`00-conventions §3`) | the idempotency store, not the checkpointer, is the dedupe authority |
| **Approval-parked run** (`interrupt()`, `03 §4`) | **resumable** across process restarts via the checkpointer + `Command(resume=decision)` | the whole point of durable interrupts — a human may decide minutes later |
| **Eval worker** case | the job retries the case fresh (`allow_cache=False`, `05 §7`) | determinism of the *verdict*, not of a single run, is what matters |

So the checkpointer's durability is load-bearing specifically for the **approval-parked** path; interactive
runs rely on reconnect, published invoke on the idempotency store, eval on job retry. Conflating these (e.g.
auto-resuming a playground run) would replay tokens to a client that's gone.

---

## 9. Verification performed

1. **Snippet compile** — every `python` block via `compile()` (same harness as `01`/`00`/`02`/`05` LLD).
2. **Cross-ref** — against `03` read in full: `GraphState`/nodes/edges (`§3`/`§4`), streaming bridge +
   deadline (`§5`), memory/checkpointer (`§6`), context (`§7`), tool exec + sub-agents (`§8`); `01` LLD
   (`VersionSnapshot`/`RunContext`/`InvokeInputs`/`MemoryConfig`/`SkillSnap`/`ToolBindingSnap`); `02` LLD
   (`gateway.stream`/`build_normalized_request`/profile limits); `00-conventions §1`/`§3` (`InputInvalid`/
   `ProviderUnavailable`/idempotency); `05 §7` (eval caller); `06` (`OutputBlocked`, guardrail runner);
   `07` (cheap tier, budget reserve/reconcile).
3. **Amendment applied** — the subtask-1 `GraphState` flag (`pending_tool_calls` + `session_id`-via-`ctx`) is
   resolved here (§1); re-recorded as the callout for the `03` parent reconciliation pass.

→ Next LLD: `08-api-surface.lld.md` (subtask 6).
