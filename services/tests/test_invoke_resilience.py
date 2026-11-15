"""T011 (feature 025, US1) — retries surface honestly on the RunRecord through `runtime.invoke_result`
(contract runtime-resilience §8; spec FR-005 / FR-015 / SC-001 / SC-002).

Real gateway + real adapter; only the transport seam is faked (`_fault_transport`). The Guard rides on
`RunContext.guard` with an injected fake clock/sleep, so retry waits cost ~0 wall time. No live calls.
"""
from __future__ import annotations

import random
from decimal import Decimal
from types import SimpleNamespace

import pytest

from runtime.invoke import invoke_result
from runtime.providers import resilience, transport
from runtime.providers.transport import TransportError
from runtime.retrieval.port import Passage, SearchOutcome
from runtime.run import Outcome
from runtime.snapshot import RetrievalRef
from tests._fault_transport import FaultScript, fail, ok, partial
from tests._invoke_helpers import JSON_IO, make_context, make_snapshot

pytestmark = pytest.mark.asyncio

INPUTS = {"message": "go"}
VALID = '{"severity":"high","score":8}'


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0
        self.waits: list[float] = []

    def now(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        self.waits.append(s)
        self.t += s


def _guard(clock: FakeClock) -> "resilience.Guard":
    return resilience.Guard(policy=resilience.ResiliencePolicy(), clock=clock.now, sleep=clock.sleep,
                            rng=random.Random(11))


def _ctx(clock: FakeClock, **kw):
    return make_context(guard=_guard(clock), **kw)


# (a) fail×2 then success → SUCCESS, retries == 2, cost = priced REPORTED usage only (FR-015 as amended)
async def test_a_two_retries_then_success_cost_is_reported_usage_only(monkeypatch):
    FaultScript([ok("hello", prompt=10, completion=3)]).install(monkeypatch)
    clean = await invoke_result(make_snapshot(), INPUTS, _ctx(FakeClock()))

    script = FaultScript([fail(503), fail(429), ok("hello", prompt=10, completion=3)]).install(monkeypatch)
    res = await invoke_result(make_snapshot(), INPUTS, _ctx(FakeClock()))
    assert script.calls == 3
    assert res.run.outcome is Outcome.SUCCESS
    assert res.text == "hello"
    assert res.run.retries == 2
    assert clean.run.retries == 0
    # failed attempts reported no usage → contribute 0; cost is exactly the clean run's (never a guessed charge)
    assert res.run.cost_usd == clean.run.cost_usd and res.run.cost_usd > Decimal("0")
    assert res.run.usage.input_tokens == clean.run.usage.input_tokens


# (b) validated mode: original + 1 correction turn, each retried once → retries == 2
async def test_b_correction_turns_each_run_their_own_attempt_loop(monkeypatch):
    script = FaultScript([fail(503), ok("{bad"), fail(503), ok(VALID)]).install(monkeypatch)
    res = await invoke_result(make_snapshot(io_contract=JSON_IO), INPUTS, _ctx(FakeClock(), max_corrections=2))
    assert script.calls == 4
    assert res.run.outcome is Outcome.SUCCESS and res.run.correction_turns == 1
    assert res.run.retries == 2


# (c) a retried query embedding adds to retries
PASSAGE = Passage(chunk_id="ch-1", kb_id="kb-h", document_id="doc-h", document_name="h.md",
                  text="Annual leave is 27 days.", token_count=6, score=0.9, meta={})


class FakeKnowledge:
    async def search(self, ref, qvec, *, top_k, threshold):
        return SearchOutcome("ok", (PASSAGE,))


def _kb_ref():
    return RetrievalRef(kb_id="kb-h", embedding_model="text-embedding-3-small",
                        retrieval_config={"top_k": 6, "similarity_threshold": 0.2, "max_ctx_tokens": 2000},
                        injection="always_on", kb_index_version=1, embedding_provider="openai", kb_name="H")


async def test_c_retried_query_embedding_counts_toward_retries(monkeypatch):
    FaultScript([ok("27 days")]).install(monkeypatch)        # chat stream: clean
    embed_calls: list = []

    async def flaky_embed(**kwargs):                          # embedding: transient once, then OK
        embed_calls.append(kwargs)
        if len(embed_calls) == 1:
            raise TransportError("injected 503", retryable=True, status=503)
        return SimpleNamespace(data=[{"index": 0, "embedding": [0.1] * 1024}],
                               usage=SimpleNamespace(prompt_tokens=5), _hidden_params={})
    monkeypatch.setattr(transport, "embed", flaky_embed)

    ctx = _ctx(FakeClock(), knowledge=FakeKnowledge(), embedding_api_key="sk-embed")
    res = await invoke_result(make_snapshot(retrieval=(_kb_ref(),)), INPUTS, ctx)
    assert len(embed_calls) == 2
    assert res.run.outcome is Outcome.SUCCESS
    assert res.run.retrieval.bindings[0].status == "used"          # retrieval succeeded after the retry
    assert res.run.retries == 1


# (d) Retry-After 3600 s with a 30 s deadline → ends within deadline + 1 s of (injected) time, never hangs (SC-002)
async def test_d_huge_retry_after_never_exceeds_deadline(monkeypatch):
    script = FaultScript([fail(429, retry_after=3600.0)]).install(monkeypatch)
    clock = FakeClock()
    res = await invoke_result(make_snapshot(), INPUTS, _ctx(clock, deadline_s=30.0))
    assert res.run.outcome in (Outcome.TIMEOUT, Outcome.PROVIDER_ERROR)
    assert clock.t <= 31.0
    assert all(w <= 8.0 for w in clock.waits)                 # per-wait cap honoured
    assert script.calls <= 3


# (e) non-transient → PROVIDER_ERROR, no retry
async def test_e_non_transient_is_provider_error_without_retry(monkeypatch):
    script = FaultScript([fail(401), ok("never")]).install(monkeypatch)
    res = await invoke_result(make_snapshot(), INPUTS, _ctx(FakeClock()))
    assert script.calls == 1
    assert res.run.outcome is Outcome.PROVIDER_ERROR and res.run.retries == 0


# (f) transient AFTER first token → PROVIDER_ERROR (pinned, FR-005 — not DEGRADED), no retry, text not duplicated
async def test_f_failure_after_first_token_is_provider_error_not_retried(monkeypatch):
    script = FaultScript([partial("par", "tial"), ok("DUPLICATE")]).install(monkeypatch)
    res = await invoke_result(make_snapshot(), INPUTS, _ctx(FakeClock()))
    assert script.calls == 1
    assert res.run.outcome is Outcome.PROVIDER_ERROR
    assert res.run.outcome is not Outcome.DEGRADED
    assert res.run.retries == 0
    assert res.text == "partial"


# RunContext / RunRecord compatibility: both new fields default safely
async def test_defaults_guard_none_and_retries_zero(monkeypatch):
    FaultScript([ok("hi")]).install(monkeypatch)
    ctx = make_context()
    assert ctx.guard is None
    res = await invoke_result(make_snapshot(), INPUTS, ctx)
    assert res.run.retries == 0


# ---- T019 (US2): breaker refusal surfaces honestly on the RunRecord (contract §8, spec FR-012) -------------------
async def _opened_guard(clock: FakeClock, key) -> "resilience.Guard":
    g = _guard(clock)
    for _ in range(g.policy.failure_threshold):
        await g.store.record_failure(key, was_trial=False)
    return g


async def test_unavailable_breaker_open_run_is_provider_unavailable(monkeypatch):
    script = FaultScript([ok("never")]).install(monkeypatch)
    # Open the LIVE key: refuses live traffic and (B6) batch traffic too, whatever class make_context's source maps to.
    guard = await _opened_guard(FakeClock(), resilience.BreakerKey("openai", "gpt-5-mini", "live"))
    res = await invoke_result(make_snapshot(), INPUTS, make_context(guard=guard))
    assert script.calls == 0
    assert res.run.outcome is Outcome.PROVIDER_UNAVAILABLE
    assert res.run.outcome.value == "provider_unavailable"
    assert res.run.usage is None
    assert not res.run.cost_usd                                        # None or 0 — never a guessed charge
    assert res.run.retries == 0
    assert res.run.error["type"] == "provider_unavailable"


async def test_unavailable_breaker_open_validated_mode_too(monkeypatch):
    script = FaultScript([ok(VALID)]).install(monkeypatch)
    guard = await _opened_guard(FakeClock(), resilience.BreakerKey("openai", "gpt-5-mini", "live"))
    res = await invoke_result(make_snapshot(io_contract=JSON_IO), INPUTS, make_context(guard=guard, max_corrections=2))
    assert script.calls == 0
    assert res.run.outcome is Outcome.PROVIDER_UNAVAILABLE and res.run.correction_turns == 0


async def test_unavailable_query_embedding_degrades_binding_not_run(monkeypatch):
    FaultScript([ok("27 days")]).install(monkeypatch)                  # chat stream: clean
    embed_calls: list = []

    async def never_embed(**kwargs):
        embed_calls.append(kwargs)
        raise AssertionError("embedding transport must not be called while the breaker is open")
    monkeypatch.setattr(transport, "embed", never_embed)
    clock = FakeClock()
    guard = await _opened_guard(clock, resilience.BreakerKey("openai", "text-embedding-3-small", "live"))
    ctx = make_context(guard=guard, knowledge=FakeKnowledge(), embedding_api_key="sk-embed")
    res = await invoke_result(make_snapshot(retrieval=(_kb_ref(),)), INPUTS, ctx)
    assert embed_calls == []
    assert res.run.outcome is Outcome.SUCCESS                         # 024 FR-020 parity: retrieval degrades, run lives
    [binding] = res.run.retrieval.bindings
    assert binding.status == "degraded" and binding.degraded_reason == "provider_unavailable"
    assert res.run.retries == 0


# ---- T028 (US3): the run's traffic class reaches the gateway — chat AND query embedding (research D6) -------------
@pytest.mark.parametrize("source,expected", [("playground", "live"), ("api", "live"), ("channel", "live"),
                                             ("eval", "batch"), ("subagent", "live"), ("something_new", "live")])
async def test_traffic_class_follows_source_for_chat_and_query_embedding(monkeypatch, source, expected):
    from runtime.providers import gateway
    FaultScript([ok("27 days")]).install(monkeypatch)
    classes: dict[str, list] = {"stream": [], "embed": []}
    real_stream, real_embed = gateway.stream, gateway.embed

    def spy_stream(req, **kw):
        classes["stream"].append(kw.get("traffic_class", "live"))
        return real_stream(req, **kw)

    async def spy_embed(texts, **kw):
        classes["embed"].append(kw.get("traffic_class", "live"))
        return await real_embed(texts, **kw)
    monkeypatch.setattr(gateway, "stream", spy_stream)
    monkeypatch.setattr(gateway, "embed", spy_embed)
    ctx = _ctx(FakeClock(), source=source, knowledge=FakeKnowledge(), embedding_api_key="sk-embed")
    res = await invoke_result(make_snapshot(retrieval=(_kb_ref(),)), INPUTS, ctx)
    assert res.run.outcome is Outcome.SUCCESS
    assert classes["stream"] == [expected] and classes["embed"] == [expected]
