"""Feature 026 US1 (T015 + T016) — draft + description persistence under the xmin ETag, enriched AgentOut/VersionOut,
archive/restore lifecycle, and audited re-point/archive/restore. Integration (real Postgres + RLS).

Owner decision (2026-11-16): a stale If-Match stays **409 `conflict.stale`** (the existing contract), not `conflict`.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from api.db import tenant_session
from api.models import AuditLog
from tests._api_helpers import client, dev_headers, seed_kb, seed_workspace

pytestmark = pytest.mark.integration

IO = {"input_schema": {"type": "object", "properties": {"message": {"type": "string"}}}, "output_format": "text"}


def _draft(**over) -> dict:
    d = {"provider": "openai", "model": "gpt-5-mini", "model_params": {}, "system_prompt": "You are helpful.",
         "io_contract": IO, "kb_ids": [], "tool_ids": [], "skill_ids": []}
    d.update(over)
    return d


async def _create(c, ws, **over):
    body = {"name": "Draft Agent", "provider": "openai", "model": "gpt-5-mini", "system_prompt": "You are helpful.",
            "io_contract": IO, **over}
    r = await c.post("/api/v1/agents", json=body, headers=dev_headers(workspace=ws))
    assert r.status_code == 201, r.text
    return r.json(), r.headers["ETag"]


async def _patch(c, ws, aid, etag, body, **hdr):
    return await c.patch(f"/api/v1/agents/{aid}", json=body, headers=dev_headers(workspace=ws, if_match=etag, **hdr))


async def _audit(ws, action=None) -> list[AuditLog]:
    async with tenant_session(ws) as s:
        stmt = select(AuditLog).order_by(AuditLog.created_at)
        if action:
            stmt = stmt.where(AuditLog.action == action)
        return list((await s.scalars(stmt)).all())


# --- T015: GET enrichment ----------------------------------------------------------------------------------------------
async def test_get_returns_enriched_agent_with_resolved_draft_and_etag():
    ws = await seed_workspace()
    async with client() as c:
        created, _ = await _create(c, ws, system_prompt="Original prompt.")
        r = await c.get(f"/api/v1/agents/{created['id']}", headers=dev_headers(workspace=ws))
    assert r.status_code == 200 and r.headers.get("ETag")
    a = r.json()
    assert a["description"] == "" and a["provider"] == "openai" and a["model"] == "gpt-5-mini"
    assert a["draft"]["system_prompt"] == "Original prompt." and a["draft"]["io_contract"] == IO
    assert a["draft"]["kb_ids"] == [] and a["draft_dirty"] is False and a["restore_status"] is None
    (v,) = a["versions"]
    assert v["is_active"] is True and "created_by" in v


async def test_list_items_carry_the_enrichment():
    ws = await seed_workspace()
    async with client() as c:
        await _create(c, ws)
        r = await c.get("/api/v1/agents", headers=dev_headers(workspace=ws))
    item = r.json()["items"][0]
    assert {"description", "provider", "model", "draft", "draft_dirty", "restore_status"} <= set(item)


# --- T015: PATCH description / draft -----------------------------------------------------------------------------------
async def test_patch_description_persists_and_rotates_etag():
    ws = await seed_workspace()
    async with client() as c:
        a, etag = await _create(c, ws)
        r = await _patch(c, ws, a["id"], etag, {"description": "x" * 2000})
        assert r.status_code == 200, r.text
        assert r.json()["description"] == "x" * 2000 and r.headers["ETag"] != etag
        again = await c.get(f"/api/v1/agents/{a['id']}", headers=dev_headers(workspace=ws))
    assert again.json()["description"] == "x" * 2000


async def test_patch_description_over_2000_is_422():
    ws = await seed_workspace()
    async with client() as c:
        a, etag = await _create(c, ws)
        r = await _patch(c, ws, a["id"], etag, {"description": "x" * 2001})
    assert r.status_code == 422


async def test_patch_draft_full_replace_and_dirty_tracking():
    ws = await seed_workspace()
    async with client() as c:
        a, etag = await _create(c, ws)
        r = await _patch(c, ws, a["id"], etag, {"draft": _draft(system_prompt="Changed.")})
        assert r.status_code == 200, r.text
        assert r.json()["draft"]["system_prompt"] == "Changed." and r.json()["draft_dirty"] is True
        r2 = await _patch(c, ws, a["id"], r.headers["ETag"], {"draft": _draft()})     # revert to identical content
        assert r2.status_code == 200 and r2.json()["draft_dirty"] is False
        got = await c.get(f"/api/v1/agents/{a['id']}", headers=dev_headers(workspace=ws))
    assert got.json()["draft"]["system_prompt"] == "You are helpful."


async def test_stale_if_match_is_409_conflict_stale():
    ws = await seed_workspace()
    async with client() as c:
        a, etag = await _create(c, ws)
        ok = await _patch(c, ws, a["id"], etag, {"description": "tab one"})
        assert ok.status_code == 200
        stale = await _patch(c, ws, a["id"], etag, {"description": "tab two"})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "conflict.stale"


async def test_missing_if_match_keeps_existing_refusal():
    ws = await seed_workspace()
    async with client() as c:
        a, _ = await _create(c, ws)
        r = await c.patch(f"/api/v1/agents/{a['id']}", json={"description": "x"}, headers=dev_headers(workspace=ws))
    assert r.status_code == 400 and r.json()["error"]["code"] == "input.invalid"


@pytest.mark.parametrize("bad", [
    {"provider": "nope"},
    {"model": "gpt-does-not-exist"},
    {"io_contract": {"output_format": "text"}},
    {"model_params": {"temperature": 7}},
    {"model_params": {"api_key": "sk-x"}},
])
async def test_invalid_draft_is_422(bad):
    ws = await seed_workspace()
    async with client() as c:
        a, etag = await _create(c, ws)
        r = await _patch(c, ws, a["id"], etag, {"draft": _draft(**bad)})
    assert r.status_code == 422


async def test_draft_with_nonexistent_kb_id_is_accepted_at_save():
    ws = await seed_workspace()
    ghost = str(uuid.uuid4())
    async with client() as c:
        a, etag = await _create(c, ws)
        r = await _patch(c, ws, a["id"], etag, {"draft": _draft(kb_ids=[ghost])})
    assert r.status_code == 200 and r.json()["draft"]["kb_ids"] == [ghost]


async def test_viewer_patch_is_403_and_other_workspace_is_404():
    ws, other = await seed_workspace(), await seed_workspace()
    async with client() as c:
        a, etag = await _create(c, ws)
        viewer = await c.patch(f"/api/v1/agents/{a['id']}", json={"description": "x"},
                               headers=dev_headers(workspace=ws, claims={ws: "viewer"}, if_match=etag))
        foreign = await _patch(c, other, a["id"], etag, {"description": "x"})
    assert viewer.status_code == 403
    assert foreign.status_code == 404


# --- T016: archive / restore -------------------------------------------------------------------------------------------
async def _to_status(c, ws, aid, etag, status):
    r = await _patch(c, ws, aid, etag, {"status": status})
    assert r.status_code == 200, r.text
    return r.json(), r.headers["ETag"]


@pytest.mark.parametrize("prior", ["published", "draft", "eval_failing"])
async def test_archive_remembers_prior_status_and_restore_puts_it_back(prior):
    ws = await seed_workspace()
    async with client() as c:
        a, etag = await _create(c, ws)
        if prior != "draft":
            a, etag = await _to_status(c, ws, a["id"], etag, prior)
        arch, etag = await _to_status(c, ws, a["id"], etag, "archived")
        assert arch["status"] == "archived" and arch["restore_status"] == prior
        back, _ = await _to_status(c, ws, a["id"], etag, prior)
    assert back["status"] == prior and back["restore_status"] is None


async def test_other_transition_out_of_archived_is_400():
    ws = await seed_workspace()
    async with client() as c:
        a, etag = await _create(c, ws)
        a, etag = await _to_status(c, ws, a["id"], etag, "published")
        a, etag = await _to_status(c, ws, a["id"], etag, "archived")
        r = await _patch(c, ws, a["id"], etag, {"status": "draft"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "input.invalid"


# --- T016: audit -------------------------------------------------------------------------------------------------------
async def test_repoint_writes_agent_repoint_audit_row():
    ws = await seed_workspace()
    async with client() as c:
        a, etag = await _create(c, ws)
        v1 = a["active_version_id"]
        v2 = (await c.post(f"/api/v1/agents/{a['id']}/versions", headers=dev_headers(workspace=ws))).json()["id"]
        etag = (await c.get(f"/api/v1/agents/{a['id']}", headers=dev_headers(workspace=ws))).headers["ETag"]
        r = await _patch(c, ws, a["id"], etag, {"active_version_id": v2})
        assert r.status_code == 200, r.text
    (row,) = await _audit(ws, "agent.repoint")
    assert row.actor == "u1" and row.target_type == "agent" and str(row.target_id) == a["id"]
    assert row.meta == {"from_version_id": v1, "to_version_id": v2}


async def test_archive_and_restore_write_audit_rows():
    ws = await seed_workspace()
    async with client() as c:
        a, etag = await _create(c, ws)
        _, etag = await _to_status(c, ws, a["id"], etag, "archived")
        await _to_status(c, ws, a["id"], etag, "draft")
    assert [r.action for r in await _audit(ws)] == ["agent.archive", "agent.restore"]


async def test_failed_patch_writes_no_audit_row():
    ws = await seed_workspace()
    async with client() as c:
        a, etag = await _create(c, ws)
        v2 = (await c.post(f"/api/v1/agents/{a['id']}/versions", headers=dev_headers(workspace=ws))).json()["id"]
        rotated = await _patch(c, ws, a["id"], etag, {"description": "rotate"})    # rotates the ETag; not audited
        stale = await _patch(c, ws, a["id"], etag, {"active_version_id": v2})      # old ETag → genuinely stale
        assert stale.status_code == 409
        bad = await _patch(c, ws, a["id"], rotated.headers["ETag"],
                           {"status": "archived", "active_version_id": str(uuid.uuid4())})   # foreign version → 404
        assert bad.status_code == 404
    assert await _audit(ws) == []


async def test_noop_description_edit_writes_no_audit_row():
    ws = await seed_workspace()
    async with client() as c:
        a, etag = await _create(c, ws)
        await _patch(c, ws, a["id"], etag, {"description": "just text"})
    assert await _audit(ws) == []


# --- P5 / 024 H2: draft binding ids the viewer may not USE are masked, and a save can't strip them -------------------
async def test_restricted_draft_binding_is_masked_and_preserved_across_a_save():
    ws = await seed_workspace()
    secret = await seed_kb(ws, slug="secret-kb", access_policy={"visibility": "restricted",
                                                               "useRoles": ["admin"], "manageRoles": ["admin"]})
    open_kb = await seed_kb(ws, slug="open-kb")
    adm = {"claims": {ws: "admin"}, "subject": "adm"}
    async with client() as c:
        a, etag = await _create(c, ws)
        r = await c.patch(f"/api/v1/agents/{a['id']}", json={"draft": _draft(kb_ids=[secret, open_kb])},
                          headers=dev_headers(workspace=ws, if_match=etag, **adm))
        assert r.status_code == 200, r.text
        as_builder = await c.get(f"/api/v1/agents/{a['id']}", headers=dev_headers(workspace=ws))
        assert secret not in as_builder.text
        d = as_builder.json()["draft"]
        assert d["kb_ids"] == [open_kb] and d["restricted_bindings"] == 1
        # builder saves the draft it SAW (without the secret id) → the secret binding survives server-side
        saved = await _patch(c, ws, a["id"], as_builder.headers["ETag"],
                             {"draft": {**{k: v for k, v in d.items() if k != "restricted_bindings"},
                                        "system_prompt": "builder edit"}})
        assert saved.status_code == 200, saved.text
        assert secret not in saved.text
        as_admin = await c.get(f"/api/v1/agents/{a['id']}", headers=dev_headers(workspace=ws, **adm))
    assert sorted(as_admin.json()["draft"]["kb_ids"]) == sorted([secret, open_kb])
    assert as_admin.json()["draft"]["system_prompt"] == "builder edit"
