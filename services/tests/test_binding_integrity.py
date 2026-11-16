"""Feature 025 US4 — binding integrity at the DATA layer (migration 0015; data-model §B1–§B4, spec FR-016–FR-021).

Every guarantee here is asserted in Postgres, as the non-superuser `ac_app` role (RLS applies), NOT through the API:
  * US4-1 reference FKs — a binding to a missing skill / tool / MCP tool / KB, or to a same-id-shape row that lives in
    ANOTHER workspace, is refused (23503). Composite (workspace_id, id) FKs are the only RLS-proof guarantee (G8).
  * US4-2 immutability (F15 parity) — UPDATE / DELETE of a committed version's binding, or a late INSERT, is refused
    with 23001 `frozen_binding:`; version + bindings in ONE transaction still works; a SAVEPOINT-inserted version is
    rejected (G5, pinned — fail-closed direction).
  * US4-3 `ck_tool_bindings_one_target` (23514).
  * US4-4 `agents.active_version_id` must be a version of the SAME agent (deferred → checked at COMMIT).
  * US4-5 chunk vector size must equal the KB's declared `embedding_dim` (23514 `chunk_dim_mismatch:`); a chunk under a
    KB of another workspace is refused (23503).
  * US4-6 hard DELETE of a referenced skill/tool/KB is refused (RESTRICT); archiving still works.
Integration: needs the compose Postgres at head.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integration

_VEC = "[" + ",".join(["0.01"] * 1024) + "]"
_MC = json.dumps({"provider": "openai", "model": "gpt-4o-mini", "params": {}})


async def set_ws(conn, ws) -> None:
    await conn.execute(text("select set_config('app.workspace_id', :w, true)"), {"w": str(ws)})


def _sqlstate(exc: IntegrityError) -> str | None:
    return getattr(exc.orig, "sqlstate", None)


def _constraint(exc: IntegrityError) -> str | None:
    return getattr(exc.orig.__cause__, "constraint_name", None)


def _message(exc: IntegrityError) -> str:
    return str(getattr(exc.orig.__cause__, "message", None) or exc.orig)


async def _ws(engine) -> str:
    async with engine.begin() as c:
        return str((await c.execute(text("insert into workspaces (name) values (:n) returning id"),
                                    {"n": f"bi-{uuid.uuid4().hex[:8]}"})).scalar_one())


async def _catalog(engine, ws: str) -> dict:
    """One committed agent (+v1), skill, tool, MCP server+tool and 1024-dim KB in `ws`."""
    async with engine.begin() as c:
        await set_ws(c, ws)
        q = lambda sql, **p: c.execute(text(sql), {"w": ws, **p})  # noqa: E731
        agent = (await q("insert into agents (workspace_id, name) values (:w, 'a') returning id")).scalar_one()
        skill = (await q("insert into skills (workspace_id, slug, name) values (:w, :s, 'S') returning id",
                         s=f"s-{uuid.uuid4().hex[:6]}")).scalar_one()
        tool = (await q("insert into tools (workspace_id, name) values (:w, 'T') returning id")).scalar_one()
        srv = (await q("insert into mcp_servers (workspace_id, name) values (:w, 'M') returning id")).scalar_one()
        mtool = (await q("insert into mcp_tools (workspace_id, server_id, name) values (:w, :s, 'mt') returning id",
                         s=str(srv))).scalar_one()
        kb = (await q("insert into knowledge_bases (workspace_id, slug, name, embedding_model, embedding_dim) "
                      "values (:w, :s, 'KB', 'amazon.titan-embed-text-v2:0', 1024) returning id",
                      s=f"kb-{uuid.uuid4().hex[:6]}")).scalar_one()
    return {"ws": ws, "agent": str(agent), "skill": str(skill), "tool": str(tool), "mcp_tool": str(mtool),
            "kb": str(kb)}


async def _version(c, ws: str, agent: str, label: str | None = None) -> str:
    return str((await c.execute(text(
        "insert into agent_versions (workspace_id, agent_id, label, system_prompt, prompt_hash, snapshot_hash, "
        "model_config) values (:w, :a, :l, 'p', 'ph', 'sh', cast(:mc as jsonb)) returning id"),
        {"w": ws, "a": agent, "l": label or f"v-{uuid.uuid4().hex[:6]}", "mc": _MC})).scalar_one())


async def _skill_binding(c, ws, vid, skill):
    await c.execute(text("insert into skill_bindings (workspace_id, version_id, skill_id, skill_content_hash) "
                         "values (:w, :v, :s, 'h')"), {"w": ws, "v": vid, "s": skill})


async def _tool_binding(c, ws, vid, *, kind="http", tool=None, mcp_tool=None, subagent=None):
    await c.execute(text(
        "insert into tool_bindings (workspace_id, version_id, kind, tool_id, mcp_tool_id, subagent_config_id, "
        "schema_snapshot) values (:w, :v, cast(:k as tool_kind), :t, :m, :s, '{}'::jsonb)"),
        {"w": ws, "v": vid, "k": kind, "t": tool, "m": mcp_tool, "s": subagent})


async def _retrieval_binding(c, ws, vid, kb):
    await c.execute(text("insert into retrieval_bindings (workspace_id, version_id, kb_id, kb_index_version, "
                         "retrieval_config) values (:w, :v, :k, 1, '{}'::jsonb)"), {"w": ws, "v": vid, "k": kb})


async def _committed_version_with_bindings(engine, cat: dict) -> str:
    """The `create_version` shape: version + all three binding kinds in ONE transaction (must succeed)."""
    async with engine.begin() as c:
        await set_ws(c, cat["ws"])
        vid = await _version(c, cat["ws"], cat["agent"])
        await _skill_binding(c, cat["ws"], vid, cat["skill"])
        await _tool_binding(c, cat["ws"], vid, tool=cat["tool"])
        await _retrieval_binding(c, cat["ws"], vid, cat["kb"])
    return vid


async def _expect(engine, ws: str, fn, sqlstate: str, *, constraint: str | None = None, prefix: str | None = None):
    with pytest.raises(IntegrityError) as ei:
        async with engine.begin() as c:
            await set_ws(c, ws)
            await fn(c)
    assert _sqlstate(ei.value) == sqlstate, f"want {sqlstate}, got {_sqlstate(ei.value)}: {_message(ei.value)}"
    if constraint is not None:
        assert _constraint(ei.value) == constraint, _message(ei.value)
    if prefix is not None:
        assert _message(ei.value).startswith(prefix), _message(ei.value)
    return ei.value


# ------------------------------------------------------------------------------------- US4-1 reference FKs


@pytest.mark.parametrize("kind", ["skill", "tool", "mcp_tool", "kb"])
async def test_binding_to_missing_target_is_refused(engine, kind) -> None:
    cat = await _catalog(engine, await _ws(engine))
    ws, missing = cat["ws"], str(uuid.uuid4())
    want = {"skill": "fk_skill_bindings_skill", "tool": "fk_tool_bindings_tool",
            "mcp_tool": "fk_tool_bindings_mcp_tool", "kb": None}[kind]

    async def go(c):
        vid = await _version(c, ws, cat["agent"])
        if kind == "skill":
            await _skill_binding(c, ws, vid, missing)
        elif kind == "tool":
            await _tool_binding(c, ws, vid, tool=missing)
        elif kind == "mcp_tool":
            await _tool_binding(c, ws, vid, kind="mcp", mcp_tool=missing)
        else:
            await _retrieval_binding(c, ws, vid, missing)

    await _expect(engine, ws, go, "23503", constraint=want)


@pytest.mark.parametrize("kind", ["skill", "tool", "mcp_tool", "kb"])
async def test_binding_to_other_workspace_target_is_refused(engine, kind) -> None:
    """The target row EXISTS — in another workspace. RI checks bypass RLS, so only a composite FK refuses this (G8)."""
    mine = await _catalog(engine, await _ws(engine))
    theirs = await _catalog(engine, await _ws(engine))
    ws = mine["ws"]
    want = {"skill": "fk_skill_bindings_skill", "tool": "fk_tool_bindings_tool",
            "mcp_tool": "fk_tool_bindings_mcp_tool", "kb": "fk_retrieval_bindings_kb_ws"}[kind]

    async def go(c):
        vid = await _version(c, ws, mine["agent"])
        if kind == "skill":
            await _skill_binding(c, ws, vid, theirs["skill"])
        elif kind == "tool":
            await _tool_binding(c, ws, vid, tool=theirs["tool"])
        elif kind == "mcp_tool":
            await _tool_binding(c, ws, vid, kind="mcp", mcp_tool=theirs["mcp_tool"])
        else:
            await _retrieval_binding(c, ws, vid, theirs["kb"])

    await _expect(engine, ws, go, "23503", constraint=want)


# ------------------------------------------------------------------------------------- US4-2 immutability


async def test_version_and_bindings_in_one_transaction_succeeds(engine) -> None:
    cat = await _catalog(engine, await _ws(engine))
    vid = await _committed_version_with_bindings(engine, cat)
    async with engine.begin() as c:
        await set_ws(c, cat["ws"])
        n = (await c.execute(text(
            "select (select count(*) from skill_bindings where version_id = :v) + "
            "(select count(*) from tool_bindings where version_id = :v) + "
            "(select count(*) from retrieval_bindings where version_id = :v)"), {"v": vid})).scalar_one()
    assert n == 3


@pytest.mark.parametrize("table,assign", [
    ("skill_bindings", "skill_content_hash = 'tampered'"),
    ("tool_bindings", "config_snapshot = '{\"x\": 1}'::jsonb"),
    ("retrieval_bindings", "kb_index_version = 99"),
])
async def test_update_of_committed_binding_is_refused_loudly(engine, table, assign) -> None:
    cat = await _catalog(engine, await _ws(engine))
    vid = await _committed_version_with_bindings(engine, cat)

    async def go(c):
        await c.execute(text(f"update {table} set {assign} where version_id = :v"), {"v": vid})

    await _expect(engine, cat["ws"], go, "23001", prefix="frozen_binding:")


@pytest.mark.parametrize("table", ["skill_bindings", "tool_bindings", "retrieval_bindings"])
async def test_delete_of_committed_binding_is_refused_loudly(engine, table) -> None:
    cat = await _catalog(engine, await _ws(engine))
    vid = await _committed_version_with_bindings(engine, cat)

    async def go(c):
        await c.execute(text(f"delete from {table} where version_id = :v"), {"v": vid})

    await _expect(engine, cat["ws"], go, "23001", prefix="frozen_binding:")
    async with engine.begin() as c:                                    # nothing was removed; the hash is untouched
        await set_ws(c, cat["ws"])
        assert (await c.execute(text(f"select count(*) from {table} where version_id = :v"),
                                {"v": vid})).scalar_one() == 1
        assert (await c.execute(text("select snapshot_hash from agent_versions where id = :v"),
                                {"v": vid})).scalar_one() == "sh"


@pytest.mark.parametrize("kind", ["skill", "tool", "kb"])
async def test_late_insert_into_committed_version_is_refused(engine, kind) -> None:
    cat = await _catalog(engine, await _ws(engine))
    async with engine.begin() as c:                                    # version committed with NO bindings
        await set_ws(c, cat["ws"])
        vid = await _version(c, cat["ws"], cat["agent"])
    ws = cat["ws"]

    async def go(c):
        if kind == "skill":
            await _skill_binding(c, ws, vid, cat["skill"])
        elif kind == "tool":
            await _tool_binding(c, ws, vid, tool=cat["tool"])
        else:
            await _retrieval_binding(c, ws, vid, cat["kb"])

    await _expect(engine, ws, go, "23001", prefix="frozen_binding:")


async def test_savepoint_inserted_version_rejects_bindings_pinned(engine) -> None:
    """Research G5: xmin of a row inserted inside a SAVEPOINT is the subxact id, not the top-level one ⇒ the insert
    window refuses. Fail-closed by design; pinned so a future nested-txn writer learns it from a red test."""
    cat = await _catalog(engine, await _ws(engine))
    ws = cat["ws"]

    async def go(c):
        sp = await c.begin_nested()
        vid = await _version(c, ws, cat["agent"])
        await sp.commit()
        await _skill_binding(c, ws, vid, cat["skill"])

    await _expect(engine, ws, go, "23001", prefix="frozen_binding:")


# ------------------------------------------------------------------------------------- US4-3 one target


@pytest.mark.parametrize("targets", ["none", "tool_and_mcp"])
async def test_tool_binding_must_name_exactly_one_target(engine, targets) -> None:
    cat = await _catalog(engine, await _ws(engine))
    ws = cat["ws"]

    async def go(c):
        vid = await _version(c, ws, cat["agent"])
        if targets == "none":
            await _tool_binding(c, ws, vid, kind="http")
        else:
            await _tool_binding(c, ws, vid, kind="http", tool=cat["tool"], mcp_tool=cat["mcp_tool"])

    await _expect(engine, ws, go, "23514", constraint="ck_tool_bindings_one_target")


async def test_subagent_binding_with_only_subagent_id_is_accepted(engine) -> None:
    cat = await _catalog(engine, await _ws(engine))
    async with engine.begin() as c:
        await set_ws(c, cat["ws"])
        vid = await _version(c, cat["ws"], cat["agent"])
        await _tool_binding(c, cat["ws"], vid, kind="subagent", subagent=str(uuid.uuid4()))


# ------------------------------------------------------------------------------------- US4-4 active version


async def test_active_version_of_another_agent_is_refused_at_commit(engine) -> None:
    cat = await _catalog(engine, await _ws(engine))
    ws = cat["ws"]
    async with engine.begin() as c:
        await set_ws(c, ws)
        other_agent = str((await c.execute(text(
            "insert into agents (workspace_id, name) values (:w, 'b') returning id"), {"w": ws})).scalar_one())
        foreign_v = await _version(c, ws, other_agent)

    async def go(c):
        await c.execute(text("update agents set active_version_id = :v where id = :a"),
                        {"v": foreign_v, "a": cat["agent"]})

    await _expect(engine, ws, go, "23503", constraint="fk_agents_active_version")


async def test_active_version_fk_is_deferred_for_circular_create(engine) -> None:
    """Agent + first version + re-point in one txn, pointing BEFORE the version row exists (deferred to COMMIT)."""
    ws = await _ws(engine)
    async with engine.begin() as c:
        await set_ws(c, ws)
        aid, vid = str(uuid.uuid4()), str(uuid.uuid4())
        await c.execute(text("insert into agents (id, workspace_id, name, active_version_id) values (:a, :w, 'c', :v)"),
                        {"a": aid, "w": ws, "v": vid})
        await c.execute(text(
            "insert into agent_versions (id, workspace_id, agent_id, label, system_prompt, prompt_hash, snapshot_hash, "
            "model_config) values (:v, :w, :a, 'v1', 'p', 'ph', 'sh', cast(:mc as jsonb))"),
            {"v": vid, "w": ws, "a": aid, "mc": _MC})


# ------------------------------------------------------------------------------------- US4-5 chunks


async def _doc(c, ws, kb) -> str:
    return str((await c.execute(text(
        "insert into kb_documents (workspace_id, kb_id, name, media_type, size_bytes, content_hash, storage_key, "
        "status, created_by) values (:w, :k, 'd.txt', 'text/plain', 1, 'h', 'k', 'ready', 't') returning id"),
        {"w": ws, "k": kb})).scalar_one())


async def _chunk(c, ws, kb, doc):
    await c.execute(text(
        "insert into kb_chunks (workspace_id, kb_id, document_id, generation, ordinal, text, token_count, embedding, "
        "embedding_model) values (:w, :k, :d, 1, 0, 't', 1, cast(:v as vector), 'm')"),
        {"w": ws, "k": kb, "d": doc, "v": _VEC})


async def test_chunk_dimension_must_match_kb_declared_dimension(engine) -> None:
    ws = await _ws(engine)
    async with engine.begin() as c:
        await set_ws(c, ws)
        kb = str((await c.execute(text(
            "insert into knowledge_bases (workspace_id, slug, name, embedding_model, embedding_dim) "
            "values (:w, :s, 'KB768', 'legacy-768', 768) returning id"),
            {"w": ws, "s": f"kb-{uuid.uuid4().hex[:6]}"})).scalar_one())
        doc = await _doc(c, ws, kb)

    async def go(c):
        await _chunk(c, ws, kb, doc)

    await _expect(engine, ws, go, "23514", prefix="chunk_dim_mismatch:")


async def test_chunk_under_other_workspace_kb_is_refused(engine) -> None:
    mine = await _catalog(engine, await _ws(engine))
    theirs = await _catalog(engine, await _ws(engine))
    ws = mine["ws"]
    async with engine.begin() as c:
        await set_ws(c, ws)
        doc = await _doc(c, ws, mine["kb"])

    async def go(c):
        await _chunk(c, ws, theirs["kb"], doc)

    await _expect(engine, ws, go, "23503", constraint="fk_kb_chunks_kb")


async def test_matching_chunk_is_accepted(engine) -> None:
    cat = await _catalog(engine, await _ws(engine))
    async with engine.begin() as c:
        await set_ws(c, cat["ws"])
        await _chunk(c, cat["ws"], cat["kb"], await _doc(c, cat["ws"], cat["kb"]))


# ------------------------------------------------------------------------------------- US4-6 RESTRICT vs archive


@pytest.mark.parametrize("kind", ["skill", "tool", "kb"])
async def test_referenced_catalog_row_cannot_be_hard_deleted_but_can_be_archived(engine, kind) -> None:
    cat = await _catalog(engine, await _ws(engine))
    await _committed_version_with_bindings(engine, cat)
    table = {"skill": "skills", "tool": "tools", "kb": "knowledge_bases"}[kind]

    async def go(c):
        await c.execute(text(f"delete from {table} where id = :i"), {"i": cat[kind]})

    await _expect(engine, cat["ws"], go, "23503")
    async with engine.begin() as c:
        await set_ws(c, cat["ws"])
        await c.execute(text(f"update {table} set archived_at = now() where id = :i"), {"i": cat[kind]})
