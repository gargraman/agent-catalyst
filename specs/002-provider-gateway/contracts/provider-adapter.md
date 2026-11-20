# Contract — ProviderAdapter + Gateway

The seam every provider implements and the orchestrator that drives it. Callers (M2 runtime, M3 eval, Phase-4 API, channels) depend **only** on this contract — no provider-specific branching above the gateway (FR-002).

## ProviderAdapter (Protocol — `runtime/providers/base.py`)

```python
@runtime_checkable
class ProviderAdapter(Protocol):
    provider: str

    def supports(self, profile: CapabilityProfile, req: NormalizedRequest) -> list[Substitution]:
        """Preflight downgrades THIS provider needs for THIS request, from profile.features.
        Delegates to profiles.preflight_substitutions. Empty when fully supported."""

    def stream(
        self, req: NormalizedRequest, *, api_key: str, profile: CapabilityProfile
    ) -> AsyncIterator[NormalizedStreamEvent]:
        """Translate req → provider wire (via transport.py/LiteLLM), yield normalized events:
        TEXT_DELTA/TOOL_CALL_* as content streams, then exactly one final USAGE event whose
        NormalizedUsage carries RAW token counts (cost attached by the gateway, not here)."""
```

Registry: `register_adapter(adapter)` at import; `adapter_for(provider) -> ProviderAdapter` (lazy-imports the three adapter modules on first use).

### Conformance obligations (every adapter; one shared test parametrization)
1. **Event vocabulary**: yields only `EventType` members; content as `TEXT_DELTA`; exactly one terminal `USAGE`.
2. **Raw usage**: the `USAGE` event's `NormalizedUsage` holds raw provider counts; `cost_usd` left for the gateway (adapter must not invent cost).
3. **Substitutions**: `supports()` returns the correct preflight list; runtime-discovered downgrades appended to the `USAGE` event's `substitutions`.
4. **No secret leak**: `api_key` never appears in any yielded event payload.
5. **Error normalization**: a transport failure surfaces as a normalized `ERROR` event or a normalized exception — never a raw LiteLLM/SDK exception type crossing the contract.
6. **Wire-body matches substitutions**: a feature the profile lacks is actually omitted/emulated on the wire (so a recorded `Substitution` is never a lie, and nothing unsupported is sent raw).

## Gateway (`runtime/providers/gateway.py`)

```python
async def stream(
    req: NormalizedRequest, *, api_key: str, profile: CapabilityProfile | None = None
) -> AsyncIterator[NormalizedStreamEvent]:
    ...
```

Behavior (ordered):
1. Resolve profile (`profile or resolve_profile(req.provider, req.model, req.api_version)`); **fail closed** with a caller-visible error if none (FR-008) — no guessed model/pricing.
2. If `profile.availability == "retired"` → caller-visible condition (do not execute as active).
3. `adapter = adapter_for(req.provider)`; `preflight = adapter.supports(profile, req)`.
4. Stream adapter events through; on the terminal `USAGE`:
   - `usage.cost_usd = quantize_usd(compute_cost(profile, usage))` — **our** cost only (FR-003/004).
   - `substitutions = preflight + runtime_discovered` (dedup by (feature, action)).
   - if `profile` is provisional → mark cost an estimate (FR-010).
5. Any transport error → normalized `ERROR` event; a stream that ends with no `USAGE` → degraded/`ERROR`, never silent success (P8).

## Invariants (asserted by tests)
- `runtime/` imports no inbound web framework (`test_runtime_purity.py`). LiteLLM (outbound) is allowed.
- Cost is computed only by `cost.compute_cost`; LiteLLM `response_cost` is never read.
- `litellm` is called with `drop_params=False`.
