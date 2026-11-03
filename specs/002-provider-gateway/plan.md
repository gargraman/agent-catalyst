# Implementation Plan: Provider Gateway (M1)

**Branch**: `feat/backend-m0-foundation` (continuation) | **Date**: 2026-11-03 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/002-provider-gateway/spec.md`

## Summary

Deliver the provider-abstraction layer in `services/runtime/providers/` so the runtime can execute an LLM request against any supported provider behind one contract and return one normalized shape, with **cost computed from our own versioned profiles** (never the provider/transport) and **every capability downgrade surfaced as an explicit `Substitution`**. Transport is **LiteLLM, wire-layer only** (owner decision, 2026-11-03; aligns `spec.md` B1). The already-seeded primitives (`ir`, `profiles` incl. `Substitution`/`preflight_substitutions`, `cost`, `validation`) are reused as-is; this feature adds the adapter contract + registry (`base.py`), three conforming adapters (`openai`, `ollama` reimplemented on LiteLLM; net-new `anthropic`), the profile JSON loader (`resolve_profile`, currently `NotImplementedError`) + profile data, and the `gateway.py` orchestrator — all TDD, all offline (mocked transport; no live/paid calls in M1).

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: `litellm` (NEW — transport only), existing `pydantic`/`jsonschema`; stdlib `asyncio`, `decimal`, `pathlib`. No inbound web framework in `runtime/`.

**Storage**: N/A for this layer. Capability/pricing profiles are versioned JSON **data** on disk (`services/runtime/providers/profiles_data/`). No DB access in the provider gateway.

**Testing**: pytest + pytest-asyncio (already configured, `asyncio_mode=auto`). All provider tests mock the LiteLLM transport seam — zero network.

**Target Platform**: Linux server (runs inside the API process and the eval worker); the library itself is process-agnostic.

**Project Type**: Pure library (the `services/runtime/` package). Consumed later by `runtime.invoke` (M2), the eval worker (M3), the FastAPI app (Phase 4), and channels.

**Performance Goals**: Streaming must surface first content incrementally (not buffer-then-emit). Per-call transport timeout bounded (default 60s, configurable). No throughput target at M1 (no concurrency layer yet).

**Constraints**: `runtime/` imports **no** FastAPI/Starlette/inbound-web framework (enforced by `services/tests/test_runtime_purity.py`). Cost precision = `numeric(12,6)` via `cost.quantize_usd` (already pinned). No provider credential in any emitted event/usage/substitution/error/log.

**Scale/Scope**: 3 providers, ~6–8 capability profiles (openai gpt-5 + gpt-5-mini, ollama 2 models, anthropic: frontend-seeded Claude models + judge `claude-haiku-5-5`). ~1 new runtime module set + ~1 test module set.

## Constitution Check

*GATE: evaluated against constitution v1.0.0. Re-checked post-design (Phase 1) at the bottom.*

| Principle | Gate | How this plan satisfies it |
|---|---|---|
| **P6 — single pure runtime** | `runtime/` stays a pure library; one execution contract | Provider gateway lives under `services/runtime/`; no inbound web imports; `test_runtime_purity.py` is a required check. LiteLLM is outbound transport, permitted. |
| **P7 — provider honesty & cost** | cost from our profiles; downgrades explicit; estimates labeled | `cost.compute_cost` (ours) is the only cost source; LiteLLM `drop_params=False` + our `preflight_substitutions` keep every downgrade an explicit `Substitution`; LiteLLM's `response_cost` is **ignored**; provisional profiles flagged estimate. |
| **P5 — tenancy/secrets** | server-side authz + no secret leakage | This layer is tenant-agnostic (no DB/RLS here — justified omission below), but MUST keep the api_key out of all emitted artifacts and logs — a dedicated test asserts this. |
| **P3 — deterministic controls** | enforce in code, fail-closed | Unknown model → `resolve_profile` fails closed (caller-visible error, no guessed model/pricing). Capability gating is code (`preflight_substitutions`), not model instruction. |
| **P8 — observable, honest failure** | normalize failure; degradation explicit | LiteLLM timeouts/errors → normalized `ERROR` stream event / normalized exception; an interrupted stream without a USAGE event is reported as degraded, not a silent 0-cost success. |
| **P9 — spec/test-first** | Red→Green→Refactor, genuine behavioral RED, owner-confirmed | Every module gets a failing behavioral test first (not import failure); RED evidence shown to owner before GREEN. |
| **Governance** | M1 production expansion authorized | Authorized by the 2026-11-03 advisory-gate decision in `docs/scope.md`. |

**Minimum Verification Checks applied** (Provider/runtime row): adapter-contract conformance, streaming, timeout/failure, usage normalization, substitution, independently-reconciled cost — all present in this plan. Security/secrets row: secret-leakage test. Justified omissions: DB/tenant row (no DB in this layer); Model/prompt-eval row (that's M3). Paid-call protocol row: N/A — no live calls in M1.

**Initial gate: PASS** (no unjustified violations).

## Project Structure

### Documentation (this feature)

```text
specs/002-provider-gateway/
├── plan.md              # this file
├── research.md          # Phase 0 — transport & port decisions
├── data-model.md        # Phase 1 — entities (reused + new)
├── quickstart.md        # Phase 1 — run/verify guide
├── contracts/           # Phase 1 — adapter protocol, gateway iface, profile schema, usage-mapping
└── tasks.md             # /speckit-tasks output (NOT created here)
```

### Source Code (repository root)

```text
services/
├── runtime/
│   └── providers/
│       ├── ir.py                 # REUSE — NormalizedRequest/Event/Usage, EventType (TEXT_DELTA, TOOL_CALL_*, USAGE, ERROR, STOP)
│       ├── profiles.py           # REUSE + EXTEND — CapabilityProfile, Substitution, preflight_substitutions; IMPLEMENT resolve_profile()
│       ├── cost.py               # REUSE — compute_cost / quantize_usd (the single cost authority)
│       ├── validation.py         # REUSE — validate_output / validate_tool_call (used by M2, not re-touched here)
│       ├── profiles_data/        # NEW — versioned capability+pricing JSON (openai, ollama, anthropic)
│       │   ├── openai__gpt-5.json
│       │   ├── openai__gpt-5-mini.json
│       │   ├── ollama__llama3.1-8b.json
│       │   ├── ollama__llama3.2-3b.json
│       │   └── anthropic__*.json  (Claude models + claude-haiku-5-5 judge)
│       ├── transport.py          # NEW — thin LiteLLM wrapper (acompletion stream), isolated for test mock
│       ├── base.py               # NEW — ProviderAdapter Protocol + registry (register_adapter/adapter_for)
│       ├── openai_adapter.py     # NEW (reimpl on LiteLLM; mvp urllib version is the behavioral reference)
│       ├── ollama_adapter.py     # NEW (reimpl on LiteLLM)
│       ├── anthropic_adapter.py  # NEW (net-new)
│       └── gateway.py            # NEW — orchestrator (resolve → pick adapter → preflight subs → stream → attach our cost → collect subs)
└── tests/
    ├── test_runtime_purity.py    # REUSE — asserts runtime/ imports no inbound web framework
    ├── test_profile_loader.py    # NEW — resolve_profile content-match + fail-closed
    ├── test_providers.py         # NEW — adapter contract/usage/substitution/cost/error (LiteLLM-mocked; ports mvp test intents)
    ├── test_anthropic_adapter.py # NEW — anthropic usage-field mapping (cache_creation/cache_read) + cost hand-calc
    └── test_gateway.py           # NEW — end-to-end gateway orchestration, substitution collection, unknown-model fail-closed, secret non-leakage
```

**Structure Decision**: Extend the existing `services/runtime/providers/` package in place (it already holds the seeded primitives). Keep adapters per-provider (not collapsed into one LiteLLM adapter) because **usage-field mapping differs per provider and drives cost** (see research.md); a shared `transport.py` carries the common LiteLLM wire I/O so the adapters stay thin. This satisfies FR-002's "one contract, ≥3 implementations" honestly rather than cosmetically.

## Complexity Tracking

| Decision | Why needed | Simpler alternative rejected because |
|---|---|---|
| Add `litellm` dependency | Owner-locked transport choice; aligns `spec.md` B1; one wire surface for many providers | Hand-rolled `urllib` adapters (mvp) rejected by owner — we'd maintain N wire formats; LiteLLM centralizes transport while cost/usage/substitution stay ours. |
| Keep 3 separate adapters despite LiteLLM unifying the response | Provider usage fields differ (Anthropic cache_creation/cache_read vs OpenAI cached_tokens vs Ollama none) and feed cost; per-provider mapping must be explicit and tested | One generic adapter rejected: it would bury provider-specific usage mapping and make FR-002's "3 implementations" cosmetic, risking silent cost errors. |
| `drop_params=False` on LiteLLM | So unsupported params are NOT silently dropped by the transport — we drop them ourselves and record a `Substitution` (P7/FR-006) | Default `drop_params=True` rejected: it hides capability downgrades, directly violating the substitution-honesty requirement. |

## Post-Design Constitution Re-Check (after Phase 1)

Re-evaluated after research + contracts: **PASS, no new violations.**
- **P6**: design keeps everything under `runtime/providers/`; `transport.py` wraps LiteLLM (outbound) only; purity test unchanged and still required. ✅
- **P7**: contracts explicitly forbid reading LiteLLM `response_cost`, mandate `drop_params=False`, and route all cost through `cost.compute_cost`; provisional profiles → estimate. ✅
- **P3/P8**: gateway fails closed on unknown/retired model; transport errors normalized to `ERROR` (no SDK leak); missing-usage = degraded, not silent success. ✅
- **P5**: no DB/tenancy in this layer (justified); secret-non-leakage is an explicit contract obligation + test. ✅
- **P9**: `tasks.md` (next step) will order each module as Red→Green→Refactor with a named focused command; behavioral RED (not import failure) shown to owner before GREEN. ✅

**Design surfaced no requirement to amend the spec.** The one deferred item (judge-model naming) was already a non-blocking assumption owned by M3. Ready for `/speckit-tasks`.
