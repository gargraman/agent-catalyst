"""US3 — agent CRUD + versioning, idempotency, optimistic concurrency (M4, feature 005).

Unit: the in-flight idempotency guard (FR-014). Integration (compose DB): create/get/list/PATCH/version,
ETag (xmin) If-Match concurrency (FR-015), immutable versions.
"""
from __future__ import annotations

import uuid

import pytest

import uuid as _uuid

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from api import deps
from api.db import tenant_session
from api.deps import idempotency_guard
from api.errors import IdempotencyInProgress
from api.models import AgentVersion, RetrievalBinding
from runtime.providers import gateway
from tests._api_helpers import client, dev_headers, parse_sse, seed_workspace
from tests._invoke_helpers import make_gateway, stop_ev, text_ev, usage_ev


# ---- unit: idempotency in-flight guard (FR-014/D8) -----------------------------------------------
async def test_idempotency_guard_rejects_concurrent_inflight():
    async with idempotency_guard("ws_1", "key-1"):
        with pytest.raises(IdempotencyInProgress):
            async with idempotency_guard("ws_1", "key-1"):       # same key still in flight → 409
                pass
    # released after the first completes → re-acquirable
    async with idempotency_guard("ws_1", "key-1"):
        pass


# ---- integration: CRUD + versioning --------------------------------------------------------------
_CREATE = {"name": "Triage Bot", "provider": "openai", "model": "gpt-5-mini",
           "system_prompt": "You are a triage bot.",
           "io_contract": {"input_schema": {"type": "object", "properties": {"message": {"type": "string"}},
                                            "required": ["message"]}, "output_format": "text", "output_schema": None}}


@pytest.mark.integration
async def test_create_scopes_to_workspace_and_returns_typed_agent():
    ws = await seed_workspace()
    async with client() as c:
        r = await c.post("/api/v1/agents", headers=dev_headers(workspace=ws), json=_CREATE)
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Triage Bot" and body["workspace_id"] == ws and body["status"] == "draft"
    assert len(body["versions"]) == 1 and body["active_version_id"] == body["versions"][0]["id"]
    assert r.headers.get("ETag")                                  # xmin ETag surfaced


@pytest.mark.integration
async def test_get_and_list():
    ws = await seed_workspace()
    async with client() as c:
        created = (await c.post("/api/v1/agents", headers=dev_headers(workspace=ws), json=_CREATE)).json()
        g = await c.get(f"/api/v1/agents/{created['id']}", headers=dev_headers(workspace=ws))
        lst = await c.get("/api/v1/agents", headers=dev_headers(workspace=ws))
    assert g.status_code == 200 and g.headers.get("ETag")
    page = lst.json()
    assert "items" in page and "next_cursor" in page
    assert any(a["id"] == created["id"] for a in page["items"])


@pytest.mark.integration
async def test_post_version_is_immutable_snapshot():
    ws = await seed_workspace()
    async with client() as c:
        created = (await c.post("/api/v1/agents", headers=dev_headers(workspace=ws), json=_CREATE)).json()
        r = await c.post(f"/api/v1/agents/{created['id']}/versions",
                         headers=dev_headers(workspace=ws), json={"label": "v2"})
        g = (await c.get(f"/api/v1/agents/{created['id']}", headers=dev_headers(workspace=ws))).json()
    assert r.status_code == 201
    v = r.json()
    assert v["label"] == "v2" and v["snapshot_hash"]
    assert len(g["versions"]) == 2


@pytest.mark.integration
async def test_patch_if_match_optimistic_concurrency():
    ws = await seed_workspace()
    async with client() as c:
        created = await c.post("/api/v1/agents", headers=dev_headers(workspace=ws), json=_CREATE)
        etag1 = created.headers["ETag"]
        aid = created.json()["id"]
        # correct If-Match → 200
        ok = await c.patch(f"/api/v1/agents/{aid}", headers=dev_headers(workspace=ws, if_match=etag1),
                           json={"name": "Renamed"})
        assert ok.status_code == 200 and ok.json()["name"] == "Renamed"
        # reuse the now-stale etag1 → 409 conflict.stale, row unchanged
        stale = await c.patch(f"/api/v1/agents/{aid}", headers=dev_headers(workspace=ws, if_match=etag1),
                              json={"name": "Again"})
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "conflict.stale"
        g = (await c.get(f"/api/v1/agents/{aid}", headers=dev_headers(workspace=ws))).json()
    assert g["name"] == "Renamed"                                # stale write did not clobber


@pytest.mark.integration
async def test_patch_without_if_match_is_rejected():
    ws = await seed_workspace()
    async with client() as c:
        aid = (await c.post("/api/v1/agents", headers=dev_headers(workspace=ws), json=_CREATE)).json()["id"]
        r = await c.patch(f"/api/v1/agents/{aid}", headers=dev_headers(workspace=ws), json={"name": "X"})
    assert r.status_code == 428 or (r.status_code == 400 and r.json()["error"]["code"] == "input.invalid")


# ---- Phase 8 convergence (T022/T023) -------------------------------------------------------------

@pytest.mark.integration
async def test_created_agent_is_invocable_end_to_end(monkeypatch):
    """T022 — a CREATED agent (not a seeded one) must be invocable: its frozen profile_snapshot resolves."""
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("ok"), usage_ev(), stop_ev()]))
    ws = await seed_workspace()
    async with client() as c:
        aid = (await c.post("/api/v1/agents", headers=dev_headers(workspace=ws), json=_CREATE)).json()["id"]
        r = await c.post(f"/api/v1/agents/{aid}/invoke",
                         headers=dev_headers(workspace=ws), json={"inputs": {"message": "hi"}})
    assert r.status_code == 200
    names = [n for n, _ in parse_sse(r.text)]
    assert names[0] == "run_start" and names[-1] == "done" and "usage" in names


@pytest.mark.integration
async def test_create_rejects_unknown_model():
    """T022 corollary — create fails closed on a provider/model with no capability profile."""
    ws = await seed_workspace()
    bad = {**_CREATE, "model": "no-such-model-9000"}
    async with client() as c:
        r = await c.post("/api/v1/agents", headers=dev_headers(workspace=ws), json=bad)
    assert r.status_code == 400 and r.json()["error"]["code"] == "input.invalid"


@pytest.mark.integration
async def test_create_endpoint_honors_idempotency_key():
    """T023 — the create endpoint wires the in-flight guard: a key already in flight → 409; a fresh key → 201."""
    ws = await seed_workspace()
    key = "k-endpoint-1"
    deps._INFLIGHT.add((ws, key))                        # simulate a concurrent in-flight create
    try:
        async with client() as c:
            r = await c.post("/api/v1/agents", headers=dev_headers(workspace=ws, idempotency_key=key), json=_CREATE)
        assert r.status_code == 409 and r.json()["error"]["code"] == "idempotency.in_progress"
    finally:
        deps._INFLIGHT.discard((ws, key))
    async with client() as c:
        ok = await c.post("/api/v1/agents", headers=dev_headers(workspace=ws, idempotency_key="k-endpoint-2"), json=_CREATE)
    assert ok.status_code == 201


# ---- post-review hardening (code-review 2026-11-05, F1–F5) ----------------------------------------

@pytest.mark.integration
async def test_list_pagination_is_complete_across_pages():
    """F1 — keyset pages must not drop/repeat rows (id is random uuid4, unrelated to created_at order)."""
    ws = await seed_workspace()
    created: set[str] = set()
    async with client() as c:
        for i in range(3):
            r = await c.post("/api/v1/agents", headers=dev_headers(workspace=ws), json={**_CREATE, "name": f"A{i}"})
            created.add(r.json()["id"])
        seen: set[str] = set()
        cursor, pages = None, 0
        while pages < 10:
            url = "/api/v1/agents?limit=1" + (f"&cursor={cursor}" if cursor else "")
            page = (await c.get(url, headers=dev_headers(workspace=ws))).json()
            seen.update(a["id"] for a in page["items"])
            cursor = page["next_cursor"]
            pages += 1
            if not cursor:
                break
    assert created == seen and len(seen) == 3          # every agent returned exactly once across pages


@pytest.mark.integration
async def test_stored_snapshot_hash_is_authoritative():
    """F4 — the stored/returned snapshot_hash equals what the runtime recomputes via to_snapshot()."""
    ws = await seed_workspace()
    async with client() as c:
        created = (await c.post("/api/v1/agents", headers=dev_headers(workspace=ws), json=_CREATE)).json()
    vid, stored = created["versions"][0]["id"], created["versions"][0]["snapshot_hash"]
    async with tenant_session(ws) as s:
        v = (await s.execute(
            select(AgentVersion).where(AgentVersion.id == _uuid.UUID(vid)).options(
                selectinload(AgentVersion.retrieval_bindings).selectinload(RetrievalBinding.kb),
                selectinload(AgentVersion.tool_bindings), selectinload(AgentVersion.skill_bindings)))).scalar_one()
        assert stored and v.to_snapshot().snapshot_hash == stored


@pytest.mark.integration
async def test_patch_foreign_active_version_rejected():
    """F3 — re-pointing to a version of ANOTHER agent is rejected (no cross-agent mispublish)."""
    ws = await seed_workspace()
    async with client() as c:
        a1 = await c.post("/api/v1/agents", headers=dev_headers(workspace=ws), json=_CREATE)
        a2 = await c.post("/api/v1/agents", headers=dev_headers(workspace=ws), json=_CREATE)
        a1id, a1etag = a1.json()["id"], a1.headers["ETag"]
        a2_version = a2.json()["versions"][0]["id"]
        r = await c.patch(f"/api/v1/agents/{a1id}", headers=dev_headers(workspace=ws, if_match=a1etag),
                          json={"active_version_id": a2_version})
    assert r.status_code == 404                         # a2's version is not a1's


@pytest.mark.integration
async def test_duplicate_version_label_rejected():
    """F5 — an explicit label that already exists is rejected."""
    ws = await seed_workspace()
    async with client() as c:
        aid = (await c.post("/api/v1/agents", headers=dev_headers(workspace=ws), json=_CREATE)).json()["id"]
        r = await c.post(f"/api/v1/agents/{aid}/versions", headers=dev_headers(workspace=ws), params={"label": "v1"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "input.invalid"
