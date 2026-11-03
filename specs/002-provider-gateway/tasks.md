---
description: "Task list — M1 Provider Gateway"
---

# Tasks: Provider Gateway (M1)

**Input**: Design documents in `specs/002-provider-gateway/` (plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md)

**Tests**: TDD is REQUIRED (constitution P9). Every behavior-changing task has a RED test written and shown-failing (genuine behavioral failure, not import/syntax) BEFORE its implementation task. Owner confirmation of RED is required before the first GREEN.

**Working dir for all commands**: `services/` (run `cd services` first; `pythonpath=["."]` is configured). No DB/compose needed — this layer is infra-free.

**Conventions**: `[P]` = parallelizable (different files, no incomplete deps). `[USn]` maps to the spec user story. All provider tests mock the `runtime.providers.transport` seam — **no live/paid calls** anywhere in M1.

**REUSE (do NOT rebuild — already implemented & tested)**: `runtime/providers/ir.py`, `runtime/providers/profiles.py` (`CapabilityProfile`, `Substitution`, `VALID_ACTIONS`, `preflight_substitutions`), `runtime/providers/cost.py` (`compute_cost`, `quantize_usd`), `runtime/providers/validation.py`.

---

## Phase 1: Setup (Shared Infrastructure)

- [X] T001 Add `litellm` to the `[api]` optional-dependencies in `services/pyproject.toml` (transport-only dep per research R1); reinstall with `pip install -e '.[api,test]'` and record the resolved version.
- [X] T002 [P] Create `services/runtime/providers/profiles_data/` and port the four existing profiles there as JSON matched-by-content: `openai__gpt-5.json`, `openai__gpt-5-mini.json`, `ollama__llama3.1-8b.json`, `ollama__llama3.2-3b.json` (copy from `mvp/profiles/*.json`; keep the `pricing_usd_per_mtok` shape; validate each against `contracts/profile.schema.json`).

**Checkpoint**: deps installed; profile data present. No behavior yet.

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ Blocks every adapter + the gateway.**

### Profile loader (serves all stories)

- [X] T003 RED: write `services/tests/test_profile_loader.py` asserting `profiles.resolve_profile("openai","gpt-5-mini")` returns a `CapabilityProfile` with `pricing["input"]==0.6` and `features["structured_output"] is True`, AND that an unknown `(provider,model)` raises a clear fail-closed error (FR-008). Run `pytest -q tests/test_profile_loader.py` → MUST fail on behavior (currently `NotImplementedError`). **Show failure to owner.**
- [X] T004 GREEN: implement `resolve_profile(provider, model, api_version=None)` in `services/runtime/providers/profiles.py` — load+content-match JSON from `profiles_data/`, map `pricing_usd_per_mtok`→`.pricing`, carry `availability`/`sunset_at`/`provisional`; raise a clear error when no match (per research R7). Run `pytest -q tests/test_profile_loader.py` → green; regression `pytest -q tests/test_substitutions.py tests/test_cost.py`.

### Transport seam + adapter contract

- [X] T005 [P] Implement `services/runtime/providers/transport.py` — a thin async wrapper over `litellm.acompletion(..., stream=True, drop_params=False)` exposing an isolated `async def stream_chunks(**kwargs) -> AsyncIterator[chunk]` that tests monkeypatch; it must NOT read any litellm cost field (research R2/R3). (No behavioral test of its own — it is the mock seam; exercised via adapter tests. Keep it dependency-light so `test_runtime_purity.py` stays green.)
- [X] T006 Implement `services/runtime/providers/base.py` — `ProviderAdapter` Protocol (`provider`, `supports`, `stream`) + registry (`register_adapter`, `adapter_for` with lazy import of the three adapter modules), per `contracts/provider-adapter.md`. Fix the mvp import drift: `Substitution` comes from `runtime.providers.profiles`, not `ir`.
- [X] T007 Regression: run `pytest -q tests/test_runtime_purity.py` → confirm `runtime/` still imports no inbound web framework after adding litellm/transport.

**Checkpoint**: profiles resolve; transport seam + adapter contract exist. Adapters can be built.

---

## Phase 3: User Story 1 — Normalized execution across providers (Priority: P1) 🎯 MVP

**Goal**: One normalized streaming shape (TEXT_DELTA… then one USAGE) across OpenAI + Ollama, through a single gateway, no caller-side provider branching.

**Independent Test**: issue a normalized request for openai and for ollama with mocked transport; both stream content deltas then exactly one USAGE event, in the shared vocabulary.

- [X] T008 [P] [US1] RED: `services/tests/test_providers.py` — openai path: monkeypatch `transport.stream_chunks` with canned LiteLLM-shaped chunks; assert `OpenAIAdapter().stream(...)` yields `EventType.TEXT_DELTA` content then one `EventType.USAGE` with raw `input_tokens/output_tokens/cached_input_tokens/reasoning_tokens` mapped per `contracts/usage-mapping.md`. Run `pytest -q tests/test_providers.py -k openai_usage` → MUST fail (no adapter). **Show owner.**
- [X] T009 [US1] GREEN: implement `services/runtime/providers/openai_adapter.py` on the transport seam — build request (honoring `max_tokens`→`max_completion_tokens`, emulation for downgraded features), map LiteLLM stream→`TEXT_DELTA`/`TOOL_CALL_*`, map usage→`NormalizedUsage` (raw; no cost here), `register_adapter`. `supports()` delegates to `preflight_substitutions`. Run `pytest -q tests/test_providers.py -k openai` → target green.
- [X] T010 [P] [US1] RED: add ollama path to `test_providers.py` — assert `OllamaAdapter().stream(...)` maps `prompt_eval_count`/`eval_count`→usage, cache/reasoning = 0. Run `pytest -q tests/test_providers.py -k ollama_usage` → MUST fail. **Show owner.**
- [X] T011 [US1] GREEN: implement `services/runtime/providers/ollama_adapter.py` on the transport seam (per research R4/R5). Run `pytest -q tests/test_providers.py -k ollama` → target green.
- [X] T012 [US1] RED: `services/tests/test_gateway.py` — assert `gateway.stream(req, api_key=...)` resolves the profile, picks the adapter, and passes through events ending in one USAGE, for both providers. Run `pytest -q tests/test_gateway.py -k passthrough` → MUST fail. **Show owner.**
- [X] T013 [US1] GREEN: implement `services/runtime/providers/gateway.py` orchestration (resolve-or-use-profile → `adapter_for` → stream passthrough → terminal USAGE), per `contracts/provider-adapter.md` (cost attachment lands in US2). Run `pytest -q tests/test_gateway.py -k passthrough`; regression `pytest -q tests/test_providers.py tests/test_runtime_purity.py`.

**Checkpoint**: a request runs end-to-end through the gateway on two providers with one normalized shape.

---

## Phase 4: User Story 2 — Honest, independently-computed cost (Priority: P1)

**Goal**: Every call's cost is computed from our profiles (never LiteLLM), reconciling to hand-calc.

**Independent Test**: for canned usage + a known profile, gateway's USAGE event carries `cost_usd` equal to the hand-calc literal; a LiteLLM `response_cost` in the mock is never reflected.

- [X] T014 [P] [US2] RED: in `test_providers.py`, `test_openai_cost_matches_hand_calc` — reuse the mvp literal (`compute_cost(gpt-5-mini profile, usage) == 0.001518`), driven through the adapter+gateway USAGE `cost_usd`. Run `pytest -q tests/test_providers.py -k cost_matches` → MUST fail (gateway not attaching cost). **Show owner.**
- [X] T015 [US2] GREEN: in `gateway.py`, attach cost on the terminal USAGE — `usage.cost_usd = quantize_usd(compute_cost(profile, usage))`; if `profile.provisional` mark the cost an estimate (FR-010). Run `pytest -q tests/test_providers.py -k cost_matches`.
- [X] T016 [P] [US2] RED+GREEN: `test_gateway.py::test_litellm_cost_field_ignored` — mock transport returns a bogus `response_cost`; assert gateway's `cost_usd` derives only from the profile (FR-003/P7). Run `pytest -q tests/test_gateway.py -k cost_ignored`.
- [X] T017 [P] [US2] `test_ollama_is_free` — ollama profile → `cost_usd == 0.0` through the gateway. Run `pytest -q tests/test_providers.py -k is_free`.

**Checkpoint**: cost is ours, reconciled, and LiteLLM's number is provably ignored.

---

## Phase 5: User Story 3 — Explicit capability substitutions (Priority: P2)

**Goal**: Every downgrade surfaces as a `Substitution`; none silent; no false positives.

**Independent Test**: a weak-profile request yields the expected substitutions on the USAGE event; a fully-supported request yields none.

- [X] T018 [P] [US3] RED: substitution cases — weak profile → `structured_output`/`forced_tool_choice`=emulated_via_prompt, `reasoning_control`/`prompt_caching`=dropped (via `supports()`), surfaced on the USAGE event by the gateway (`test_gateway.py::test_gateway_merges_preflight_substitutions_onto_usage`); plain request → `[]`. Genuine behavioral RED shown (3 failed: native-feature forward, emulation injection, gateway merge).
- [X] T019 [US3] GREEN: `supports()`→`preflight_substitutions` already wired in both adapters; gateway now calls `adapter.supports()` and merges preflight + runtime-discovered subs onto the USAGE event (dedup by `(feature,action)` via `_merge_substitutions`).
- [X] T020 [US3] RED+GREEN: new `_common.build_wire_request(profile, req)` — native features forwarded only when supported (closes code-review F2), omitted + emulated-via-prompt otherwise. Tests `test_build_request_forwards_native_features_when_supported` + `test_build_request_omits_and_emulates_downgraded_features`: contract obligation #6 honored (nothing unsupported sent raw; emulation makes the `emulated_via_prompt` claim true).

**Checkpoint**: substitutions honest in both directions (present when needed, absent when not).

---

## Phase 6: User Story 4 — Add a third provider without caller changes (Priority: P2)

**Goal**: Net-new Anthropic adapter + profiles slot in behind the same contract; proves provider-agnosticism and seeds the judge model.

**Independent Test**: the parametrized adapter-contract suite passes for `anthropic` with no caller change; Anthropic usage mapping + cost reconcile.

- [X] T021 [P] [US4] Seeded 4 Anthropic profiles in `profiles_data/` (`claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-4-5` [deprecated per data.ts], judge `claude-haiku-5-5`). input/output pricing sourced from `frontend/src/lib/data.ts`; judge `claude-haiku-5-5` (not in data.ts) sourced + cited from the `claude-api` skill ($0.10/$0.50); cache/reasoning rates omitted (compute_cost conservative fallback); all `provisional: true`; features (notably `forced_tool_choice: false` on 5.x Claude) from the claude-api skill. All 4 validate against `contracts/profile.schema.json`. Judge-naming reconciliation deferred to M3.
- [X] T022 [P] [US4] RED: `services/tests/test_anthropic_adapter.py` — gateway-driven (genuine BEHAVIORAL RED: `adapter_for('anthropic')` KeyError, not an import error). Asserts `cache_read_input_tokens`→`cached_input_tokens`, `cache_creation_input_tokens`→`cache_write_tokens`, `input_tokens` made INCLUSIVE (add-back), and a cost hand-calc literal (0.0152); litellm cost ignored; provisional→estimate. Mapping pinned with a canned native-shape chunk (test authoritative over the LiteLLM-shape assumption).
- [X] T023 [US4] GREEN: `_common.map_anthropic_usage` (cache add-back → inclusive) + `services/runtime/providers/anthropic_adapter.py` on the transport seam (`anthropic/<model>` routing, `register_adapter`, `supports()`→`preflight_substitutions`, shares `build_wire_request`/stream loop). `pytest -q tests/test_anthropic_adapter.py` → 2 passed.
- [X] T024 [US4] Added a parametrized adapter-contract conformance test (`test_adapter_contract_conformance`) across all 3 providers: event vocabulary (TEXT_DELTA then one USAGE), raw usage (no adapter-invented cost), and no api_key leak. `pytest -q tests/test_providers.py -k contract` → 3 passed.

**Checkpoint**: three providers conform to one contract; FR-002/US4 proven.

---

## Phase 7: Polish & Cross-Cutting

- [ ] T025 [P] RED+GREEN: `test_gateway.py::test_unknown_model_fails_closed` and `::test_retired_model_not_executed` — gateway surfaces a caller-visible error, never a guessed model/pricing (FR-008, edge cases). Run `pytest -q tests/test_gateway.py -k "fails_closed or retired"`.
- [ ] T026 [P] RED+GREEN: `test_gateway.py::test_transport_error_normalized` — mock transport raises a LiteLLM exception (Timeout/APIError/BadRequestError); assert a normalized `EventType.ERROR` (or normalized exception) with NO raw litellm type leaking (FR-009/P8); and `::test_interrupted_stream_is_degraded` (no USAGE → degraded, not silent success). Run `pytest -q tests/test_gateway.py -k "error_normalized or degraded"`.
- [ ] T027 [P] RED+GREEN: `test_gateway.py::test_api_key_not_leaked` — drive a full stream with `api_key="SECRET123"`; assert the string appears in 0 emitted event payloads / usage / substitutions / errors (FR-012/P5/SC-006). Run `pytest -q tests/test_gateway.py -k not_leaked`.
- [ ] T028 Full regression: `pytest -q -m "not integration"` (the whole services unit batch incl. the 143 pre-existing) → all green; confirm `test_runtime_purity.py` green.
- [ ] T029 [P] Run the live-DB integration batch unaffected: `pytest -q -m integration` (compose up) → still green (no regression from the dep/layout change).
- [ ] T030 Docs reconciliation (per the `update-docs-after-feature` memory): update `docs/implementation-plan.md` M1 status row + `docs/backend/02-provider-gateway.md`/`02L` deltas to the built state (LiteLLM transport decision, resolve_profile implemented, 3 adapters, profiles_data location); note judge-naming still deferred to M3.
- [ ] T031 Run `specs/002-provider-gateway/quickstart.md` validation commands end-to-end; confirm SC-001…SC-007 each demonstrably met; write a short completion report naming commands run + evidence + any unverified items.

---

## Dependencies & Execution Order

- **Setup (P1)** → **Foundational (P2)** blocks everything.
- **US1 (P3)** requires P2. **US2 (P4)** extends the US1 gateway (needs T013). **US3 (P5)** needs the adapters (T009/T011) + gateway (T013). **US4 (P6)** needs the contract (T006) + loader (T004); independent of US2/US3 logic but reuses the same seam. **Polish (P7)** last.
- MVP = Setup + Foundational + **US1** (normalized execution on 2 providers). US2 (honest cost) is the natural immediate follow — both are P1.

### Parallel opportunities
- T002 ∥ (after T001).
- Within US1: T008 ∥ T010 (different test foci), then their GREENs are sequential per file.
- US4 T021 ∥ T022 (profiles vs test authoring).
- Polish T025 ∥ T026 ∥ T027 (distinct gateway test cases), then T028 serializes.

## Implementation Strategy
1. Setup + Foundational.
2. **US1 → STOP/VALIDATE** (MVP: normalized execution).
3. US2 (cost) → validate. 4. US3 (substitutions) → validate. 5. US4 (Anthropic) → validate.
6. Polish (fail-closed, error/secret, regressions, docs).
Each behavior task: RED (show owner) → GREEN (smallest change) → keep prior tasks green. Commit per logical group. **PR only when asked.**
