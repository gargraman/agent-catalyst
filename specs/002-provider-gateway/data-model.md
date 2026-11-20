# Data Model — Provider Gateway (M1)

This feature introduces **no database tables**. Its "data model" is (a) the in-memory contracts the layer passes around (mostly REUSED from the seeded `ir.py`/`profiles.py`) and (b) the on-disk capability/pricing **profile JSON** schema. Persisted run/usage/cost rows are M2's concern.

## Reused contracts (already on disk — do not redefine)

### NormalizedRequest (`runtime/providers/ir.py`) — REUSE
Provider-neutral request. Fields: `provider`, `model`, `api_version`, `messages[]`, `system`, `params{}`, `tools[]`, `tool_choice`, `response_format`, `reasoning`, `cache_hints[]`, `stream`. Frozen.

### NormalizedStreamEvent + EventType (`ir.py`) — REUSE
`EventType ∈ {TEXT_DELTA, REASONING_DELTA, TOOL_CALL_START, TOOL_CALL_DELTA, TOOL_CALL_END, USAGE, STOP, ERROR}`. Each event = `(type, data)`.
Payload-by-type (pinned this feature):
- `TEXT_DELTA` → `{"text": str}`
- `TOOL_CALL_START` → `{"id": str, "name": str}`; `TOOL_CALL_DELTA` → `{"id": str, "args_delta": str}`; `TOOL_CALL_END` → `{"id": str}`
- `USAGE` → `{"usage": NormalizedUsage, "substitutions": list[Substitution]}` (final event)
- `ERROR` → `{"error": str, "kind": str}` (normalized; no provider SDK type)

### NormalizedUsage (`ir.py`) — REUSE
`input_tokens`, `output_tokens`, `cached_input_tokens`, `cache_write_tokens`, `reasoning_tokens`, `cost_usd`. Conventions pinned in `cost.py` (input inclusive of cached+cache_write; output inclusive of reasoning). **`cost_usd` is filled by us, never the provider.**

### CapabilityProfile + Substitution + preflight_substitutions (`profiles.py`) — REUSE
`CapabilityProfile(provider, model, api_version, pricing{}, limits{}, features{}, availability, sunset_at)`. `Substitution(feature, action, detail)` with `action ∈ VALID_ACTIONS = {dropped, emulated_via_prompt, downgraded}`. `preflight_substitutions(profile, req) -> list[Substitution]`.

## New / changed this feature

### Capability+Pricing Profile JSON (on disk) — NEW DATA
Location: `services/runtime/providers/profiles_data/*.json`. One file per (provider, model). Matched by **content** (`provider`+`model`), not filename. Schema in `contracts/profile.schema.json`. Shape (mirrors the existing mvp/seeded openai profiles):
```json
{
  "provider": "anthropic",
  "model": "<model-id>",
  "api_version": "<str|null>",
  "availability": "active | deprecated | retired",
  "sunset_at": "<ISO date|null>",
  "pricing_usd_per_mtok": { "input": 0.0, "output": 0.0, "cached_input": 0.0, "cache_write": 0.0, "reasoning": 0.0, "batch_multiplier": 0.5 },
  "limits": { "context_tokens": 0, "max_output_tokens": 0 },
  "features": { "structured_output": true, "forced_tool_choice": true, "reasoning_control": false, "prompt_caching": true, "tool_call_format": "anthropic" }
}
```
Validation rules: `provider`/`model` required non-empty; `pricing_usd_per_mtok.input` & `.output` required ≥ 0; `availability ∈ {active,deprecated,retired}`; unknown feature keys allowed (forward-compat) but `feature()` returns falsy for absent. Provisional pricing (unreconciled) carries a `"_note"`/`"provisional": true` marker → cost labeled estimate (FR-010).

### ProviderAdapter (contract) — NEW
Protocol in `contracts/provider-adapter.md` / implemented in `base.py`. Methods: `provider: str`, `supports(profile, req) -> list[Substitution]`, `stream(req, *, api_key, profile) -> AsyncIterator[NormalizedStreamEvent]`. Registry: `register_adapter(a)`, `adapter_for(provider) -> ProviderAdapter`.

### Gateway result (orchestration) — NEW
`gateway.stream(req, *, api_key, profile=None) -> AsyncIterator[NormalizedStreamEvent]`:
1. `profile = profile or resolve_profile(req.provider, req.model, req.api_version)` → fail closed if missing.
2. `adapter = adapter_for(req.provider)`.
3. preflight subs = `adapter.supports(profile, req)`.
4. stream adapter events; on the final `USAGE` event, **attach our cost** (`usage.cost_usd = quantize_usd(compute_cost(profile, usage))`) and merge preflight + runtime-discovered substitutions.
5. normalize any transport error into an `ERROR` event (no SDK leak).

### Per-provider usage mapping — NEW (table)
See `contracts/usage-mapping.md`. The authoritative map from each provider's (LiteLLM-surfaced) usage fields → `NormalizedUsage`, including the Anthropic inclusive/exclusive divergence.

## State / lifecycle
No stateful entities. A profile's `availability` (`active → deprecated → retired`) is data, not a state machine this feature mutates; a `retired` model is treated as a caller-visible condition (edge case), not executed as active.
