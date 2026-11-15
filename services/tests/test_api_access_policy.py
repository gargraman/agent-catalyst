"""Feature 013 — per-resource access-policy ENFORCEMENT at the API (US1 restricted / no-leak).

The headline P5 control: a `restricted` resource is invisible (list-omit + 404, NEVER 403) to a member who can't
USE it, visible to a matching member, always reachable by a workspace admin. The invoke AND eval-run paths deny
USE fail-closed (404) with no Run row and no provider call. Uses the RLS+ASGI integration harness.

Caller model: console role comes from the dev claim (`resolve_workspace`), teams from the member row
(`teams_for`). A restricted USE match needs role ∈ useRoles AND team ∈ useTeams.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from api.db import tenant_session
from api.models import Run
from tests._api_helpers import (
    client, dev_headers, seed_agent, seed_kb, seed_member, seed_version,
)

pytestmark = pytest.mark.integration

# restricted: USE = builder AND team SOC; MANAGE = publisher AND team SOC
RESTRICTED = {"visibility": "restricted", "useRoles": ["builder"], "useTeams": ["SOC"],
              "manageRoles": ["publisher"], "manageTeams": ["SOC"]}
EMPTY_RESTRICTED = {"visibility": "restricted"}        # names neither role nor team → grants no one (fail-closed)


async def _run_count(ws: str) -> int:
    async with tenant_session(ws) as s:
        return (await s.execute(select(func.count(Run.id)))).scalar_one()


# ---- US1: restricted resource USE (agents + knowledge_bases) -------------------------------------
async def test_restricted_agent_hidden_from_non_matching_member_visible_to_matching_and_admin():
    ws = await seed_workspace_with_members()
    aid = await seed_agent(ws, name="Restricted A", access_policy=RESTRICTED)
    async with client() as c:
        # non-matching member (builder, team IR) → list omits + GET 404 (never 403)
        out = dev_headers(workspace=ws, subject="outsider", claims={ws: "builder"})
        lst = (await c.get("/api/v1/agents", headers=out)).json()
        assert all(a["id"] != aid for a in lst["items"]), "restricted agent leaked into outsider's list"
        got = await c.get(f"/api/v1/agents/{aid}", headers=out)
        assert got.status_code == 404 and got.json()["error"]["code"] == "not_found"
        # matching member (builder, team SOC) → 200 and present in list
        match = dev_headers(workspace=ws, subject="insider", claims={ws: "builder"})
        assert (await c.get(f"/api/v1/agents/{aid}", headers=match)).status_code == 200
        lst_m = (await c.get("/api/v1/agents", headers=match)).json()
        assert any(a["id"] == aid for a in lst_m["items"])
        # admin → 200 always
        adm = dev_headers(workspace=ws, subject="adm", claims={ws: "admin"})
        assert (await c.get(f"/api/v1/agents/{aid}", headers=adm)).status_code == 200


async def test_empty_restricted_denies_everyone_but_admin():
    ws = await seed_workspace_with_members()
    aid = await seed_agent(ws, name="Empty Restricted", access_policy=EMPTY_RESTRICTED)
    async with client() as c:
        for subj, role in (("insider", "builder"), ("outsider", "builder")):
            got = await c.get(f"/api/v1/agents/{aid}", headers=dev_headers(workspace=ws, subject=subj, claims={ws: role}))
            assert got.status_code == 404, f"{subj} should be denied by empty restricted policy"
        adm = dev_headers(workspace=ws, subject="adm", claims={ws: "admin"})
        assert (await c.get(f"/api/v1/agents/{aid}", headers=adm)).status_code == 200


async def test_restricted_kb_hidden_from_non_matching_member():
    ws = await seed_workspace_with_members()
    kid = await seed_kb(ws, slug="restricted-kb", access_policy=RESTRICTED)
    async with client() as c:
        out = dev_headers(workspace=ws, subject="outsider", claims={ws: "builder"})
        lst = (await c.get("/api/v1/knowledge-bases", headers=out)).json()
        assert all(k["id"] != kid for k in lst["items"])
        assert (await c.get(f"/api/v1/knowledge-bases/{kid}", headers=out)).status_code == 404
        match = dev_headers(workspace=ws, subject="insider", claims={ws: "builder"})
        assert (await c.get(f"/api/v1/knowledge-bases/{kid}", headers=match)).status_code == 200


# ---- US1: invoke + eval-run deny USE fail-closed (no run, no provider call) ----------------------
async def test_invoke_denied_fail_closed_no_run_written():
    ws = await seed_workspace_with_members()
    aid = await seed_agent(ws, name="Restricted Inv", access_policy=RESTRICTED)
    await seed_version(ws, aid)          # fully invocable — proves the 404 is the policy gate, not a missing version
    async with client() as c:
        out = dev_headers(workspace=ws, subject="outsider", claims={ws: "builder"})
        r = await c.post(f"/api/v1/agents/{aid}/invoke", headers=out, json={"inputs": {"message": "hi"}})
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert await _run_count(ws) == 0, "a denied invoke must write no Run row (no provider call)"


async def test_eval_run_denied_fail_closed_no_run_written():
    ws = await seed_workspace_with_members()
    aid = await seed_agent(ws, name="Restricted Eval", access_policy=RESTRICTED)
    vid = await seed_version(ws, aid)
    body = {
        "version_id": vid,
        "cases": [{"id": "c1", "input": "hi", "rubric": [{"name": "quality", "weight": 1.0}]}],
        "baseline": {"quality": 0.9, "cost_usd": 0.001, "latency_ms": 100, "per_case": {}},
        "policy": {"min_cases": 1, "quality_threshold": 0.5, "efficiency_budget_pct": 50,
                   "allow_low_confidence_override": False},
    }
    async with client() as c:
        out = dev_headers(workspace=ws, subject="outsider", claims={ws: "builder"})
        r = await c.post(f"/api/v1/agents/{aid}/eval-runs", headers=out, json=body)
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert await _run_count(ws) == 0, "a denied eval-run must write no Run row (no provider call)"


PRIVATE = {"visibility": "private"}      # owner is stamped on create (no owner named by the client)
_AGENT_BODY = {"provider": "openai", "model": "gpt-5-mini", "io_contract": {"input_schema": {"type": "object"}}}
_KB_BODY = {"embedding_model": "text-embedding-3-small", "embedding_dim": 1024}   # feature 024: deployment dim 1024


# ---- US2: private resource reachable only by owner + admin ----------------------------------------
async def test_private_agent_owner_only_plus_admin():
    ws = await seed_workspace_with_members()
    async with client() as c:
        # "insider" creates a PRIVATE agent → the server stamps insider as the owner (FR-012)
        creator = dev_headers(workspace=ws, subject="insider", claims={ws: "builder"})
        created = await c.post("/api/v1/agents", headers=creator,
                               json={"name": "Private A", "access_policy": PRIVATE, **_AGENT_BODY})
        assert created.status_code == 201, created.text
        aid = created.json()["id"]
        assert created.json()["access_policy"]["owner"] == "insider", "create must stamp the owner subject"
        # owner → 200; a different non-admin member → 404 (no leak); admin → 200
        assert (await c.get(f"/api/v1/agents/{aid}", headers=creator)).status_code == 200
        other = dev_headers(workspace=ws, subject="outsider", claims={ws: "builder"})
        assert (await c.get(f"/api/v1/agents/{aid}", headers=other)).status_code == 404
        adm = dev_headers(workspace=ws, subject="adm", claims={ws: "admin"})
        assert (await c.get(f"/api/v1/agents/{aid}", headers=adm)).status_code == 200


async def test_private_kb_owner_only_plus_admin():
    ws = await seed_workspace_with_members()
    async with client() as c:
        creator = dev_headers(workspace=ws, subject="insider", claims={ws: "builder"})
        created = await c.post("/api/v1/knowledge-bases", headers=creator,
                               json={"slug": "private-kb", "name": "Private KB", "access_policy": PRIVATE, **_KB_BODY})
        assert created.status_code == 201, created.text
        kid = created.json()["id"]
        assert created.json()["access_policy"]["owner"] == "insider"
        assert (await c.get(f"/api/v1/knowledge-bases/{kid}", headers=creator)).status_code == 200
        other = dev_headers(workspace=ws, subject="outsider", claims={ws: "builder"})
        assert (await c.get(f"/api/v1/knowledge-bases/{kid}", headers=other)).status_code == 404
        adm = dev_headers(workspace=ws, subject="adm", claims={ws: "admin"})
        assert (await c.get(f"/api/v1/knowledge-bases/{kid}", headers=adm)).status_code == 200


async def test_edit_agent_to_private_stamps_owner():
    """Editing a workspace agent to `private` without naming an owner must stamp the editor (never ownerless-locked)."""
    ws = await seed_workspace_with_members()
    async with client() as c:
        editor = dev_headers(workspace=ws, subject="insider", claims={ws: "builder"})
        created = await c.post("/api/v1/agents", headers=editor, json={"name": "WS A", **_AGENT_BODY})
        assert created.status_code == 201, created.text
        aid = created.json()["id"]
        etag = created.headers["ETag"]
        patched = await c.patch(f"/api/v1/agents/{aid}", headers={**editor, "If-Match": etag},
                                json={"access_policy": PRIVATE})
        assert patched.status_code == 200, patched.text
        assert patched.json()["access_policy"]["owner"] == "insider", "edit-to-private must stamp the editor as owner"
        # now a different member can't see it; the owner still can
        other = dev_headers(workspace=ws, subject="outsider", claims={ws: "builder"})
        assert (await c.get(f"/api/v1/agents/{aid}", headers=other)).status_code == 404
        assert (await c.get(f"/api/v1/agents/{aid}", headers=editor)).status_code == 200


# ---- US3: manage gated separately from use --------------------------------------------------------
# Managers must also be USERS (ensure_manage is 404-then-403: a caller who can't USE can't MANAGE, gets 404).
# So USE grants both builder+publisher; MANAGE is the narrower publisher-only slice — on the same SOC team.
RESTRICTED_MANAGE = {"visibility": "restricted", "useRoles": ["builder", "publisher"], "useTeams": ["SOC"],
                     "manageRoles": ["publisher"], "manageTeams": ["SOC"]}


async def test_manage_gated_separately_from_use_agents():
    """USE=builder|publisher+SOC, MANAGE=publisher+SOC. A usable-but-not-manageable member → 403; a
    non-usable member → 404 (no leak); a matching manager + admin → 200."""
    ws = await seed_workspace_with_members()
    await seed_member(ws, subject="mgr", role="publisher", teams=["SOC"])     # can USE and MANAGE
    aid = await seed_agent(ws, name="R-manage", access_policy=RESTRICTED_MANAGE)
    async with client() as c:
        # insider builder+SOC: can USE (GET 200) but role builder ∉ manageRoles → PATCH 403
        insider = dev_headers(workspace=ws, subject="insider", claims={ws: "builder"})
        etag = (await c.get(f"/api/v1/agents/{aid}", headers=insider)).headers["ETag"]
        r = await c.patch(f"/api/v1/agents/{aid}", headers={**insider, "If-Match": etag}, json={"name": "x"})
        assert r.status_code == 403 and r.json()["error"]["code"] == "access.denied", r.text
        # outsider builder+IR: can't USE → PATCH 404 (indistinguishable from absent, never 403)
        outsider = dev_headers(workspace=ws, subject="outsider", claims={ws: "builder"})
        r2 = await c.patch(f"/api/v1/agents/{aid}", headers={**outsider, "If-Match": etag}, json={"name": "x"})
        assert r2.status_code == 404, r2.text
        # publisher+SOC: can MANAGE → 200
        mgr = dev_headers(workspace=ws, subject="mgr", claims={ws: "publisher"})
        etag_m = (await c.get(f"/api/v1/agents/{aid}", headers=mgr)).headers["ETag"]
        r3 = await c.patch(f"/api/v1/agents/{aid}", headers={**mgr, "If-Match": etag_m}, json={"name": "y"})
        assert r3.status_code == 200, r3.text
        # admin always manages
        adm = dev_headers(workspace=ws, subject="adm", claims={ws: "admin"})
        etag_a = (await c.get(f"/api/v1/agents/{aid}", headers=adm)).headers["ETag"]
        r4 = await c.patch(f"/api/v1/agents/{aid}", headers={**adm, "If-Match": etag_a}, json={"name": "z"})
        assert r4.status_code == 200, r4.text


async def test_manage_rbac_denied_role_is_403_before_policy_agents():
    """A viewer (no `agent.edit`) PATCHing a restricted agent gets 403 from RBAC BEFORE the policy runs — the
    SAME 403 it would get for a non-existent agent, so RBAC role-gating leaks no existence (FR-015)."""
    ws = await seed_workspace_with_members()
    aid = await seed_agent(ws, name="R-rbac", access_policy=RESTRICTED)
    async with client() as c:
        viewer = dev_headers(workspace=ws, subject="v", claims={ws: "viewer"})
        r = await c.patch(f"/api/v1/agents/{aid}", headers={**viewer, "If-Match": "1"}, json={"name": "x"})
        assert r.status_code == 403 and r.json()["error"]["code"] == "access.denied", r.text


async def test_manage_gated_separately_from_use_kb():
    ws = await seed_workspace_with_members()
    await seed_member(ws, subject="mgr", role="publisher", teams=["SOC"])
    kid = await seed_kb(ws, slug="r-manage-kb", access_policy=RESTRICTED_MANAGE)
    async with client() as c:
        # usable-not-manageable → 403
        insider = dev_headers(workspace=ws, subject="insider", claims={ws: "builder"})
        etag = (await c.get(f"/api/v1/knowledge-bases/{kid}", headers=insider)).headers["ETag"]
        r = await c.patch(f"/api/v1/knowledge-bases/{kid}", headers={**insider, "If-Match": etag},
                          json={"name": "x"})
        assert r.status_code == 403 and r.json()["error"]["code"] == "access.denied", r.text
        # non-usable → 404
        outsider = dev_headers(workspace=ws, subject="outsider", claims={ws: "builder"})
        r2 = await c.patch(f"/api/v1/knowledge-bases/{kid}", headers={**outsider, "If-Match": etag},
                           json={"name": "x"})
        assert r2.status_code == 404, r2.text


async def test_publish_manage_gated_agents():
    """Publish (agent.publish RBAC) is a MANAGE op. A publisher who CAN use but whose team is not a manage-team
    → 403 (usable-not-manageable); admin → 200. (USE grants publisher+SOC; MANAGE is publisher+IR only.)"""
    ws = await seed_workspace_with_members()
    pub_policy = {"visibility": "restricted", "useRoles": ["publisher"], "useTeams": ["SOC"],
                  "manageRoles": ["publisher"], "manageTeams": ["IR"]}
    await seed_member(ws, subject="pub-soc", role="publisher", teams=["SOC"])   # can USE, cannot MANAGE (team SOC∉IR)
    aid = await seed_agent(ws, name="R-pub", access_policy=pub_policy)
    vid = await seed_version(ws, aid)
    async with client() as c:
        pub = dev_headers(workspace=ws, subject="pub-soc", claims={ws: "publisher"})
        r = await c.post(f"/api/v1/agents/{aid}/publish", headers=pub, json={"version_id": vid})
        assert r.status_code == 403 and r.json()["error"]["code"] == "access.denied", r.text
        adm = dev_headers(workspace=ws, subject="adm", claims={ws: "admin"})
        r2 = await c.post(f"/api/v1/agents/{aid}/publish", headers=adm, json={"version_id": vid})
        assert r2.status_code == 200, r2.text


# ---- shared seeding ------------------------------------------------------------------------------
async def seed_workspace_with_members() -> str:
    """A workspace with an insider (builder, team SOC — USE-matches) and an outsider (builder, team IR — denied)."""
    from tests._api_helpers import seed_workspace
    ws = await seed_workspace()
    await seed_member(ws, subject="insider", role="builder", teams=["SOC"])
    await seed_member(ws, subject="outsider", role="builder", teams=["IR"])
    return ws
