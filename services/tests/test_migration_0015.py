"""Migration 0015 — binding integrity (feature 025 US4; data-model §B1–§B7, research D10/D11, spec FR-022).

  * Preflight REFUSES (never repairs): starting at 0014, one seeded violation per rule makes `upgrade 0015` fail with a
    message listing `<rule>=<count>` and ids only (no text content); the DB stays at 0014 and nothing was deleted.
  * Clean DB: 0015 → 0014 → 0015 round-trips; every 0015 object exists after upgrade and is gone after downgrade
    (and the pre-0015 single-column `fk_agents_active_version` is restored by the downgrade).
  * The preflight disables row security (`SET LOCAL row_security = off`) so a non-bypass role fails LOUD (G7).
  * ORM parity: every constraint name 0015 adds is declared in `Base.metadata`.
Runs alembic in a subprocess as the owner role; the DB is left at head in `finally`. Seeded rows are inserted as the
superuser owner (`ac`) at 0014, where no 0015 constraint/trigger exists yet, and removed again before re-upgrading.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from api.models import Base
from api.settings import get_settings

pytestmark = pytest.mark.integration

_SERVICES = Path(__file__).resolve().parents[1]
_MIG = _SERVICES / "migrations" / "versions" / "0015_binding_integrity.py"
_VEC = "[" + ",".join(["0.01"] * 1024) + "]"
_MC = json.dumps({"provider": "openai", "model": "gpt-4o-mini", "params": {}})

_UNIQUES = ("uq_skills_ws_id", "uq_tools_ws_id", "uq_mcp_tools_ws_id", "uq_kb_ws_id", "uq_agent_versions_id_agent")
_FKS = ("fk_skill_bindings_skill", "fk_tool_bindings_tool", "fk_tool_bindings_mcp_tool",
        "fk_retrieval_bindings_kb_ws", "fk_kb_chunks_kb", "fk_agents_active_version")
_CHECKS = ("ck_tool_bindings_one_target",)
_FUNCS = ("bindings_freeze", "bindings_insert_window", "kb_chunks_dim_check")
_RULES = ("skill_ref", "tool_ref", "mcp_tool_ref", "kb_ref_ws", "chunk_orphan", "chunk_dim", "tool_target",
          "active_version_agent")


def _alembic(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=_SERVICES, capture_output=True, text=True,
                          env={**os.environ}, timeout=180)


def _owner():
    return create_engine(get_settings().database_sync_url)


def _objects(conn) -> dict:
    cons = set(conn.execute(text("select conname from pg_constraint")).scalars().all())
    funcs = set(conn.execute(text("select proname from pg_proc")).scalars().all())
    fk_def = conn.execute(text("select pg_get_constraintdef(oid) from pg_constraint "
                               "where conname = 'fk_agents_active_version'")).scalar()
    return {"cons": cons, "funcs": funcs, "fk_active_def": fk_def or ""}


def _seed_base(c, tag: str) -> dict:
    """Two workspaces with an agent+version, a skill, a tool, an MCP tool, a 1024 KB + doc each (owner role)."""
    out = {}
    for side in ("a", "b"):
        ws = c.execute(text("insert into workspaces (name) values (:n) returning id"), {"n": f"m15-{tag}-{side}"}).scalar_one()
        agent = c.execute(text("insert into agents (workspace_id, name) values (:w, 'a') returning id"), {"w": ws}).scalar_one()
        vid = c.execute(text(
            "insert into agent_versions (workspace_id, agent_id, label, system_prompt, prompt_hash, snapshot_hash, "
            "model_config) values (:w, :a, 'v1', 'SECRET-PROMPT-TEXT', 'ph', 'sh', cast(:mc as jsonb)) returning id"),
            {"w": ws, "a": agent, "mc": _MC}).scalar_one()
        kb = c.execute(text("insert into knowledge_bases (workspace_id, slug, name, embedding_model, embedding_dim) "
                            "values (:w, :s, 'KB', 'm', 1024) returning id"),
                       {"w": ws, "s": f"kb-{uuid.uuid4().hex[:6]}"}).scalar_one()
        doc = c.execute(text(
            "insert into kb_documents (workspace_id, kb_id, name, media_type, size_bytes, content_hash, storage_key, "
            "status, created_by) values (:w, :k, 'd', 'text/plain', 1, 'h', 'k', 'ready', 't') returning id"),
            {"w": ws, "k": kb}).scalar_one()
        out[side] = {"ws": ws, "agent": agent, "vid": vid, "kb": kb, "doc": doc}
    return out


def _seed_violation(c, rule: str, b: dict) -> list:
    """Insert exactly one violating row for `rule`; returns [(table, id)] for cleanup."""
    a, o = b["a"], b["b"]
    if rule == "skill_ref":
        i = c.execute(text("insert into skill_bindings (workspace_id, version_id, skill_id, skill_content_hash) "
                           "values (:w, :v, gen_random_uuid(), 'h') returning id"), {"w": a["ws"], "v": a["vid"]}).scalar_one()
        return [("skill_bindings", i)]
    if rule in ("tool_ref", "mcp_tool_ref"):
        kind, col = ("http", "tool_id") if rule == "tool_ref" else ("mcp", "mcp_tool_id")
        i = c.execute(text(f"insert into tool_bindings (workspace_id, version_id, kind, {col}, schema_snapshot) "
                           f"values (:w, :v, cast(:k as tool_kind), gen_random_uuid(), '{{}}'::jsonb) returning id"),
                      {"w": a["ws"], "v": a["vid"], "k": kind}).scalar_one()
        return [("tool_bindings", i)]
    if rule == "kb_ref_ws":
        i = c.execute(text("insert into retrieval_bindings (workspace_id, version_id, kb_id, kb_index_version, "
                           "retrieval_config) values (:w, :v, :k, 1, '{}'::jsonb) returning id"),
                      {"w": a["ws"], "v": a["vid"], "k": o["kb"]}).scalar_one()
        return [("retrieval_bindings", i)]
    if rule in ("chunk_orphan", "chunk_dim"):
        kb = o["kb"] if rule == "chunk_orphan" else a["kb"]
        if rule == "chunk_dim":
            c.execute(text("update knowledge_bases set embedding_dim = 768 where id = :k"), {"k": a["kb"]})
        i = c.execute(text(
            "insert into kb_chunks (workspace_id, kb_id, document_id, generation, ordinal, text, token_count, "
            "embedding, embedding_model) values (:w, :k, :d, 1, 0, 'SECRET-CHUNK-TEXT', 1, cast(:v as vector), 'm') "
            "returning id"), {"w": a["ws"], "k": kb, "d": a["doc"], "v": _VEC}).scalar_one()
        return [("kb_chunks", i)]
    if rule == "tool_target":
        i = c.execute(text("insert into tool_bindings (workspace_id, version_id, kind, schema_snapshot) "
                           "values (:w, :v, 'http', '{}'::jsonb) returning id"), {"w": a["ws"], "v": a["vid"]}).scalar_one()
        return [("tool_bindings", i)]
    if rule == "active_version_agent":
        c.execute(text("update agents set active_version_id = :v where id = :a"), {"v": o["vid"], "a": a["agent"]})
        return [("agents_active", a["agent"])]
    raise AssertionError(rule)


def _cleanup(c, rows: list) -> None:
    for table, i in rows:
        if table == "agents_active":
            c.execute(text("update agents set active_version_id = null where id = :i"), {"i": i})
        else:
            c.execute(text(f"delete from {table} where id = :i"), {"i": i})


def _restore_head() -> None:
    up = _alembic("upgrade", "head")
    assert up.returncode == 0, f"could not restore head:\n{up.stderr[-3000:]}"


def test_preflight_refuses_each_rule_and_applies_nothing() -> None:
    eng = _owner()
    try:
        down = _alembic("downgrade", "0014")
        assert down.returncode == 0, f"downgrade to 0014 failed:\n{down.stderr[-3000:]}"
        for rule in _RULES:
            with eng.begin() as c:
                base = _seed_base(c, rule)
                c.execute(text("update knowledge_bases set embedding_dim = 1024 where id = :k"), {"k": base["a"]["kb"]})
                rows = _seed_violation(c, rule, base)
            try:
                up = _alembic("upgrade", "0015")
                assert up.returncode != 0, f"{rule}: preflight did NOT refuse"
                err = up.stderr
                # boundary-anchored: "tool_ref=1" must not be satisfied by "mcp_tool_ref=1"
                assert "0015 preflight refused" in err and re.search(rf"(?<![a-z_]){rule}=1 ", err), f"{rule}: {err[-1500:]}"
                assert "SECRET-PROMPT-TEXT" not in err and "SECRET-CHUNK-TEXT" not in err, "preflight leaked content"
                assert "0014" in _alembic("current").stdout, f"{rule}: DB moved off 0014"
                with eng.begin() as c:                                    # nothing was repaired/deleted
                    for table, i in rows:
                        if table != "agents_active":
                            assert c.execute(text(f"select count(*) from {table} where id = :i"),
                                             {"i": i}).scalar_one() == 1, f"{rule}: preflight deleted a row"
            finally:
                with eng.begin() as c:
                    _cleanup(c, rows)
                    c.execute(text("update knowledge_bases set embedding_dim = 1024 where id = :k"), {"k": base["a"]["kb"]})
    finally:
        _restore_head()
        eng.dispose()


def test_preflight_disables_row_security() -> None:
    src = _MIG.read_text()
    assert "SET LOCAL row_security = off" in src, "preflight must fail loud for a non-bypass role (G7)"


def test_clean_roundtrip_adds_and_removes_every_object() -> None:
    eng = _owner()
    try:
        assert _alembic("upgrade", "head").returncode == 0
        with eng.connect() as c:
            up1 = _objects(c)
        for name in (*_UNIQUES, *_FKS, *_CHECKS):
            assert name in up1["cons"], f"{name} missing after upgrade"
        for fn in _FUNCS:
            assert fn in up1["funcs"], f"trigger function {fn} missing after upgrade"
        assert "DEFERRABLE INITIALLY DEFERRED" in up1["fk_active_def"] and "agent_id" in up1["fk_active_def"]

        down = _alembic("downgrade", "0014")
        assert down.returncode == 0, down.stderr[-3000:]
        with eng.connect() as c:
            d = _objects(c)
        for name in (*_UNIQUES, *_CHECKS, *(f for f in _FKS if f != "fk_agents_active_version")):
            assert name not in d["cons"], f"{name} survived downgrade"
        for fn in _FUNCS:
            assert fn not in d["funcs"], f"{fn} survived downgrade"
        # the pre-0015 single-column FK is restored (0001 shape), not dropped
        assert "fk_agents_active_version" in d["cons"]
        assert "DEFERRABLE" not in d["fk_active_def"] and "agent_id" not in d["fk_active_def"]

        up = _alembic("upgrade", "0015")
        assert up.returncode == 0, up.stderr[-3000:]
    finally:
        _restore_head()
        eng.dispose()


def test_orm_metadata_declares_every_0015_constraint() -> None:
    declared = set()
    for table in Base.metadata.tables.values():
        for con in table.constraints:
            if con.name:
                declared.add(con.name)
    missing = [n for n in (*_UNIQUES, *_FKS, *_CHECKS) if n not in declared]
    assert not missing, f"Base.metadata lacks 0015 constraints: {missing}"
