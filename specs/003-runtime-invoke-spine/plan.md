# Implementation Plan: Runtime Invoke Spine (M2a)

**Branch**: `003-runtime-invoke-spine` (work continues on `feat/backend-m0-foundation` — no git hook) | **Date**: 2026-11-04 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/003-runtime-invoke-spine/spec.md`

## Summary

Build the single, pure `runtime.invoke` execution path that every caller (playground, eval, published
invoke, channel) shares. It reads an **immutable `VersionSnapshot`** (never the live agent row),
validates caller inputs and the context budget, assembles a `NormalizedRequest`, runs it through the
**M1 provider gateway**, re-surfaces the full normalized event stream (content / reasoning / tool-call /
stop), bounds the work by an explicit deadline, and produces **exactly one honest `RunRecord`** per
invocation — success, failure, timeout, or degradation alike. Fully mocked (no live/paid calls). The
AI-output trust boundary (F1/F2/F3) is **M2b**; the first live call is **Phase 5**.

Technical approach is resolved in [research.md](./research.md): a **plain async pipeline** (not LangGraph)
of discrete, testable steps; the Run delivered via a **sink on the context** (keeps the runtime
I/O-free); a conservative **token-count** guard; and a **runtime-level async deadline**.

## Technical Context

**Language/Version**: Python 3.12 (project venv `services/.venv`, 3.12.13).

**Primary Dependencies**: standard library `asyncio`; existing in-repo `runtime.providers` (gateway, IR,
cost, profiles, validation helper), `runtime.snapshot` (`VersionSnapshot`), `runtime.hashing`. No new
third-party dependency (token heuristic is stdlib; `tiktoken`/exact tokenizers deferred — research D4).

**Storage**: None in this feature. The `RunRecord` is produced as a value and handed to a caller-supplied
sink; DB persistence under RLS is M4.

**Testing**: pytest + pytest-asyncio (`asyncio_mode=auto`), fully mocked gateway/transport (no network,
no API key). Run via `services/.venv/bin/python -m pytest`.

**Target Platform**: Linux/macOS server-side library (imported by the future API `web`/`worker`).

**Project Type**: Pure library within the `services/` monorepo tree (`services/runtime/`).

**Performance Goals**: deterministic unit latency (mocked); the real SLO (p99 ≤ 30s) is enforced by the
deadline bound and validated live at Phase 5, not here.

**Constraints**: **P6 purity** — `services/runtime/` imports no inbound web framework/HTTP server
(enforced by `tests/test_runtime_purity.py`). No live/paid provider calls anywhere in the feature.

**Scale/Scope**: one generate step per invocation; no agentic loop, retrieval, tool execution, memory, or
checkpointer in scope (all deferred — see spec Out of Scope).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Obligation for this feature | Verdict |
|---|---|---|
| **P6** (one pure runtime; immutable snapshots) | `runtime.invoke` is the single path; reads frozen `VersionSnapshot` only; `runtime/` stays free of web-framework imports; Run produced as a value (no DB in runtime). | ✅ PASS — purity test stays green; sink keeps persistence out (research D2/D3). |
| **P3** (deterministic, fail-closed controls; untrusted inputs) | Input validation vs `io_contract.input_schema` and context-budget guard both fail closed **before** any provider call; deadline bound is enforced in code, not model-dependent. | ✅ PASS. Note: `max_tool_calls` is carried but loop-enforcement is deferred with tool execution (research D6) — flagged for spec reconciliation. |
| **P7** (cost ours, never re-sourced) | The `RunRecord` cost is the gateway's independently-computed cost, carried through unchanged; the spine never recomputes or reads a provider/transport cost. | ✅ PASS. |
| **P8** (observable, honest degradation) | Exactly one `RunRecord` per invocation with an explicit outcome; degraded/failed runs recorded truthfully, never as success, never dropped; every run carries a correlation id. | ✅ PASS. |
| **P5** (tenancy) | The `RunRecord` carries `workspace_id` (from the snapshot/ctx) so the M4 sink stores it under RLS; the runtime itself does no cross-tenant access. | ✅ PASS (persistence + negative cross-tenant tests are M4). |
| **P9** (genuine behavioral RED first) | Each behavior-changing task names a genuine behavioral RED test before implementation. | ✅ PASS (process; enforced at `/speckit-tasks` + implement). |
| **P4** (eval evidence) | N/A — no judge/eval gate in this feature. | — |

**Result**: No MUST violation. **Complexity Tracking not required** (no gate justification needed).

One deliberate design divergence (not a constitution violation) is recorded for owner review: **plain
pipeline instead of `backend/03`'s LangGraph** (research D1).

## Project Structure

### Documentation (this feature)

```text
specs/003-runtime-invoke-spine/
├── plan.md              # this file
├── research.md          # decisions D1–D7 + direction check
├── data-model.md        # RunContext, RunRecord, InvokeResult, assembly map
├── quickstart.md        # how to validate M2a (mocked)
├── contracts/
│   └── runtime-invoke.md # the invoke() / invoke_result() / RunSink contract + outcome taxonomy
└── tasks.md             # (/speckit-tasks — not created here)
```

### Source Code (repository root)

```text
services/
├── runtime/
│   ├── invoke.py        # NEW — the spine: invoke() (async event iterator) + invoke_result() collector
│   ├── context.py       # NEW — RunContext + RunSink protocol + defaults (deadline_s=30, max_tool_calls=8)
│   ├── run.py           # NEW — RunRecord + InvokeResult + Outcome taxonomy (value objects)
│   ├── tokens.py        # NEW — count_tokens abstraction (conservative heuristic backend)
│   ├── io.py            # NEW — validate_input(inputs, io_contract) + build_normalized_request(snapshot, inputs)
│   ├── snapshot.py      # EXISTING (M0) — VersionSnapshot (read-only here)
│   ├── hashing.py       # EXISTING (M0)
│   └── providers/       # EXISTING (M1) — gateway, ir, cost, profiles, transport, validation(=M2b output/tool)
└── tests/
    ├── test_invoke.py            # NEW — pipeline: success, frozen-snapshot read, no caller branches (US1)
    ├── test_invoke_run_record.py # NEW — one honest Run per outcome (US2/P8)
    ├── test_invoke_deadline.py   # NEW — deadline bound + best-effort partial (US3/D5)
    ├── test_invoke_events.py     # NEW — full stream fidelity incl. tool-call-only (US4/C4)
    ├── test_invoke_input_guard.py# NEW — input + context-budget fail-closed before call (US5/D7)
    ├── test_tokens.py            # NEW — count_tokens is monotonic + over-estimate-safe (D4)
    └── test_runtime_purity.py    # EXISTING — must stay green (P6)
```

**Structure Decision**: Single library within the existing `services/runtime/` tree. New spine modules sit
beside `snapshot.py`/`hashing.py` at the runtime root; the M1 provider layer is consumed unchanged under
`runtime/providers/`. No new top-level package, no new dependency. A small additive timeout pass-through to
`runtime/providers/transport.py` (research D5) is the only touch to M1 code.

## Direction Check (plan arg: "verify and check overall direction of development")

See [research.md → Direction check](./research.md). Summary: the invariant holds (one pure `invoke`,
frozen-snapshot read); the M2a/M2b split is sound and lower-risk than feared (the M2b validators already
exist, tested); M1 integration points are real; downstream (M2b → M3 → M4 → Phase 5) consumes M2a without
rework. Three flagged items — D1 (pipeline vs LangGraph), D6 (`max_tool_calls` deferral + spec
reconciliation), D7 (validation.py location debt) — are recorded; none block M2a.

## Complexity Tracking

No constitution violations requiring justification — section intentionally empty.
