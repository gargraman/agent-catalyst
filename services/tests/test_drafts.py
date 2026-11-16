"""Feature 026 (T014, research D1/D2/D3) — `api.drafts`: the editable draft value object. Infra-free (no DB).

  * `Draft` validates like AgentCreate: known provider/model profile, io_contract.input_schema, allow-listed params
  * `normalize()` sorts + dedupes the id lists and drops unset (None) params → a canonical JSON-able dict
  * `resolve(agent, active)` = the stored draft, or the active version projected onto the Draft shape when NULL
  * `dirty(agent, active)` = normalized(draft) != normalized(active); False when draft IS NULL
  * `transient_version(agent, draft)` = an UNSAVED AgentVersion-shaped object `to_snapshot()` accepts (D3)
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from api.models import AgentVersion, RetrievalBinding, SkillBinding, ToolBinding

A, B, C = (str(uuid.UUID(int=i)) for i in (1, 2, 3))
IO = {"input_schema": {"type": "object"}, "output_format": "text"}


def _d():
    import api.drafts as d
    return d


def _draft(**over) -> dict:
    base = {"provider": "openai", "model": "gpt-5-mini", "model_params": {"temperature": 0.2},
            "system_prompt": "be brief", "io_contract": IO, "kb_ids": [], "tool_ids": [], "skill_ids": []}
    base.update(over)
    return base


def _version(**over) -> AgentVersion:
    v = AgentVersion(id=uuid.uuid4(), workspace_id=uuid.uuid4(), agent_id=uuid.uuid4(), label="v1",
                     system_prompt="be brief", prompt_hash="ph", snapshot_hash="sh",
                     model_config_={"provider": "openai", "model": "gpt-5-mini", "params": {"temperature": 0.2}},
                     profile_snapshot={}, io_contract=IO, guardrail_config={}, memory_config={}, context_config={})
    v.retrieval_bindings, v.tool_bindings, v.skill_bindings = [], [], []
    for k, val in over.items():
        setattr(v, k, val)
    return v


def _agent(draft=None, workspace_id=None):
    return SimpleNamespace(id=uuid.uuid4(), workspace_id=workspace_id or uuid.uuid4(), draft=draft)


# --- Draft validation ------------------------------------------------------------------------------------------------
def test_valid_draft_parses():
    d = _d().Draft.model_validate(_draft())
    assert d.provider == "openai" and d.model == "gpt-5-mini"


@pytest.mark.parametrize("over", [
    {"provider": "nope"},                                     # unknown provider
    {"model": "gpt-does-not-exist"},                          # model not in the profiles
    {"model": "text-embedding-3-small"},                      # an embedding model is not a chat model
    {"io_contract": {"output_format": "text"}},               # no input_schema
    {"model_params": {"temperature": 9}},                     # out of range
    {"model_params": {"top_p": -0.1}},
    {"model_params": {"max_tokens": 0}},
    {"model_params": {"max_tokens": 10_000_000}},             # above the profile's max_output_tokens
    {"model_params": {"api_key": "sk-smuggled"}},             # unknown key → refused (params flow into transport kwargs)
    {"model_params": {"temperature": True}},                  # bool is not a number
    {"kb_ids": ["not-a-uuid"]},
])
def test_invalid_draft_is_refused(over):
    with pytest.raises(ValidationError):
        _d().Draft.model_validate(_draft(**over))


# --- normalize -------------------------------------------------------------------------------------------------------
def test_normalize_sorts_and_dedupes_ids_and_drops_unset_params():
    n = _d().normalize(_draft(kb_ids=[B, A, B], tool_ids=[C, A], skill_ids=[A, A],
                              model_params={"temperature": 0.2, "top_p": None}))
    assert n["kb_ids"] == [A, B] and n["tool_ids"] == [A, C] and n["skill_ids"] == [A]
    assert n["model_params"] == {"temperature": 0.2}


def test_normalize_is_json_plain():
    import json
    n = _d().normalize(_d().Draft.model_validate(_draft(kb_ids=[A])))
    assert json.loads(json.dumps(n)) == n                     # storable in agents.draft as-is


# --- resolve / dirty -------------------------------------------------------------------------------------------------
def test_resolve_null_draft_projects_the_active_version():
    kb, tool, skill = uuid.UUID(B), uuid.UUID(C), uuid.UUID(A)
    v = _version()
    v.retrieval_bindings = [RetrievalBinding(kb_id=kb, kb_index_version=1, retrieval_config={}, injection="tool")]
    v.tool_bindings = [ToolBinding(kind="http", tool_id=tool, schema_snapshot={}, config_snapshot={})]
    v.skill_bindings = [SkillBinding(skill_id=skill, skill_content_hash="h")]
    r = _d().resolve(_agent(None), v)
    assert r == _d().normalize(_draft(kb_ids=[B], tool_ids=[C], skill_ids=[A]))


def test_resolve_returns_the_stored_draft():
    stored = _d().normalize(_draft(system_prompt="edited"))
    assert _d().resolve(_agent(stored), _version())["system_prompt"] == "edited"


def test_dirty_false_when_draft_is_null():
    assert _d().dirty(_agent(None), _version()) is False


def test_dirty_true_when_prompt_differs_and_false_when_reverted():
    v = _version()
    assert _d().dirty(_agent(_d().normalize(_draft(system_prompt="changed"))), v) is True
    assert _d().dirty(_agent(_d().normalize(_draft())), v) is False


def test_reordering_ids_or_adding_unset_param_is_not_dirty():
    kb1, kb2 = uuid.UUID(A), uuid.UUID(B)
    v = _version()
    v.retrieval_bindings = [RetrievalBinding(kb_id=k, kb_index_version=1, retrieval_config={}, injection="tool")
                            for k in (kb1, kb2)]
    stored = _draft(kb_ids=[B, A], model_params={"temperature": 0.2, "max_tokens": None})
    assert _d().dirty(_agent(stored), v) is False


def test_dirty_with_no_active_version_is_true_for_any_draft():
    assert _d().dirty(_agent(_d().normalize(_draft())), None) is True
    assert _d().dirty(_agent(None), None) is False


# --- transient_version -----------------------------------------------------------------------------------------------
def test_transient_version_is_accepted_by_to_snapshot():
    agent = _agent()
    draft = _d().Draft.model_validate(_draft(system_prompt="draft prompt", model_params={"temperature": 0.7}))
    tv = _d().transient_version(agent, draft)
    snap = tv.to_snapshot()
    assert snap.system_prompt == "draft prompt"
    assert snap.model_config["model"] == "gpt-5-mini" and snap.model_config["params"] == {"temperature": 0.7}
    assert snap.workspace_id == str(agent.workspace_id) and snap.agent_id == str(agent.id)
    assert snap.profile_snapshot.get("model") == "gpt-5-mini"   # frozen profile, so the runtime can resolve it
    assert snap.snapshot_hash                                    # content identity computable for an unsaved draft
