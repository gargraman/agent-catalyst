# Research & Design Decisions — AI-Output Trust Boundary (M2b)

All decisions are grounded in the **current on-disk** runtime (verified 2026-11-06), not the design docs. Key source files: `runtime/invoke.py`, `runtime/io.py`, `runtime/context.py`, `runtime/run.py`, `runtime/snapshot.py`, `runtime/providers/{validation,ir}.py`, `api/routers/invoke.py`, `api/sse.py`, `api/run_sink.py`.

---

### D1 — Where validation happens: two pipeline modes

**Decision**: Split `_pipeline` into **pass-through mode** and **buffered validated mode**.
- **Pass-through** when the snapshot has `io_contract.output_format != "json"` **and** no bound tools (`not snapshot.tools`): stream events live exactly as today. **Zero behavior change** for the plain-chat case.
- **Buffered validated mode** when `output_format == "json"` **or** `snapshot.tools` is non-empty: buffer each model turn's events (text, tool_calls, usage, stop, error), validate at turn end, run the correction loop, then emit only the final validated events.

**Rationale**: Output validation is inherently **post-hoc** — you cannot know a JSON message is valid until it is complete, so you cannot stream it as "valid" token-by-token without risking surfacing invalid output (violating FR-015a/SC-001). Buffering is therefore required wherever validation applies. Confining buffering to json/tool snapshots preserves live streaming for ordinary text chat (the majority case) and keeps the blast radius small.

**Alternatives considered**: (a) stream live then emit a trailing "invalid" error — rejected: it surfaces invalid content on the wire first. (b) always buffer — rejected: needlessly removes live streaming from plain text chat.

**Tradeoff (documented)**: json-output and tool agents lose token-by-token streaming (they emit the validated result as a single text_delta / the validated tool_call_end). Acceptable: structured output is consumed whole, and correctness > streaming granularity for these agents.

---

### D2 — Correction loop structure

**Decision**: Inside the single `async with asyncio.timeout(deadline)` (so the whole invocation incl. corrections is deadline-bounded), run:

```
budget = ctx.max_corrections          # dedicated; default 2
messages = req.messages               # initial
correction_turns = 0
loop:
    turn = await run_one_buffered_turn(replace(req, messages=messages))
    accumulate usage/cost/substitutions from turn
    if turn.error: outcome = provider_error|degraded|timeout ; break      # fail closed, surface error
    defects = collect_defects(turn)   # output (json-mode) + tool-call (bound tools)
    if not defects: outcome = success ; emit_final(turn) ; break
    if budget <= 0: outcome = defect_outcome(defects) ; emit_error(outcome) ; break
    budget -= 1 ; correction_turns += 1
    messages = build_correction_messages(messages, turn, defects)
```

**Rationale**: One bounded loop satisfies US1 (one repair) and US2 (one feed-back) with a single shared budget (clarify decision: dedicated budget, default 2). Keeping it inside the existing timeout makes FR-011 automatic. Each iteration decrements the budget so termination is guaranteed (SC-004).

**defect_outcome precedence**: if a turn has a tool-call defect → `tool_call_rejected`; else (output defect) → `output_invalid`. (A single turn rarely has both a final JSON message and tool calls; precedence is defined for determinism.)

---

### D3 — Correction feed-back message format: plain normalized roles

**Decision**: Build correction turns with **plain normalized `messages`** (role/content), not provider-native tool-result messages:
- **Output repair**: append `{"role":"assistant","content": <invalid raw text>}` then `{"role":"user","content": "<reason>: <errors>. Return ONLY valid JSON matching the schema."}`.
- **Tool-call feed-back**: append `{"role":"assistant","content": <description of the attempted call>}` then `{"role":"user","content": "That tool call was invalid: <reason>. Available tools: <names>. Call a valid tool or answer directly."}`.

**Rationale**: The runtime does **not execute tools** (P2), so provider-native `tool`-role result messages are unnecessary; and the per-adapter serialization of tool-result messages is **not verified to exist** today (the Anthropic adapter passes `tools=req.tools` straight through but there is no evidence of tool-result message plumbing). Plain user-role feedback is provider-agnostic, sufficient for self-correction, and avoids coupling this feature to the deferred P2 tool-execution loop. Provider-native tool-result formatting is **explicitly deferred** with that loop.

**Alternatives considered**: provider-native tool-result messages — rejected: couples to unverified/absent adapter plumbing and to P2 scope for no correctness gain in a no-execution runtime.

---

### D4 — Tool-schema extraction for F3

**Decision**: Build `tool_schemas: dict[str, dict]` (tool name → args JSON-schema) from `snapshot.tools`. For each `ToolRef`, read its provider-shaped tool def `d = toolref.schema_snapshot` and extract:
- `name = d.get("name")`
- `args_schema = d.get("input_schema") or d.get("parameters") or {}` (tolerate Anthropic `input_schema` and OpenAI `parameters`; `{}` ⇒ accept any args, matching the existing `validate_tool_call` "empty schema" behavior)

Only defs **with a name** enter the map. A tool-call naming anything not in the map ⇒ `unknown_tool` (correct — the model can only legitimately call a tool it was given by name).

**Rationale**: `build_normalized_request` sends `[t.schema_snapshot for t in snapshot.tools]` straight to the provider via the adapter (`tools=req.tools or None`), so in real data each `schema_snapshot` is a **provider-shaped tool def carrying a name** (the seeders' bare `{"type":"object",...}` schemas are minimal fixtures that never drive a real tool call). Extraction tolerates both provider dialects so F3 works regardless of the agent's provider.

**Verification note**: tests MUST use properly-named tool defs (e.g. `{"name":"search_runbook","input_schema":{...}}`), not the bare seeder shape, and assert both `unknown_tool` and `bad_args` rejection paths.

---

### D5 — Usage/cost accumulation across turns (P7)

**Decision**: Accumulate a running `NormalizedUsage` total across all turns — sum `input_tokens`, `output_tokens`, `cached_input_tokens`, `cache_write_tokens`, `reasoning_tokens`, and `cost_usd`; union/accumulate `substitutions`; keep `cost_is_estimate` true if any turn was an estimate. The final `RunRecord` carries the summed usage + `correction_turns`.

**Rationale**: FR-012 — every model turn (original + corrections) must be accounted, computed from our profiles (the gateway already attaches our cost per `USAGE` event; we only sum). Never drop or double-count.

---

### D6 — New Outcome values + RunRecord field

**Decision**: Extend `Outcome` with `OUTPUT_INVALID = "output_invalid"` and `TOOL_CALL_REJECTED = "tool_call_rejected"`. Add `correction_turns: int = 0` to `RunRecord`. `BOUNDED_EXIT` stays reserved/unused (clarify decision).

**Rationale**: Closed honest taxonomy (P8); defect-type outcomes give callers a meaningful reason on fail-closed (clarify decision). Default `correction_turns=0` keeps all existing Run construction valid.

---

### D7 — Streaming suppression + final emission (FR-015a)

**Decision**: In validated mode, do **not** yield intermediate turn events. On terminal:
- **success**: emit the validated content as a single `TEXT_DELTA` (the validated text) and/or the valid `TOOL_CALL_END` event(s), then `USAGE` (summed) and `STOP`.
- **fail-closed** (`output_invalid`/`tool_call_rejected`): emit an `EventType.ERROR` carrying `{"error": {"type": <outcome>, "message": ...}}` (and `USAGE` with the summed cost so spend is still reported), no partial/invalid content.

**Rationale**: Suppression is a pure-runtime concern — the API just forwards whatever the pipeline yields (`api/routers/invoke.py` loops `encode_event(ev)`), so emitting an `ERROR` event makes the API produce a typed `error` frame via `sse.encode_event` with no API change. Emitting `USAGE` even on fail-closed keeps spend honest (P7/P8).

---

### D8 — Entry-point parity (FR-016)

**Decision**: Keep **all** logic in `_pipeline`. `invoke` (streaming) yields its events; `invoke_result` (collecting, used by eval) drains it and reconstructs `text`/`tool_calls` from the emitted events exactly as today. No separate code path ⇒ identical outcomes/Runs for equivalent inputs by construction.

---

### D9 — API & persistence changes

**Decision**: **No change** to `api/routers/invoke.py` or `api/sse.py`. The existing stream already: (a) encodes `EventType.ERROR` → `error` frame; (b) has a post-stream fallback (`run.error and not error_emitted → error_frame`); (c) emits `done` with `status = run.outcome.value`. New outcomes flow through unchanged. **Persistence**: `api/run_sink.py` adds `correction_turns` into the persisted `usage` JSON (e.g. `usage={**asdict(rec.usage), "correction_turns": rec.correction_turns}`) — **no migration** (the `runs.usage` column is JSONB). US3 adds a test asserting the HTTP endpoint surfaces the new outcomes as typed `error`+`done`.

**Rationale**: Minimize blast radius; the M4a contract already anticipated runtime-surfaced errors. Folding the count into `usage` keeps the feature migration-free while still persisting it honestly.
