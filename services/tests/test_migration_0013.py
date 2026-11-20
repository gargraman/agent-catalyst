"""Migration 0013 — vector extension + kb_documents / kb_chunks / ingestion_jobs + ingestion_reap_stale (feature 024).

Asserts after `alembic upgrade head` (tasks.md T006, data-model.md §1–§4):
  * the pgvector extension and the three workspace-scoped tables exist, RLS ENABLED + FORCED with `*_ws_isolation`;
  * as `ac_app`, an unset tenant GUC reads zero rows (fail closed) and ws A cannot read ws B;
  * CHECK / UNIQUE constraints and ON DELETE CASCADE behave as specified;
  * `knowledge_bases.last_ingested_at` is a nullable timestamptz;
  * the cross-tenant reaper is a SECURITY DEFINER function PUBLIC cannot execute but ac_app can;
  * 0013 → 0012 → head round-trips cleanly (subprocess alembic, head restored in `finally`).
Integration: needs the compose Postgres (pgvector image). Connects as ac_app via the `engine` fixture.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

pytestmark = pytest.mark.integration


async def set_ws(conn, ws) -> None:  # same as conftest.set_ws (not imported: conftest is not a module to import)
    await conn.execute(text("select set_config('app.workspace_id', :w, true)"), {"w": str(ws)})


async def mk_ws(conn, name: str):
    return (await conn.execute(text("insert into workspaces (name) values (:n) returning id"), {"n": name})).scalar_one()

_TABLES = ("kb_documents", "kb_chunks", "ingestion_jobs")
_SERVICES = Path(__file__).resolve().parents[1]
_VEC = "[" + ",".join(["0.01"] * 1024) + "]"


async def _column(conn, table: str, column: str):
    return (await conn.execute(text(
        "select data_type, is_nullable, udt_name from information_schema.columns "
        "where table_name = :t and column_name = :c"), {"t": table, "c": column})).first()


async def _seed_doc(conn, ws, *, name: str = "doc.txt", status: str = "ready"):
    """Insert a KB + document (+ one chunk + one job) in `ws` under the tenant GUC. Returns (kb_id, doc_id)."""
    await set_ws(conn, ws)
    kb = (await conn.execute(text(
        "insert into knowledge_bases (workspace_id, slug, name, embedding_model, embedding_dim) "
        "values (:w, :s, 'KB', 'text-embedding-3-small', 1024) returning id"),
        {"w": str(ws), "s": f"kb-{uuid.uuid4().hex[:8]}"})).scalar_one()
    doc = (await conn.execute(text(
        "insert into kb_documents (workspace_id, kb_id, name, media_type, size_bytes, content_hash, storage_key, "
        "status, created_by) values (:w, :k, :n, 'text/plain', 10, 'h', 'ws/x', :st, 'tester') returning id"),
        {"w": str(ws), "k": str(kb), "n": name, "st": status})).scalar_one()
    await conn.execute(text(
        "insert into kb_chunks (workspace_id, kb_id, document_id, generation, ordinal, text, token_count, embedding, "
        "embedding_model) values (:w, :k, :d, 1, 0, 'hello', 1, CAST(:v AS vector), 'text-embedding-3-small')"),
        {"w": str(ws), "k": str(kb), "d": str(doc), "v": _VEC})
    await conn.execute(text(
        "insert into ingestion_jobs (workspace_id, kb_id, document_id, kind, status, created_by) "
        "values (:w, :k, :d, 'upload', 'ready', 'tester')"), {"w": str(ws), "k": str(kb), "d": str(doc)})
    return kb, doc


# ------------------------------------------------------------------------------------------------- schema


async def test_vector_extension_present(engine) -> None:
    async with engine.connect() as conn:
        n = (await conn.execute(text("select count(*) from pg_extension where extname = 'vector'"))).scalar()
        assert n == 1, "pgvector extension must be created by 0013"


async def test_tables_exist_with_rls_enabled_and_forced(engine) -> None:
    async with engine.connect() as conn:
        for t in _TABLES:
            row = (await conn.execute(text(
                "select relrowsecurity, relforcerowsecurity from pg_class where relname = :t"), {"t": t})).first()
            assert row is not None, f"table {t} is missing"
            assert row.relrowsecurity and row.relforcerowsecurity, f"{t} must ENABLE + FORCE row level security"
            pol = (await conn.execute(text(
                "select policyname from pg_policies where tablename = :t"), {"t": t})).scalars().all()
            assert f"{t}_ws_isolation" in pol, f"{t}_ws_isolation policy missing"


async def test_embedding_column_is_vector_1024(engine) -> None:
    async with engine.connect() as conn:
        typ = (await conn.execute(text(
            "select format_type(a.atttypid, a.atttypmod) from pg_attribute a "
            "where a.attrelid = 'kb_chunks'::regclass and a.attname = 'embedding'"))).scalar()
        assert typ == "vector(1024)"


async def test_hnsw_index_on_embedding(engine) -> None:
    async with engine.connect() as conn:
        defs = (await conn.execute(text(
            "select indexdef from pg_indexes where tablename = 'kb_chunks'"))).scalars().all()
        assert any("hnsw" in d and "vector_cosine_ops" in d for d in defs), f"no HNSW cosine index: {defs}"


async def test_kb_last_ingested_at_nullable_timestamptz(engine) -> None:
    async with engine.connect() as conn:
        row = await _column(conn, "knowledge_bases", "last_ingested_at")
        assert row is not None, "knowledge_bases.last_ingested_at is missing"
        assert row.data_type == "timestamp with time zone" and row.is_nullable == "YES"


async def test_dedup_key_nullable_text(engine) -> None:
    async with engine.connect() as conn:
        row = await _column(conn, "kb_documents", "dedup_key")
        assert row is not None and row.data_type == "text" and row.is_nullable == "YES"


# ------------------------------------------------------------------------------------------------- RLS


async def test_unset_guc_reads_zero_rows_fail_closed(engine) -> None:
    async with engine.connect() as conn:
        trans = await conn.begin()
        try:
            ws = await mk_ws(conn, f"m13-unset-{uuid.uuid4().hex[:6]}")
            await _seed_doc(conn, ws)
            await conn.execute(text("select set_config('app.workspace_id', '', true)"))
            for t in _TABLES:
                n = (await conn.execute(text(f"select count(*) from {t}"))).scalar()
                assert n == 0, f"{t}: unset tenant GUC must read zero rows, got {n}"
        finally:
            await trans.rollback()


async def test_workspace_a_cannot_read_workspace_b(engine) -> None:
    async with engine.connect() as conn:
        trans = await conn.begin()
        try:
            ws_a = await mk_ws(conn, f"m13-a-{uuid.uuid4().hex[:6]}")
            ws_b = await mk_ws(conn, f"m13-b-{uuid.uuid4().hex[:6]}")
            await _seed_doc(conn, ws_b)
            await set_ws(conn, ws_a)
            for t in _TABLES:
                n = (await conn.execute(text(f"select count(*) from {t} where workspace_id = :b"),
                                        {"b": str(ws_b)})).scalar()
                assert n == 0, f"{t}: ws A read {n} rows of ws B"
        finally:
            await trans.rollback()


# ------------------------------------------------------------------------------------------------- constraints


@pytest.mark.parametrize(
    "sql",
    [
        "update kb_documents set status = 'bogus' where id = :d",
        "update kb_documents set media_type = 'application/zip' where id = :d",
        "update kb_documents set size_bytes = 0 where id = :d",
        "update ingestion_jobs set kind = 'bogus' where document_id = :d",
        "update ingestion_jobs set status = 'bogus' where document_id = :d",
    ],
)
async def test_check_constraints_reject_bad_values(engine, sql) -> None:
    async with engine.connect() as conn:
        trans = await conn.begin()
        try:
            ws = await mk_ws(conn, f"m13-ck-{uuid.uuid4().hex[:6]}")
            _, doc = await _seed_doc(conn, ws)
            with pytest.raises(IntegrityError):
                await conn.execute(text(sql), {"d": str(doc)})
        finally:
            await trans.rollback()


async def test_unique_document_name_per_kb(engine) -> None:
    async with engine.connect() as conn:
        trans = await conn.begin()
        try:
            ws = await mk_ws(conn, f"m13-uq-{uuid.uuid4().hex[:6]}")
            kb, _ = await _seed_doc(conn, ws, name="same.txt")
            with pytest.raises(IntegrityError):
                await conn.execute(text(
                    "insert into kb_documents (workspace_id, kb_id, name, media_type, size_bytes, content_hash, "
                    "storage_key, status, created_by) values (:w, :k, 'same.txt', 'text/plain', 1, 'h', 'k', "
                    "'queued', 't')"), {"w": str(ws), "k": str(kb)})
        finally:
            await trans.rollback()


async def test_one_active_job_per_document(engine) -> None:
    """H3: the DB enforces at most one queued/processing job per document; finished jobs don't count."""
    async with engine.connect() as conn:
        trans = await conn.begin()
        try:
            ws = await mk_ws(conn, f"m13-one-{uuid.uuid4().hex[:6]}")
            kb, doc = await _seed_doc(conn, ws)   # seeds one 'ready' job
            ins = text("insert into ingestion_jobs (workspace_id, kb_id, document_id, kind, status, created_by) "
                       "values (:w, :k, :d, 'retry', :st, 't')")
            args = {"w": str(ws), "k": str(kb), "d": str(doc)}
            await conn.execute(ins, {**args, "st": "ready"})       # a second finished job is fine
            await conn.execute(ins, {**args, "st": "queued"})      # first active job is fine
            with pytest.raises(IntegrityError) as ei:
                async with conn.begin_nested():
                    await conn.execute(ins, {**args, "st": "processing"})
            assert "ingestion_jobs_one_active_per_doc" in str(ei.value)
        finally:
            await trans.rollback()


async def test_document_delete_cascades_chunks_and_jobs(engine) -> None:
    async with engine.connect() as conn:
        trans = await conn.begin()
        try:
            ws = await mk_ws(conn, f"m13-cas-{uuid.uuid4().hex[:6]}")
            _, doc = await _seed_doc(conn, ws)
            await conn.execute(text("delete from kb_documents where id = :d"), {"d": str(doc)})
            for t in ("kb_chunks", "ingestion_jobs"):
                n = (await conn.execute(text(f"select count(*) from {t} where document_id = :d"),
                                        {"d": str(doc)})).scalar()
                assert n == 0, f"{t} rows survived their document's delete"
        finally:
            await trans.rollback()


# ------------------------------------------------------------------------------------------------- reaper fn


async def test_reaper_is_security_definer_with_narrow_grants(engine) -> None:
    async with engine.connect() as conn:
        row = (await conn.execute(text(
            "select p.prosecdef, p.proacl::text as acl from pg_proc p where p.proname = 'ingestion_reap_stale'"))).first()
        assert row is not None, "ingestion_reap_stale function is missing"
        assert row.prosecdef is True, "ingestion_reap_stale must be SECURITY DEFINER"
        # ACL entries look like 'ac_app=X/ac'; a PUBLIC grant renders as '=X/ac' (empty grantee).
        acl = row.acl or ""
        assert "ac_app=X" in acl, f"ac_app must have EXECUTE: {acl}"
        assert "{=X" not in acl and ",=X" not in acl, f"PUBLIC must not have EXECUTE: {acl}"
        can = (await conn.execute(text(
            "select has_function_privilege('ac_app', 'ingestion_reap_stale(interval)', 'EXECUTE')"))).scalar()
        assert can is True


async def test_app_role_cannot_disable_row_security(engine) -> None:
    """ac_app has no RLS bypass: with row_security=off a query on a forced-RLS table must ERROR, not leak."""
    async with engine.connect() as conn:
        trans = await conn.begin()
        try:
            await conn.execute(text("set local row_security = off"))
            with pytest.raises(DBAPIError) as ei:
                await conn.execute(text("select count(*) from kb_documents"))
            # Must be the RLS refusal itself — "relation does not exist" is NOT evidence of anything.
            assert "row-level security" in str(ei.value), f"expected an RLS refusal, got: {ei.value}"
        finally:
            await trans.rollback()


# ------------------------------------------------------------------------------------------------- roundtrip


def _alembic(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=_SERVICES, capture_output=True, text=True,
        env={**os.environ}, timeout=120,
    )


def test_downgrade_to_0012_and_back_roundtrips_clean() -> None:
    """Runs LAST in this module (pytest file order). Leaves the DB at head even on failure."""
    try:
        down = _alembic("downgrade", "0012")
        assert down.returncode == 0, f"downgrade 0013→0012 failed:\n{down.stderr[-2000:]}"
        cur = _alembic("current")
        assert "0012" in cur.stdout, f"expected 0012 after downgrade, got: {cur.stdout!r}"
    finally:
        up = _alembic("upgrade", "head")
    assert up.returncode == 0, f"upgrade 0012→head failed:\n{up.stderr[-2000:]}"
    assert "(head)" in _alembic("current").stdout   # back at head (was hardcoded "0013"; 025 added 0014 above it)
