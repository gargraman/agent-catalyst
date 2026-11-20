# Quickstart — Provider Gateway (M1)

Validation/run guide. No live provider calls — everything here is offline (mocked transport).

## Prerequisites
```bash
cd services
pip install -e '.[api,test]'      # brings in litellm (NEW), pytest, pytest-asyncio
```
(No DB, Redis, or compose stack needed — this layer is infra-free. No `AC_*_DATABASE_URL` required.)

## Run the feature's tests
```bash
cd services
# The whole M1 provider suite (all offline):
pytest -q tests/test_profile_loader.py tests/test_providers.py tests/test_anthropic_adapter.py tests/test_gateway.py
# Purity invariant (runtime/ imports no inbound web framework; litellm outbound is fine):
pytest -q tests/test_runtime_purity.py
# Full unit batch (integration auto-deselected):
pytest -q -m "not integration"
```

## What "pass" looks like (maps to Success Criteria)
- **SC-001** adapter contract: the parametrized contract test passes for `openai`, `ollama`, `anthropic` (same event vocabulary, usage obligations, substitution obligation).
- **SC-002** cost: each `test_*_cost_matches_hand_calc` asserts `compute_cost(profile, usage) == <literal>` exactly; a test confirms LiteLLM's `response_cost` is never consulted.
- **SC-003** substitutions: weak-profile requests produce the expected `Substitution`s; plain requests produce none.
- **SC-004** purity: `test_runtime_purity.py` green.
- **SC-005** errors: simulated LiteLLM `Timeout`/`APIError`/`BadRequestError` surface as a normalized `ERROR` event / normalized exception — no raw LiteLLM type escapes.
- **SC-006** secrets: the non-leakage test finds the `api_key` in 0 emitted events/usage/substitutions/errors.
- **SC-007** profiles: `resolve_profile("anthropic", "<judge/model>")` loads a reviewed profile for each seeded Claude model incl. `claude-haiku-5-5`.

## Manual smoke (optional, still offline)
```python
import asyncio
from runtime.providers.ir import NormalizedRequest
from runtime.providers.gateway import stream
# monkeypatch runtime.providers.transport in a test; do NOT call a live provider here.
```

## Adding a new provider later (proves FR-002/US4)
1. Drop a profile JSON in `runtime/providers/profiles_data/`.
2. Add `<provider>_adapter.py` implementing `ProviderAdapter` (map usage per `contracts/usage-mapping.md`), `register_adapter(...)`.
3. Add it to the parametrized contract test. No caller-side change.

## Deferred (not in M1)
Live/paid call (M2 skeleton, under the paid-call protocol), `runtime.invoke` spine (M2), eval judge via gateway (M3), FastAPI/SSE (Phase 4), Vault key custody (P1).
