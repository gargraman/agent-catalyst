"""US3 — typed domain model: CRUD, relationship navigation, and loud immutability
(feature 001, spec FR-001/010, SC-006).

Integration (real DB via ac_app tenant_session); skips when DB unreachable.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from api.db import tenant_session
from api.models import (Agent, AgentVersion, KnowledgeBase, RetrievalBinding, Skill, SkillBinding, Tool, ToolBinding,
                        Workspace)

pytestmark = pytest.mark.integration


async def _seed(session, ws_id):
    kb = KnowledgeBase(workspace_id=ws_id, slug=f"kb-{uuid.uuid4().hex[:6]}", name="KB",
                       embedding_model="text-embedding-3-small", embedding_dim=1536)
    agent = Agent(workspace_id=ws_id, name="orm-agent", status="draft")
    # 025 (0015): bindings must reference real same-workspace catalog rows (was: random dangling uuids)
    tool = Tool(workspace_id=ws_id, name="orm-tool", kind="http")
    skill = Skill(workspace_id=ws_id, slug=f"sk-{uuid.uuid4().hex[:6]}", name="orm-skill")
    session.add_all([kb, agent, tool, skill])
    await session.flush()
    av = AgentVersion(
        workspace_id=ws_id, agent_id=agent.id, label="v1", system_prompt="orig",
        prompt_hash="ph", snapshot_hash="seed", model_config_={"model": "m", "provider": "p"},
    )
    session.add(av)
    await session.flush()
    session.add_all([
        ToolBinding(workspace_id=ws_id, version_id=av.id, kind="http", tool_id=tool.id,
                    schema_snapshot={}, config_snapshot={}),
        SkillBinding(workspace_id=ws_id, version_id=av.id, skill_id=skill.id, skill_content_hash="h"),
        RetrievalBinding(workspace_id=ws_id, version_id=av.id, kb_id=kb.id, kb_index_version=1,
                         retrieval_config={}, injection="tool"),
    ])
    await session.flush()
    return agent.id, av.id


async def _new_ws():
    async with tenant_session(uuid.uuid4()) as s:
        ws = Workspace(name=f"ws-{uuid.uuid4().hex[:8]}")
        s.add(ws)
        await s.flush()
        return ws.id


async def test_typed_crud_and_relationships():
    ws_id = await _new_ws()
    async with tenant_session(ws_id) as s:
        agent_id, av_id = await _seed(s, ws_id)
    async with tenant_session(ws_id) as s:
        agent = (await s.execute(
            select(Agent).options(selectinload(Agent.versions)).where(Agent.id == agent_id)
        )).scalar_one()
        assert any(v.id == av_id for v in agent.versions), "agent → versions navigable"
        av = (await s.execute(
            select(AgentVersion).options(
                selectinload(AgentVersion.tool_bindings),
                selectinload(AgentVersion.skill_bindings),
                selectinload(AgentVersion.retrieval_bindings),
            ).where(AgentVersion.id == av_id)
        )).scalar_one()
        assert len(av.tool_bindings) == 1 and len(av.skill_bindings) == 1 and len(av.retrieval_bindings) == 1


async def test_mutable_column_update_allowed():
    ws_id = await _new_ws()
    async with tenant_session(ws_id) as s:
        _, av_id = await _seed(s, ws_id)
    async with tenant_session(ws_id) as s:  # publishable is mutable post-freeze (0002)
        av = (await s.execute(select(AgentVersion).where(AgentVersion.id == av_id))).scalar_one()
        av.publishable = True
    async with tenant_session(ws_id) as s:
        av = (await s.execute(select(AgentVersion).where(AgentVersion.id == av_id))).scalar_one()
        assert av.publishable is True


async def test_frozen_column_update_raises_loudly():
    ws_id = await _new_ws()
    async with tenant_session(ws_id) as s:
        _, av_id = await _seed(s, ws_id)
    with pytest.raises(Exception) as ei:  # 0002 BEFORE UPDATE trigger must RAISE, not silently drop
        async with tenant_session(ws_id) as s:
            av = (await s.execute(select(AgentVersion).where(AgentVersion.id == av_id))).scalar_one()
            av.system_prompt = "tampered"
    assert "immutable" in str(ei.value).lower()


# ---- feature 026 (T004): the ORM mirrors migration 0016 (data-model.md) ------------------------------------------
import api.models as _m  # noqa: E402

_026_MODELS = {
    "EvalSuite": ("eval_suites", {"id", "workspace_id", "agent_id", "name", "created_at", "updated_at"}),
    "EvalCase": ("eval_cases", {"id", "workspace_id", "suite_id", "input", "rubric", "required_fields", "expected",
                                "content_hash", "created_at", "updated_at"}),
    "EvalRun": ("eval_runs", {"id", "workspace_id", "agent_id", "suite_id", "version_id", "status", "verdict",
                              "reason_code", "reason", "baseline_run_id", "baseline_origin", "quality",
                              "quality_delta", "cost_usd", "cost_delta_pct", "latency_ms", "latency_delta_ms",
                              "dimensions", "compared_cases", "new_cases", "dropped_cases", "total_cases",
                              "pairwise_rate", "policy", "judge_version", "created_by", "started_at", "finished_at"}),
    "EvalResult": ("eval_results", {"id", "workspace_id", "eval_run_id", "case_id", "case_hash", "run_id",
                                    "passed_deterministic", "quality", "rubric_scores", "pairwise_winner",
                                    "judge_low_confidence", "failure_reason", "hard_failure", "candidate_output",
                                    "cost_usd", "cost_is_estimate", "latency_ms", "in_comparison"}),
    "EvalBaseline": ("eval_baselines", {"suite_id", "workspace_id", "eval_run_id", "frozen_by", "frozen_at"}),
    "AuditLog": ("audit_logs", {"id", "workspace_id", "actor", "action", "target_type", "target_id", "meta",
                                "created_at"}),
}
_NOT_NULL = {
    "eval_runs": {"status", "reason_code", "reason", "compared_cases", "total_cases", "policy", "created_by"},
    "eval_results": {"case_hash", "passed_deterministic", "hard_failure", "candidate_output", "in_comparison"},
    "eval_cases": {"input", "rubric", "content_hash"},
    "audit_logs": {"actor", "action", "meta"},
}
_NULLABLE = {"eval_runs": {"verdict", "baseline_run_id", "baseline_origin", "quality_delta", "judge_version"},
             "eval_results": {"run_id", "quality", "failure_reason"}, "eval_cases": {"expected"}}


def _fk_targets(table) -> set[tuple[tuple[str, ...], str]]:
    return {(tuple(c.name for c in fk.columns), fk.referred_table.name) for fk in table.foreign_key_constraints}


def test_026_agent_and_version_columns_mirrored():
    agents = _m.Base.metadata.tables["agents"]
    assert {"description", "draft", "status_before_archive"} <= set(agents.c.keys())
    assert agents.c.description.nullable is False
    assert agents.c.draft.nullable is True and agents.c.status_before_archive.nullable is True
    versions = _m.Base.metadata.tables["agent_versions"]
    assert "created_by" in versions.c and versions.c.created_by.nullable is True


@pytest.mark.parametrize("name", sorted(_026_MODELS))
def test_026_eval_and_audit_models_mirror_0016(name):
    model = getattr(_m, name, None)
    assert model is not None, f"api.models.{name} is missing"
    table_name, cols = _026_MODELS[name]
    t = model.__table__
    assert t.name == table_name
    assert cols <= set(t.c.keys()), f"{name} missing {sorted(cols - set(t.c.keys()))}"
    for c in _NOT_NULL.get(table_name, ()):
        assert t.c[c].nullable is False, f"{table_name}.{c} must be NOT NULL"
    for c in _NULLABLE.get(table_name, ()):
        assert t.c[c].nullable is True, f"{table_name}.{c} must be NULL-able"


def test_026_composite_fk_targets():
    tbl = _m.Base.metadata.tables
    assert (("workspace_id", "agent_id"), "agents") in _fk_targets(tbl["eval_suites"])
    assert (("workspace_id", "suite_id"), "eval_suites") in _fk_targets(tbl["eval_cases"])
    runs = _fk_targets(tbl["eval_runs"])
    assert (("version_id", "agent_id"), "agent_versions") in runs
    assert (("workspace_id", "suite_id"), "eval_suites") in runs
    assert (("workspace_id", "baseline_run_id"), "eval_runs") in runs
    assert (("workspace_id", "eval_run_id"), "eval_runs") in _fk_targets(tbl["eval_results"])
    base = _fk_targets(tbl["eval_baselines"])
    assert (("workspace_id", "suite_id"), "eval_suites") in base
    assert (("workspace_id", "eval_run_id"), "eval_runs") in base
    assert [c.name for c in tbl["eval_baselines"].primary_key.columns] == ["suite_id"]
