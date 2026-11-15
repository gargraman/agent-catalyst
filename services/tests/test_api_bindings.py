"""Retrieval-binding authoring on version creation (feature 024, tasks.md T035/T043; research D12 + G2; analyze H2).

`POST /api/v1/agents/{id}/versions` accepts an optional JSON body `{label?, retrieval_bindings?}`:
* bindings are validated (400 `input.invalid` with the offending field) and frozen with the KB's current
  `kb_index_version`; the version's `snapshot_hash` covers them;
* G2: omitting `retrieval_bindings` COPIES the latest version's bindings (same hash) instead of silently dropping them;
  `[]` means no bindings;
* the caller must be able to MANAGE each bound KB (404 if not usable, 403 if usable-not-manageable); archived → 409;
* H2: a viewer denied USE on a bound KB sees that binding masked (`restricted: true`, no id/name/model/version).
Integration (compose Postgres).
"""
from __future__ import annotations

import pytest

from tests._api_helpers import client, dev_headers, seed_agent, seed_kb, seed_version, seed_workspace

pytestmark = pytest.mark.integration

_CFG = {"top_k": 6, "similarity_threshold": 0.2, "max_ctx_tokens": 2000}
_DENY_BUILDER = {"visibility": "restricted", "useRoles": ["admin"], "manageRoles": ["admin"]}
_USE_NOT_MANAGE = {"visibility": "restricted", "useRoles": ["builder"], "manageRoles": ["publisher"]}


async def _setup(**kb_kw):
    ws = await seed_workspace()
    aid = await seed_agent(ws)
    await seed_version(ws, aid)
    kb = await seed_kb(ws, **kb_kw)
    return ws, aid, kb


def _binding(kb, **over):
    b = {"kb_id": kb, "injection": "always_on", "retrieval_config": dict(_CFG)}
    b.update(over)
    return b


async def _post(c, ws, aid, body=None, **hdr):
    kw = {"json": body} if body is not None else {}
    return await c.post(f"/api/v1/agents/{aid}/versions", headers=dev_headers(workspace=ws, **hdr), **kw)


async def test_bound_version_freezes_kb_facts_and_changes_the_hash():
    ws, aid, kb = await _setup(slug="handbook-kb")
    async with client() as c:
        unbound = await _post(c, ws, aid, {"retrieval_bindings": []})
        bound = await _post(c, ws, aid, {"retrieval_bindings": [_binding(kb)]})
    assert unbound.status_code == 201 and bound.status_code == 201, bound.text
    (b,) = bound.json()["retrieval_bindings"]
    assert b["kb_id"] == kb and b["kb_name"] == "Handbook-Kb" and b["restricted"] is False
    assert b["embedding_model"] == "text-embedding-3-small" and b["kb_index_version"] == 1
    assert b["injection"] == "always_on"
    assert b["retrieval_config"] == {**_CFG, "hybrid": False, "reranker": None, "metadata_filters": {}}
    assert unbound.json()["retrieval_bindings"] == []
    assert bound.json()["snapshot_hash"] != unbound.json()["snapshot_hash"]


async def test_g2_omitted_bindings_are_copied_with_the_same_hash():
    ws, aid, kb = await _setup()
    async with client() as c:
        bound = await _post(c, ws, aid, {"retrieval_bindings": [_binding(kb)]})
        copied = await _post(c, ws, aid)                         # no body at all
        copied2 = await _post(c, ws, aid, {"label": "v-copy"})   # body without the field
    for r in (copied, copied2):
        assert r.status_code == 201
        assert [x["kb_id"] for x in r.json()["retrieval_bindings"]] == [kb]
        assert r.json()["snapshot_hash"] == bound.json()["snapshot_hash"]


@pytest.mark.parametrize("bad", [
    {"retrieval_config": {**_CFG, "top_k": 0}},
    {"retrieval_config": {**_CFG, "top_k": 21}},
    {"retrieval_config": {**_CFG, "similarity_threshold": 1.5}},
    {"retrieval_config": {**_CFG, "similarity_threshold": -0.1}},
    {"retrieval_config": {**_CFG, "max_ctx_tokens": 99}},
    {"retrieval_config": {**_CFG, "max_ctx_tokens": 8001}},
    {"injection": "stuffing"},
    {"retrieval_config": {**_CFG, "unknown_lever": 1}},
])
async def test_invalid_binding_is_400(bad):
    ws, aid, kb = await _setup()
    async with client() as c:
        r = await _post(c, ws, aid, {"retrieval_bindings": [_binding(kb, **bad)]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "input.invalid", r.text


async def test_duplicate_kb_and_too_many_bindings_are_400():
    ws, aid, kb = await _setup()
    extra = [await seed_kb(ws, slug=f"kb-{i}") for i in range(5)]
    async with client() as c:
        dup = await _post(c, ws, aid, {"retrieval_bindings": [_binding(kb), _binding(kb)]})
        many = await _post(c, ws, aid, {"retrieval_bindings": [_binding(k) for k in [kb, *extra]]})
    assert dup.status_code == 400 and many.status_code == 400


async def test_kb_access_and_archive_rules():
    ws, aid, _ = await _setup()
    hidden = await seed_kb(ws, slug="hidden", access_policy=_DENY_BUILDER)
    use_only = await seed_kb(ws, slug="use-only", access_policy=_USE_NOT_MANAGE)
    archived = await seed_kb(ws, slug="archived")
    async with client() as c:
        await c.post(f"/api/v1/knowledge-bases/{archived}/archive", headers=dev_headers(workspace=ws))
        r404 = await _post(c, ws, aid, {"retrieval_bindings": [_binding(hidden)]})
        r403 = await _post(c, ws, aid, {"retrieval_bindings": [_binding(use_only)]})
        r409 = await _post(c, ws, aid, {"retrieval_bindings": [_binding(archived)]})
    assert r404.status_code == 404 and r403.status_code == 403
    assert r409.status_code == 409 and r409.json()["error"]["code"] == "kb.archived"


async def test_p2_levers_are_accepted_and_stored():
    ws, aid, kb = await _setup()
    cfg = {**_CFG, "hybrid": True, "reranker": "bge", "metadata_filters": {"a": 1}}
    async with client() as c:
        r = await _post(c, ws, aid, {"retrieval_bindings": [_binding(kb, retrieval_config=cfg)]})
    assert r.status_code == 201 and r.json()["retrieval_bindings"][0]["retrieval_config"] == cfg


async def test_agent_detail_projects_bindings_and_masks_restricted_kbs():
    ws, aid, _ = await _setup()
    secret_kb = await seed_kb(ws, slug="secret-kb-name", access_policy={"visibility": "restricted",
                                                                       "useRoles": ["admin"], "manageRoles": ["admin"]})
    async with client() as c:
        adm = {"claims": {ws: "admin"}, "subject": "adm"}
        v = await _post(c, ws, aid, {"retrieval_bindings": [_binding(secret_kb)]}, **adm)
        assert v.status_code == 201, v.text
        etag = (await c.get(f"/api/v1/agents/{aid}", headers=dev_headers(workspace=ws, **adm))).headers["ETag"]
        act = await c.patch(f"/api/v1/agents/{aid}", headers=dev_headers(workspace=ws, if_match=etag, **adm),
                            json={"active_version_id": v.json()["id"]})
        assert act.status_code == 200, act.text
        as_admin = await c.get(f"/api/v1/agents/{aid}", headers=dev_headers(workspace=ws, **adm))
        as_builder = await c.get(f"/api/v1/agents/{aid}", headers=dev_headers(workspace=ws, subject="b"))
    admin_b = next(x for x in as_admin.json()["versions"] if x["id"] == v.json()["id"])["retrieval_bindings"][0]
    assert admin_b["kb_id"] == secret_kb and admin_b["restricted"] is False
    builder_b = next(x for x in as_builder.json()["versions"] if x["id"] == v.json()["id"])["retrieval_bindings"][0]
    assert builder_b["restricted"] is True
    assert builder_b["kb_id"] is None and builder_b["kb_name"] is None
    assert builder_b["embedding_model"] is None and builder_b["kb_index_version"] is None
    assert builder_b["injection"] == "always_on"
    assert secret_kb not in as_builder.text and "Secret-Kb-Name" not in as_builder.text
