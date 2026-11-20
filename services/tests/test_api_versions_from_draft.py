"""Feature 026 US1 (T017, research D2) — POST /agents/{id}/versions builds the version FROM THE RESOLVED DRAFT, writes
tool + skill + KB bindings in the same transaction (G3 fix — tool/skill bindings used to be silently dropped), and
records `created_by`. A dangling bound id → 404 `not_found` with `detail.binding` and no version row left behind.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from api.db import tenant_session
from api.models import AgentVersion
from tests._api_helpers import client, dev_headers, seed_kb, seed_skill, seed_tool, seed_workspace

pytestmark = pytest.mark.integration

IO = {"input_schema": {"type": "object", "properties": {"message": {"type": "string"}}}, "output_format": "text"}


def _draft(**over) -> dict:
    d = {"provider": "openai", "model": "gpt-5-mini", "model_params": {}, "system_prompt": "You are helpful.",
         "io_contract": IO, "kb_ids": [], "tool_ids": [], "skill_ids": []}
    d.update(over)
    return d


async def _agent_with_draft(c, ws, draft: dict | None) -> dict:
    r = await c.post("/api/v1/agents", json={"name": "A", "provider": "openai", "model": "gpt-5-mini",
                                             "system_prompt": "You are helpful.", "io_contract": IO},
                     headers=dev_headers(workspace=ws))
    assert r.status_code == 201, r.text
    a = r.json()
    if draft is not None:
        p = await c.patch(f"/api/v1/agents/{a['id']}", json={"draft": draft},
                          headers=dev_headers(workspace=ws, if_match=r.headers["ETag"]))
        assert p.status_code == 200, p.text
    return a


async def _version_row(ws, vid) -> AgentVersion:
    async with tenant_session(ws) as s:
        return (await s.execute(select(AgentVersion).where(AgentVersion.id == uuid.UUID(vid)).options(
            selectinload(AgentVersion.tool_bindings), selectinload(AgentVersion.skill_bindings),
            selectinload(AgentVersion.retrieval_bindings)))).scalar_one()


async def _version_count(ws, aid) -> int:
    async with tenant_session(ws) as s:
        return await s.scalar(select(func.count()).select_from(AgentVersion).where(AgentVersion.agent_id == uuid.UUID(aid)))


async def test_version_is_built_from_the_draft_with_creator():
    ws = await seed_workspace()
    async with client() as c:
        a = await _agent_with_draft(c, ws, _draft(system_prompt="Draft prompt.", model_params={"temperature": 0.5}))
        r = await c.post(f"/api/v1/agents/{a['id']}/versions", headers=dev_headers(workspace=ws, subject="alice"))
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["created_by"] == "alice" and out["is_active"] is False and out["label"] == "v2"
    v = await _version_row(ws, out["id"])
    assert v.system_prompt == "Draft prompt." and v.created_by == "alice"
    assert v.model_config_["params"] == {"temperature": 0.5} and v.io_contract == IO
    assert v.profile_snapshot.get("model") == "gpt-5-mini"


async def test_create_agent_stamps_creator_on_the_initial_v1():
    """Found in T029 live check L3: the v1 that POST /agents creates rendered "unknown creator" because only the
    from-draft path stamped `created_by`. Every version — including the initial one — records its creator (D2)."""
    ws = await seed_workspace()
    async with client() as c:
        r = await c.post("/api/v1/agents", json={"name": "A", "provider": "openai", "model": "gpt-5-mini",
                                                 "system_prompt": "You are helpful.", "io_contract": IO},
                         headers=dev_headers(workspace=ws, subject="bob"))
    assert r.status_code == 201, r.text
    (v1,) = r.json()["versions"]
    assert v1["label"] == "v1" and v1["created_by"] == "bob"
    assert (await _version_row(ws, v1["id"])).created_by == "bob"


async def test_null_draft_clones_the_active_version():
    ws = await seed_workspace()
    async with client() as c:
        a = await _agent_with_draft(c, ws, None)
        r = await c.post(f"/api/v1/agents/{a['id']}/versions", headers=dev_headers(workspace=ws))
    assert r.status_code == 201
    assert r.json()["snapshot_hash"] == a["versions"][0]["snapshot_hash"]      # identical content ⇒ identical hash


async def test_tool_skill_and_kb_bindings_are_all_kept():
    """G3 regression: a draft with tool_ids=[t], skill_ids=[s], kb_ids=[k] yields a version with all three."""
    ws = await seed_workspace()
    t, s, k = await seed_tool(ws), await seed_skill(ws), await seed_kb(ws)
    async with client() as c:
        a = await _agent_with_draft(c, ws, _draft(tool_ids=[t], skill_ids=[s], kb_ids=[k]))
        r = await c.post(f"/api/v1/agents/{a['id']}/versions", headers=dev_headers(workspace=ws))
    assert r.status_code == 201, r.text
    v = await _version_row(ws, r.json()["id"])
    assert [str(b.tool_id) for b in v.tool_bindings] == [t] and v.tool_bindings[0].kind == "http"
    assert [str(b.skill_id) for b in v.skill_bindings] == [s]
    assert [str(b.kb_id) for b in v.retrieval_bindings] == [k]
    assert [b["kb_id"] for b in r.json()["retrieval_bindings"]] == [k]


async def test_kb_binding_settings_carry_over_from_the_active_version():
    ws = await seed_workspace()
    k = await seed_kb(ws)
    cfg = {"kb_id": k, "injection": "tool", "retrieval_config": {"top_k": 3}}
    async with client() as c:
        a = await _agent_with_draft(c, ws, None)
        v2 = await c.post(f"/api/v1/agents/{a['id']}/versions", json={"retrieval_bindings": [cfg]},
                          headers=dev_headers(workspace=ws))
        assert v2.status_code == 201, v2.text
        etag = (await c.get(f"/api/v1/agents/{a['id']}", headers=dev_headers(workspace=ws))).headers["ETag"]
        rp = await c.patch(f"/api/v1/agents/{a['id']}", json={"active_version_id": v2.json()["id"]},
                           headers=dev_headers(workspace=ws, claims={ws: "publisher"}, if_match=etag))
        assert rp.status_code == 200 and rp.json()["draft"]["kb_ids"] == [k]
        p = await c.patch(f"/api/v1/agents/{a['id']}", json={"draft": _draft(kb_ids=[k], system_prompt="new")},
                          headers=dev_headers(workspace=ws, if_match=rp.headers["ETag"]))
        assert p.status_code == 200
        v3 = await c.post(f"/api/v1/agents/{a['id']}/versions", headers=dev_headers(workspace=ws))
    (b,) = v3.json()["retrieval_bindings"]
    assert b["injection"] == "tool" and b["retrieval_config"]["top_k"] == 3


@pytest.mark.parametrize("kind", ["knowledge_base", "tool", "skill"])
async def test_missing_binding_is_404_with_detail_and_leaves_no_version(kind):
    ws = await seed_workspace()
    ghost = str(uuid.uuid4())
    field = {"knowledge_base": "kb_ids", "tool": "tool_ids", "skill": "skill_ids"}[kind]
    async with client() as c:
        a = await _agent_with_draft(c, ws, _draft(**{field: [ghost]}))
        r = await c.post(f"/api/v1/agents/{a['id']}/versions", headers=dev_headers(workspace=ws))
    assert r.status_code == 404
    err = r.json()["error"]
    assert err["code"] == "not_found" and err["detail"] == {"binding": kind, "id": ghost}
    assert await _version_count(ws, a["id"]) == 1


async def test_binding_from_another_workspace_is_404_binding():
    ws, other = await seed_workspace(), await seed_workspace()
    foreign_tool = await seed_tool(other)
    async with client() as c:
        a = await _agent_with_draft(c, ws, _draft(tool_ids=[foreign_tool]))
        r = await c.post(f"/api/v1/agents/{a['id']}/versions", headers=dev_headers(workspace=ws))
    assert r.status_code == 404 and r.json()["error"]["detail"] == {"binding": "tool", "id": foreign_tool}


async def test_versions_listed_newest_first_and_draft_clean_after_repoint():
    ws = await seed_workspace()
    async with client() as c:
        a = await _agent_with_draft(c, ws, _draft(system_prompt="Next."))
        v2 = (await c.post(f"/api/v1/agents/{a['id']}/versions", headers=dev_headers(workspace=ws))).json()
        got = await c.get(f"/api/v1/agents/{a['id']}", headers=dev_headers(workspace=ws))
        assert [v["label"] for v in got.json()["versions"]] == ["v2", "v1"]
        assert got.json()["draft_dirty"] is True
        rp = await c.patch(f"/api/v1/agents/{a['id']}", json={"active_version_id": v2["id"]},
                           headers=dev_headers(workspace=ws, claims={ws: "publisher"}, if_match=got.headers["ETag"]))
    assert rp.status_code == 200 and rp.json()["draft_dirty"] is False
    assert [v["is_active"] for v in rp.json()["versions"]] == [True, False]


async def test_viewer_cannot_create_a_version():
    ws = await seed_workspace()
    async with client() as c:
        a = await _agent_with_draft(c, ws, None)
        r = await c.post(f"/api/v1/agents/{a['id']}/versions", headers=dev_headers(workspace=ws, claims={ws: "viewer"}))
    assert r.status_code == 403
