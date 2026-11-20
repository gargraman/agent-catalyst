"""M0.0.a (RED) — compute_snapshot_hash: the version content-identity (01L §4.2/§4.3).

This is the C2 property-test spec: every DRIFT_TRIGGER must change the hash; io_contract is
over-covered (changes the hash but is not a trigger); kb_index_version is excluded; and an
identical clone (same binding identities, different row UUIDs / order) must hash equal.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from runtime.hashing import DRIFT_TRIGGERS, compute_snapshot_hash
from tests.conftest import _tool_binding, clone_av, make_av


# One in-place mutator per DRIFT_TRIGGER. Kept in lockstep with DRIFT_TRIGGERS by a test
# below, so adding a trigger without a test (or vice-versa) fails loudly.
def _m_model(av): av.model_config_["model"] = "gpt-4o-mini"
def _m_provider(av): av.model_config_["provider"] = "openai"
def _m_api_version(av): av.model_config_["api_version"] = "2099-12-31"
def _m_params(av): av.model_config_["params"]["temperature"] = 0.9
def _m_prompt_hash(av): av.prompt_hash = "ph-ffff"
def _m_guardrail(av): av.guardrail_config = {"input": [{"name": "pii"}], "output": []}
def _m_memory(av): av.memory_config["window"] = 99
def _m_context(av): av.context_config["limit_tokens"] = 4000
def _m_tool_bindings(av): av.tool_bindings[0].config_snapshot["url"] = "https://moved.example.com"
def _m_skill_bindings(av): av.skill_bindings[0].skill_content_hash = "sch-zzzz"
def _m_embedding_model(av): av.retrieval_bindings[0].kb.embedding_model = "text-embedding-3-large"
def _m_kb_binding(av): av.retrieval_bindings[0].kb_id = "kb-2"
def _m_retrieval_config(av): av.retrieval_bindings[0].retrieval_config["top_k"] = 10
def _m_capability_substitution(av): av.profile_snapshot["features"]["structured_output"] = False

MUTATORS = {
    "model": _m_model,
    "provider": _m_provider,
    "api_version": _m_api_version,
    "params": _m_params,
    "prompt_hash": _m_prompt_hash,
    "guardrail_config": _m_guardrail,
    "memory_config": _m_memory,
    "context_config": _m_context,
    "tool_bindings": _m_tool_bindings,
    "skill_bindings": _m_skill_bindings,
    "embedding_model": _m_embedding_model,
    "kb_binding": _m_kb_binding,
    "retrieval_config": _m_retrieval_config,
    "capability_substitution": _m_capability_substitution,
}


def test_mutators_cover_drift_triggers_exactly():
    """Guard: the tested mutators and the authoritative DRIFT_TRIGGERS set never drift apart."""
    assert set(MUTATORS) == set(DRIFT_TRIGGERS)


@pytest.mark.parametrize("trigger", sorted(MUTATORS))
def test_each_drift_trigger_changes_the_hash(trigger):
    baseline = compute_snapshot_hash(make_av())
    av = make_av()
    MUTATORS[trigger](av)
    assert compute_snapshot_hash(av) != baseline, f"{trigger} did not bust the hash"


def test_io_contract_change_changes_hash_over_coverage():
    """io_contract is NOT a DRIFT_TRIGGER but is hashed anyway (safe over-coverage, 01L §4.3)."""
    baseline = compute_snapshot_hash(make_av())
    av = make_av()
    av.io_contract = {"output_format": "json", "output_schema": {"type": "object"}, "input_variables": []}
    assert compute_snapshot_hash(av) != baseline


def test_kb_index_version_is_excluded():
    """KB content is live & independent (04 §9) — a re-ingest bump must NOT change the hash."""
    baseline = compute_snapshot_hash(make_av())
    av = make_av()
    av.retrieval_bindings[0].kb_index_version = 999
    assert compute_snapshot_hash(av) == baseline


def test_injection_toggle_changes_hash():
    """injection lives in its own column but IS part of the retrieval_config trigger (review H2)."""
    baseline = compute_snapshot_hash(make_av())
    av = make_av()
    av.retrieval_bindings[0].injection = "always_on"
    assert compute_snapshot_hash(av) != baseline


def test_clone_stability_ignores_row_uuid():
    """An identical version clone (same binding identities, different per-row UUIDs) hashes equal."""
    a = make_av()
    b = make_av()
    b.id = "ver-9999"
    b.tool_bindings[0].id = "bind-9999"
    assert compute_snapshot_hash(a) == compute_snapshot_hash(b)


def test_tool_binding_order_is_irrelevant():
    """Binding lists are sorted by a stable identity key, so load order can't change the hash."""
    t1 = _tool_binding(tool_id="tool-1", id="b1")
    t2 = _tool_binding(tool_id="tool-2", id="b2")
    av1 = make_av(tool_bindings=[t1, t2])
    av2 = make_av(tool_bindings=[clone_av(t2), clone_av(t1)])
    assert compute_snapshot_hash(av1) == compute_snapshot_hash(av2)


def test_params_float_repr_is_stable():
    """F16: equal numeric params expressed as float vs Decimal must hash identically."""
    a = make_av()
    b = make_av()
    b.model_config_["params"]["temperature"] = Decimal("0.2")  # == float 0.2 semantically
    assert compute_snapshot_hash(a) == compute_snapshot_hash(b)


# --- regression (code-review finding 1/2): stored JSON null in profile_snapshot --------------------
def test_profile_snapshot_json_null_coalesces_like_absent_or_empty():
    """A stored JSON null ("features": null / "fallback": null) must behave like an absent key /
    {} — no AttributeError on the fallback branch, and the same hash as the empty/absent forms."""
    absent = make_av(profile_snapshot={})
    empty = make_av(profile_snapshot={"features": {}, "fallback": {}})
    nulled = make_av(profile_snapshot={"features": None, "fallback": None})
    h = compute_snapshot_hash(nulled)  # must not raise (old code did on fallback=None)
    assert h == compute_snapshot_hash(absent) == compute_snapshot_hash(empty)


def test_profile_snapshot_nested_fallback_features_null_coalesces():
    """fallback.features: null (nested JSON null) must coalesce to {} too, symmetric with the primary
    branch — else two identical fallback configs hash differently (code-review finding, 2nd pass)."""
    absent = make_av(profile_snapshot={"fallback": {}})
    nested_null = make_av(profile_snapshot={"fallback": {"features": None}})
    assert compute_snapshot_hash(nested_null) == compute_snapshot_hash(absent)
