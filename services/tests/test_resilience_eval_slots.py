"""Feature 025 US3 follow-up (code review 2026-11-15, owner chose option A) — eval queue time must never be charged to
the agent's own run deadline, plus the two deadline fixes that ride with it.

Why: the gateway bulkhead's queue wait sits INSIDE invoke's `asyncio.timeout(deadline)`. Under concurrent eval runs a
case could end as TIMEOUT (and its latency absorb queue time) because of platform queueing — and the drift report
would blame the agent/model version. Option A: `run_eval` takes a per-(provider, model) CASE slot on the process Guard
(separate from the gateway bulkhead — sharing it would deadlock) BEFORE invoke starts, so the deadline only ever
covers real work.

Also pinned here:
  * first-attempt wire timeout subtracts the bulkhead queue wait (a call that never waited still gets exactly
    `timeout` — `test_gateway.py` pins that);
  * ingestion embeddings carry a real wire timeout (`AC_INGEST_EMBED_TIMEOUT_S`), so a hung provider call cannot
    hold a batch slot forever.
"""
from __future__ import annotations

import asyncio
import json
import random

import pytest

from evals.runner import run_eval
from runtime.providers import gateway, resilience
from runtime.providers.ir import NormalizedRequest
from runtime.run import Outcome
from tests._evals_helpers import VALID_OUTPUT, candidate_snapshot, make_baseline, make_eval_case
from tests._fault_transport import FaultScript, ok
from tests._invoke_helpers import CapturingSink, make_context
from evals.drift import EvalPolicy

pytestmark = pytest.mark.asyncio

MODEL = "gpt-5-mini"


def _guard(*, cap: int = 1, seen: list | None = None) -> resilience.Guard:
    observer = (lambda name, labels: seen.append((name, labels))) if seen is not None else None
    return resilience.Guard(resilience.ResiliencePolicy(batch_concurrency=cap), observer=observer,
                            rng=random.Random(3))


def _req() -> NormalizedRequest:
    return NormalizedRequest(provider="openai", model=MODEL, api_version=None,
                             messages=[{"role": "user", "content": "hi"}], system=None, params={})


# ---- A: concurrent eval runs never time out because of platform queueing ------------------------------------------
async def test_concurrent_eval_runs_do_not_charge_queue_wait_to_the_case_deadline(monkeypatch):
    # cap 1, every provider call takes 0.06 s, case deadline 0.15 s. Without case slots, three runs' candidate calls
    # queue in the gateway bulkhead INSIDE the deadline → the third waits 0.12 s + 0.06 s on the wire > 0.15 s.
    FaultScript([ok(json.dumps(VALID_OUTPUT), delay_s=0.06)]).install(monkeypatch)
    guard = _guard(cap=1)
    sinks = [CapturingSink() for _ in range(3)]
    cases = [make_eval_case(cid=f"c{i}") for i in range(2)]
    baseline = make_baseline(per_case={c.id: json.dumps(VALID_OUTPUT) for c in cases})
    policy = EvalPolicy(min_cases=1, quality_threshold=10.0, efficiency_budget_pct=10.0)

    await asyncio.gather(*(
        run_eval(cases, candidate_snapshot(), baseline, policy,
                 make_context(guard=guard, deadline_s=0.15, record_run=sink), workspace_id="ws_1")
        for sink in sinks))

    outcomes = [r.outcome for s in sinks for r in s.runs]
    assert len(outcomes) >= 6                                           # 3 runs × 2 candidate calls (+ judges)
    assert Outcome.TIMEOUT not in outcomes


async def test_case_slot_caps_per_provider_model_and_reports_the_wait():
    seen: list = []
    guard = _guard(cap=2, seen=seen)
    inside = peak = 0

    async def case():
        nonlocal inside, peak
        async with guard.case_slot("openai", MODEL):
            inside += 1
            peak = max(peak, inside)
            await asyncio.sleep(0.02)
            inside -= 1
    await asyncio.gather(*(case() for _ in range(6)))
    assert peak == 2
    waits = [labels for name, labels in seen if name == "eval_case_wait"]
    assert len(waits) == 6 and all(set(w) == {"provider", "model", "seconds"} for w in waits)
    assert sum(1 for w in waits if w["seconds"] > 0) >= 4


async def test_case_slots_are_separate_from_the_gateway_bulkhead(monkeypatch):
    """Holding EVERY case slot must not block the gateway's batch slot — a shared semaphore would deadlock."""
    FaultScript([ok("x")]).install(monkeypatch)
    guard = _guard(cap=1)
    async with guard.case_slot("openai", MODEL):
        events = await asyncio.wait_for(
            _collect(gateway.stream(_req(), api_key="k", timeout=5.0, guard=guard, traffic_class="batch")), 1.0)
    assert events


async def test_eval_case_wait_feeds_its_histogram():
    from api import metrics
    hist = metrics.ac_eval_case_wait_seconds.labels(provider="openai", model=MODEL)
    before = hist._sum.get()
    metrics.record_resilience_event("eval_case_wait", {"provider": "openai", "model": MODEL, "seconds": 0.5})
    assert hist._sum.get() == pytest.approx(before + 0.5)


# ---- first-attempt wire timeout excludes bulkhead queue time ------------------------------------------------------
async def test_first_attempt_wire_timeout_subtracts_queue_wait(monkeypatch):
    script = FaultScript([ok("hold", delay_s=0.2), ok("x")]).install(monkeypatch)
    guard = _guard(cap=1)
    holder = asyncio.create_task(_collect(gateway.stream(_req(), api_key="k", timeout=5.0, guard=guard,
                                                         traffic_class="batch")))
    await asyncio.sleep(0.01)
    await _collect(gateway.stream(_req(), api_key="k", timeout=5.0, guard=guard, traffic_class="batch"))
    await holder
    assert script.kwargs_seen[0]["timeout"] == 5.0                      # never waited → exactly `timeout`
    assert script.kwargs_seen[1]["timeout"] <= 5.0 - 0.15               # waited ~0.19 s → deducted


async def test_first_embed_attempt_wire_timeout_subtracts_queue_wait(monkeypatch):
    script = FaultScript([ok(delay_s=0.2), ok()]).install(monkeypatch)
    guard = _guard(cap=1)

    def one():
        return gateway.embed(["chunk"], provider="openai", model="text-embedding-3-small", api_key="k",
                             timeout=5.0, guard=guard, traffic_class="batch")
    holder = asyncio.create_task(one())
    await asyncio.sleep(0.01)
    await one()
    await holder
    assert script.kwargs_seen[0]["timeout"] == 5.0
    assert script.kwargs_seen[1]["timeout"] <= 5.0 - 0.15


# ---- ingestion embeddings are wire-bounded ------------------------------------------------------------------------
async def test_ingest_embedder_passes_the_configured_wire_timeout(monkeypatch):
    from api import ingest_executor, resilience_wiring
    from api.settings import get_settings
    seen: dict = {}

    async def fake_embed(texts, **kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop here")
    monkeypatch.setattr(gateway, "embed", fake_embed)
    resilience_wiring.reset_for_tests()
    try:
        embed, _ = await ingest_executor._embedder_for("00000000-0000-0000-0000-000000000001",
                                                       "amazon.titan-embed-text-v2:0")
        with pytest.raises(RuntimeError):
            await embed(["anything"])
        assert seen["timeout"] == get_settings().ingest_embed_timeout_s
        assert seen["timeout"] is not None and seen["timeout"] > 0
    finally:
        resilience_wiring.reset_for_tests()


def test_ingest_embed_timeout_setting_default_and_validation(monkeypatch):
    from pydantic import ValidationError
    from api.settings import Settings
    assert Settings().ingest_embed_timeout_s == 60.0
    monkeypatch.setenv("AC_INGEST_EMBED_TIMEOUT_S", "0")
    with pytest.raises(ValidationError):
        Settings()


async def _collect(agen) -> list:
    return [ev async for ev in agen]
