"""T020 (feature 025, US2, integration) — an open breaker reaches the API honestly (contract api §1/§4; FR-012).

The invoke route is SSE-only today (`_run_invoke` always returns a `StreamingResponse` — there is no JSON/non-streaming
invoke), so the reachable contract is the terminal SSE `error` frame with `code == "provider_unavailable"`, the persisted
run outcome, and the run counter. The `502 provider.unavailable` mapping in contract api §1 has no reachable route yet
(recorded as a deviation in tasks.md Evidence). Needs Postgres at head (`make up`). Transport faked — no live calls.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from api.db import tenant_session
from api.models import Run
from runtime.providers.resilience import BreakerKey
from tests._api_helpers import client, dev_headers, parse_sse, seed_workspace
from tests._fault_transport import FaultScript, ok

pytestmark = pytest.mark.integration

_CREATE = {"name": "Breaker Bot", "provider": "openai", "model": "gpt-5-mini",
           "system_prompt": "You are a breaker bot.",
           "io_contract": {"input_schema": {"type": "object", "properties": {"message": {"type": "string"}},
                                            "required": ["message"]}, "output_format": "text", "output_schema": None}}
_KEY = BreakerKey("openai", "gpt-5-mini", "live")


@pytest.fixture
async def opened_breaker():
    from api import resilience_wiring
    resilience_wiring.reset_for_tests()
    guard = resilience_wiring.get_guard()
    for _ in range(guard.policy.failure_threshold):
        await guard.store.record_failure(_KEY, was_trial=False)
    yield guard
    await guard.store.record_success(_KEY, was_trial=True)            # heal shared state (Redis may be configured)
    resilience_wiring.reset_for_tests()


async def test_open_breaker_sse_terminal_error_and_persisted_outcome(monkeypatch, opened_breaker):
    from api import metrics
    script = FaultScript([ok("never")]).install(monkeypatch)
    counter = metrics.ac_runs_total.labels(status="provider_unavailable", source="playground")
    before = counter._value.get()
    ws = await seed_workspace()
    async with client() as c:
        aid = (await c.post("/api/v1/agents", headers=dev_headers(workspace=ws), json=_CREATE)).json()["id"]
        r = await c.post(f"/api/v1/agents/{aid}/invoke", headers=dev_headers(workspace=ws),
                         json={"inputs": {"message": "hi"}})
    assert r.status_code == 200                                        # SSE: the stream opens, then fails honestly
    frames = parse_sse(r.text)
    errors = [d for name, d in frames if name == "error"]
    assert errors and errors[-1]["code"] == "provider_unavailable"
    assert frames[-1][0] == "done" and frames[-1][1]["status"] == "provider_unavailable"
    assert script.calls == 0
    async with tenant_session(ws) as s:
        [run] = list((await s.execute(select(Run))).scalars().all())
    assert run.outcome == "provider_unavailable"
    assert float(run.cost_usd or 0) == 0.0
    assert run.retry_count == 0
    assert counter._value.get() == before + 1
