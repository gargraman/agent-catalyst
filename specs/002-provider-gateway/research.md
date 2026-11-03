# Research — Provider Gateway (M1)

Phase 0 decisions. Each: Decision / Rationale / Alternatives considered.

## R1 — Transport library: LiteLLM, wire-layer only (OWNER-LOCKED)

**Decision**: Adapters send/stream via `litellm.acompletion(..., stream=True)`. LiteLLM is the **transport only** — it carries bytes to/from providers and gives a unified (OpenAI-shaped) streamed response. We still: compute cost from our profiles, normalize usage into `NormalizedUsage` ourselves, and record `Substitution`s ourselves.

**Rationale**: Owner decision 2026-11-03; aligns `spec.md` B1 ("LiteLLM for transport only"). One wire surface for many providers instead of maintaining N hand-rolled formats. The mvp's hand-rolled `urllib` adapters were an MVP stopgap ("LiteLLM = future slot"); this realizes the locked baseline.

**Alternatives considered**: (a) Keep hand-rolled `urllib` adapters — rejected by owner; we'd own 3 wire formats. (b) Hybrid seam, defer — rejected; decision is made, no need to carry two futures.

**Consequence / obligation**: cost stays ours (R3); substitutions stay ours (R2); per-provider usage mapping must be re-proven against LiteLLM's shapes (R4).

## R2 — Capability downgrades stay explicit: `drop_params=False`

**Decision**: Call LiteLLM with `drop_params=False` (and do **not** rely on `litellm.drop_params=True` global). We decide, in our adapter/`preflight_substitutions`, which requested capabilities the target model lacks, apply the downgrade/emulation ourselves, and record a `Substitution` for each.

**Rationale**: LiteLLM's convenience behavior is to silently drop params a provider doesn't accept. That is exactly the "silent capability drift" P7/FR-006 forbids. Turning it off makes us the single, honest authority on downgrades — the wire body then matches the `Substitution` list (no recorded downgrade is a lie, and no silent drop goes unrecorded).

**Alternatives considered**: Let LiteLLM drop params and infer substitutions afterward — rejected: non-deterministic, provider-version-dependent, and inverts the trust model.

## R3 — Cost is ours; ignore LiteLLM's cost fields

**Decision**: Never read `response._hidden_params["response_cost"]` or `litellm.completion_cost(...)`. Parse only **raw token counts** from the response usage, build `NormalizedUsage`, and attach `cost.compute_cost(profile, usage)` (quantized once via `cost.quantize_usd`).

**Rationale**: P7/FR-003 — cost from our own reviewed profiles, never the transport. LiteLLM's cost tables are a moving third-party artifact; our profiles are the versioned source of truth and the thing the snapshot pins.

**Alternatives considered**: Trust LiteLLM cost as a cross-check — deferred; a reconciliation harness vs provider invoices is an M6/observability concern, not M1.

## R4 — Per-provider adapters retained; usage-field mapping is the reason

**Decision**: Keep three adapter classes (`openai`, `ollama`, `anthropic`) implementing the `ProviderAdapter` contract, sharing a common `transport.py` LiteLLM helper. Each owns: request-building (emulation of downgraded features), **usage-field mapping**, and tool-call mapping.

**Rationale**: Even though LiteLLM unifies the *response envelope*, the **usage breakdown differs per provider and drives cost**:
- OpenAI: `prompt_tokens`, `completion_tokens`, `prompt_tokens_details.cached_tokens`, `completion_tokens_details.reasoning_tokens`.
- Anthropic: `input_tokens`, `output_tokens`, `cache_creation_input_tokens` (→ our `cache_write_tokens`), `cache_read_input_tokens` (→ our `cached_input_tokens`). **Anthropic reports input_tokens EXCLUSIVE of cache tokens** (unlike OpenAI's inclusive convention) — a real mapping divergence that must be handled so cost isn't under/over-counted.
- Ollama: `prompt_eval_count` / `eval_count`; no cache/reasoning breakdown → honest 0s.

A single generic adapter would bury these and make FR-002's "3 implementations" cosmetic while risking silent cost errors. Each adapter's usage mapping gets a dedicated hand-calc cost test.

**Alternatives considered**: One parameterized LiteLLM adapter — rejected for the cost-correctness reason above.

**Open obligation**: confirm how LiteLLM surfaces each provider's cache/reasoning counts in streaming mode (it may or may not pass them through `stream_options={"include_usage": True}` equivalently); the adapter maps from LiteLLM's streamed usage object, and tests pin the mapping with canned LiteLLM-shaped chunks.

## R5 — Event vocabulary: adopt the services IR (`TEXT_DELTA`, `TOOL_CALL_*`), not mvp's flattened `TEXT`

**Decision**: Adapters emit `EventType.TEXT_DELTA` for content and proper `TOOL_CALL_START/DELTA/END` events for tool calls, per the services `ir.py`. The mvp adapters' flattening of tool calls into a `TEXT` string (`[tool_call] name(args)`) is **not** carried over.

**Rationale**: The services `ir.py` is the newer, richer design (`02 §2`); `test_ir.py` already pins `TEXT_DELTA`. M2's runtime + F3 tool-call validation (`validate_tool_call`) need structured tool-call events, not a stringified blob.

**Alternatives considered**: Keep mvp's `TEXT`-flattening — rejected; it would regress the IR and block M2 tool validation.

## R6 — `Substitution` source + runtime-discovered downgrades

**Decision**: Reuse `Substitution` + `preflight_substitutions` from `profiles.py` (already built/tested). Each adapter's `supports()` delegates to `preflight_substitutions`. Downgrades discovered only at call time (e.g. a provider rejects a param mid-stream) are appended to the substitutions carried on the final `USAGE` event. (Note: the mvp `base.py` imported `Substitution` from `ir`; in services it lives in `profiles.py` — the port fixes the import. No `Substitution` is added to `ir.py`.)

**Rationale**: DRY — the rule table is implemented once. Preflight (static, from profile) vs runtime-discovered (dynamic) are both surfaced; neither is silent.

**Alternatives considered**: Re-implement `supports()` per adapter (mvp style) — rejected; duplicates the rule table and drifts.

## R7 — Profile loader (`resolve_profile`) + profile data location

**Decision**: Implement `profiles.resolve_profile(provider, model, api_version=None)` (currently `NotImplementedError`) by porting the mvp loader's **content-match** strategy (match on JSON `provider`+`model`, not filename — so model ids with `/` or `:` need no filesystem-safe name). Profile JSON lives in `services/runtime/providers/profiles_data/`. JSON stores pricing under `pricing_usd_per_mtok`; the loader maps it onto `CapabilityProfile.pricing` (keys: input/output/cached_input/cache_write/reasoning/batch_multiplier). Fail closed with a clear error when no profile matches (FR-008).

**Rationale**: Reuses a proven, tested loader; keeps profiles as versioned data (P6/P7). Content-match avoids filename-escaping bugs for Anthropic/Ollama model ids.

**Alternatives considered**: DB-backed profiles — deferred (no DB in this layer; profiles are config data, snapshot-pinned later).

## R8 — Anthropic profiles: honest sourcing, no invented pricing

**Decision**: Seed Anthropic capability+pricing profiles for the Claude models the frontend references (`frontend/src/lib/data.ts`) plus the eval judge model `claude-haiku-5-5`. **Pricing is sourced from `data.ts` (and/or the `claude-api` skill's pricing reference), never invented**, and every provisional/unreconciled profile is marked so cost from it is labeled an estimate (FR-010, P7). Exact model IDs + pricing are read at implementation time (consult the `claude-api` skill then, since Anthropic model IDs/LiteLLM routing are named there).

**Rationale**: P7 forbids invented cost. Mirrors how the existing openai profiles were seeded ("Pricing seeded from frontend/src/lib/data.ts; … placeholders to confirm before Phase-2 cost reconciliation").

**Open item (deferred to M3)**: judge-model **naming** reconciliation (docs reference an older Claude judge version; the experiment approved `claude-haiku-5-5`). Not resolved here — M3 (eval engine) owns it; the profile seeds `claude-haiku-5-5` and the naming note is carried forward.

## R9 — No live/paid calls in M1; failure normalization

**Decision**: All adapter/gateway tests mock the `transport.py` LiteLLM seam with canned streamed chunks (no network, no API key use). LiteLLM exceptions (`litellm.Timeout`, `APIError`, `BadRequestError`, etc.) are caught at the adapter/gateway boundary and normalized — either an `EventType.ERROR` stream event or a normalized gateway exception — so **no LiteLLM/provider SDK exception type leaks to callers** (FR-009). An interrupted stream that never yields a USAGE event is reported as degraded (explicit error/outcome), not a successful 0-cost run (P8).

**Rationale**: Constitution — paid provider calls are never ordinary tests. The first real call happens in M2/skeleton under the paid-call protocol. FR-009/P8 require normalized, honest failure.

**Alternatives considered**: A single opt-in live smoke test — deferred to M2 where the paid-call protocol (approved model, synthetic input, spend ceiling, preflight, retained evidence) is set up.

## R10 — Test port strategy: port intent, rewrite the mock

**Decision**: `mvp/tests/test_providers.py` is the **behavioral reference**, not a verbatim port. Carry over the assertions (usage parsing correctness, substitution recording, `compute_cost` == independent hand-calc literal, error propagation/normalization, "no downgrade when request is plain"). Rewrite the transport mocking from `_open_sse`/`_open_ndjson`/`urllib.error.HTTPError` to monkeypatching `transport.py` with LiteLLM-shaped chunk iterables and raising LiteLLM exceptions.

**Rationale**: The mechanism changed (urllib → LiteLLM) but the *behavioral contract* the mvp tests encode is exactly what the spec's SCs require. Keeping the hand-calc cost literals preserves the independent-reconciliation guarantee (SC-002).

**Alternatives considered**: Verbatim port — impossible; the mvp tests assert urllib internals that no longer exist.
