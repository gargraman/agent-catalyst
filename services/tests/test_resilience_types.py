"""T007 (feature 025) — pure resilience value types: policy defaults + validation, traffic classing, breaker key shape.
Contract: data-model §A1–A5, research D3/D5/D6. Infra-free."""
from __future__ import annotations

import dataclasses

import pytest

from runtime.providers import resilience


def test_policy_defaults_match_spec():
    p = resilience.ResiliencePolicy()
    assert (p.max_attempts, p.backoff_base_s, p.per_wait_cap_s, p.min_attempt_budget_s,
            p.failure_threshold, p.cooldown_s, p.batch_concurrency) == (3, 0.5, 8.0, 0.5, 5, 30.0, 4)


def test_policy_is_immutable():
    p = resilience.ResiliencePolicy()
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.max_attempts = 9  # type: ignore[misc]


@pytest.mark.parametrize("field", ["backoff_base_s", "per_wait_cap_s", "min_attempt_budget_s",
                                   "failure_threshold", "cooldown_s", "batch_concurrency"])
@pytest.mark.parametrize("bad", [0, -1])
def test_policy_rejects_non_positive(field, bad):
    with pytest.raises(ValueError):
        resilience.ResiliencePolicy(**{field: bad})


@pytest.mark.parametrize("bad", [0, -2])
def test_policy_rejects_max_attempts_below_one(bad):
    with pytest.raises(ValueError):
        resilience.ResiliencePolicy(max_attempts=bad)


def test_policy_allows_single_attempt_kill_switch():
    # AC_PROVIDER_MAX_ATTEMPTS=1 is the documented retry kill switch (plan: rollout).
    assert resilience.ResiliencePolicy(max_attempts=1).max_attempts == 1


def test_eval_source_is_batch():
    assert resilience.traffic_class("eval") == "batch"


@pytest.mark.parametrize("source", ["playground", "api", "channel", "tool_test", "subagent", "something-new"])
def test_other_sources_are_live(source):
    # Unknown sources default to live: the safe direction — live is never refused by a batch-opened breaker.
    assert resilience.traffic_class(source) == "live"


def test_breaker_key_has_no_tenant_fields():
    # FR-013 / data-model §A3: breaker state is shared across tenants and must carry no tenant identifier.
    assert [f.name for f in dataclasses.fields(resilience.BreakerKey)] == ["provider", "model", "cls"]
    k = resilience.BreakerKey("openai", "gpt-5-mini", "live")
    assert hash(k) == hash(resilience.BreakerKey("openai", "gpt-5-mini", "live"))   # usable as a dict key


def test_admission_values():
    assert {a.value for a in resilience.Admission} == {"closed", "trial", "refused"}


def test_breaker_store_protocol_shape():
    class Dummy:
        async def admit(self, key, *, also_check=None, trial_ttl_s): ...
        async def record_success(self, key, *, was_trial): ...
        async def record_failure(self, key, *, was_trial): ...

    assert isinstance(Dummy(), resilience.BreakerStore)
    assert not isinstance(object(), resilience.BreakerStore)
