"""Migration 0016 — core loop (feature 026; data-model.md, tasks T003).

  * Round-trip: head → 0015 → head is clean; after the downgrade no eval_* / audit_logs table and none of the new
    agents / agent_versions columns remain.
  * Column shapes, CHECKs, UNIQUEs, the eval_baselines PK, the cascade / restrict rules and the indexes.
  * FK guards: eval_runs.(version_id, agent_id) → agent_versions(id, agent_id) refuses another agent's version; the
    composite (workspace_id, …) FKs refuse a cross-workspace reference (RI bypasses RLS — 025 G8).
  * Trigger `eval_baselines_run_check` refuses a failed run and a run of another suite.
  * RLS ENABLE + FORCE + isolation on all 6 new tables, as `ac_app` under another `app.workspace_id`.
  * audit_logs is append-only for ac_app: UPDATE / DELETE → SQLSTATE 42501.
Structure checks run as the owner (sync); behaviour checks run as `ac_app` (async, RLS applies).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from api.settings import get_settings

pytestmark = pytest.mark.integration

_SERVICES = Path(__file__).resolve().parents[1]
_NEW_TABLES = ("eval_suites", "eval_cases", "eval_runs", "eval_results", "eval_baselines", "audit_logs")
_NEW_COLS = (("agents", "description"), ("agents", "draft"), ("agents", "status_before_archive"),
             ("agent_versions", "created_by"))
_MC = json.dumps({"provider": "openai", "model": "gpt-4o-mini", "params": {}})
_RUBRIC = json.dumps([{"name": "accuracy", "weight": 1.0}])
_POLICY = json.dumps({"min_cases": 3})


def _alembic(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=_SERVICES, capture_output=True, text=True,
                          env={**os.environ}, timeout=180)


def _owner():
    return create_engine(get_settings().database_sync_url)


def _tables(c) -> set[str]:
    return set(c.execute(text("select tablename from pg_tables where schemaname = 'public'")).scalars().all())


def _cols(c) -> set[tuple[str, str]]:
    return {(r.table_name, r.column_name) for r in c.execute(text(
        "select table_name, column_name from information_schema.columns where table_schema = 'public'"))}


def _sqlstate(exc: DBAPIError) -> str | None:
    return getattr(exc.orig, "sqlstate", None)


# --------------------------------------------------------------------------------------------------------------------
# Round-trip
# --------------------------------------------------------------------------------------------------------------------
def test_round_trip_head_0015_head() -> None:
    eng = _owner()
    try:
        up = _alembic("upgrade", "head")
        assert up.returncode == 0, up.stderr[-3000:]
        with eng.connect() as c:
            assert c.execute(text("select version_num from alembic_version")).scalar() == "0016"
            assert set(_NEW_TABLES) <= _tables(c)
            assert set(_NEW_COLS) <= _cols(c)
        down = _alembic("downgrade", "0015")
        assert down.returncode == 0, down.stderr[-3000:]
        with eng.connect() as c:
            assert not (set(_NEW_TABLES) & _tables(c)), "0016 tables survived the downgrade"
            assert not (set(_NEW_COLS) & _cols(c)), "0016 columns survived the downgrade"
            funcs = set(c.execute(text("select proname from pg_proc")).scalars().all())
            assert "eval_baselines_run_check" not in funcs
        up2 = _alembic("upgrade", "head")
        assert up2.returncode == 0, up2.stderr[-3000:]
    finally:
        _alembic("upgrade", "head")
        eng.dispose()


# --------------------------------------------------------------------------------------------------------------------
# Structure (owner, information_schema / pg_catalog)
# --------------------------------------------------------------------------------------------------------------------
def _coldef(c, table: str, col: str):
    return c.execute(text(
        "select data_type, udt_name, is_nullable, column_default from information_schema.columns "
        "where table_schema='public' and table_name=:t and column_name=:c"), {"t": table, "c": col}).first()


def test_changed_columns() -> None:
    with _owner().connect() as c:
        d = _coldef(c, "agents", "description")
        assert d is not None and d.data_type == "text" and d.is_nullable == "NO" and "''" in (d.column_default or "")
        d = _coldef(c, "agents", "draft")
        assert d is not None and d.data_type == "jsonb" and d.is_nullable == "YES"
        d = _coldef(c, "agents", "status_before_archive")
        assert d is not None and d.udt_name == "agent_status" and d.is_nullable == "YES"
        d = _coldef(c, "agent_versions", "created_by")
        assert d is not None and d.data_type == "text" and d.is_nullable == "YES"


def _checkdefs(c, table: str) -> list[str]:
    return list(c.execute(text("select pg_get_constraintdef(oid) from pg_constraint "
                               "where conrelid = cast(:t as regclass) and contype = 'c'"), {"t": table}).scalars())


def test_check_constraints() -> None:
    with _owner().connect() as c:
        runs = " ".join(_checkdefs(c, "eval_runs"))
        for v in ("'completed'", "'failed'", "'pass'", "'fail'", "'low_confidence'", "'automatic'", "'frozen'"):
            assert v in runs, f"eval_runs CHECK missing {v}: {runs}"
        cases = " ".join(_checkdefs(c, "eval_cases"))
        assert "jsonb_array_length" in cases and "8" in cases, cases


def test_uniques_and_baseline_pk() -> None:
    with _owner().connect() as c:
        defs = {r.rel: r.d for r in c.execute(text(
            "select conrelid::regclass::text || ':' || pg_get_constraintdef(oid) as d, "
            "conrelid::regclass::text as rel from pg_constraint where contype in ('u','p') "
            "and conrelid::regclass::text in ('eval_suites','eval_results','eval_baselines')"))} or {}
        alld = [r[0] for r in c.execute(text(
            "select conrelid::regclass::text || ':' || pg_get_constraintdef(oid) from pg_constraint "
            "where contype in ('u','p')"))]
        assert "eval_suites:UNIQUE (agent_id, name)" in alld
        assert "eval_results:UNIQUE (eval_run_id, case_id)" in alld
        assert "eval_baselines:PRIMARY KEY (suite_id)" in alld
        assert defs  # tables exist


def _fk(c, table: str, ref: str) -> list[str]:
    return list(c.execute(text(
        "select pg_get_constraintdef(oid) from pg_constraint where contype='f' "
        "and conrelid = cast(:t as regclass) and confrelid = cast(:r as regclass)"), {"t": table, "r": ref}).scalars())


def test_cascades_and_restricts() -> None:
    with _owner().connect() as c:
        assert any("ON DELETE CASCADE" in d for d in _fk(c, "eval_cases", "eval_suites"))
        assert any("ON DELETE CASCADE" in d for d in _fk(c, "eval_results", "eval_runs"))
        assert any("ON DELETE CASCADE" in d for d in _fk(c, "eval_baselines", "eval_suites"))
        for d in _fk(c, "eval_runs", "eval_suites"):
            assert "CASCADE" not in d, d
        assert _fk(c, "eval_runs", "eval_suites")
        self_ref = _fk(c, "eval_runs", "eval_runs")
        assert self_ref and all("CASCADE" not in d and "SET NULL" not in d for d in self_ref)
        base_run = _fk(c, "eval_baselines", "eval_runs")
        assert base_run and all("CASCADE" not in d for d in base_run)
        ver = _fk(c, "eval_runs", "agent_versions")
        assert any("(version_id, agent_id)" in d and "(id, agent_id)" in d for d in ver), ver
        # composite workspace FKs (025 G8): every cross-table ref carries workspace_id
        assert any("workspace_id" in d for d in _fk(c, "eval_suites", "agents"))
        assert any("workspace_id" in d for d in _fk(c, "eval_runs", "agents"))
        assert any("workspace_id" in d for d in _fk(c, "eval_cases", "eval_suites"))


def test_indexes() -> None:
    with _owner().connect() as c:
        idx = " | ".join(c.execute(text(
            "select indexdef from pg_indexes where tablename in ('eval_runs','audit_logs')")).scalars())
        assert "(agent_id, finished_at DESC)" in idx
        assert "(suite_id, version_id, finished_at DESC)" in idx and "'completed'" in idx
        assert "eval_runs" in idx and "(workspace_id, finished_at DESC)" in idx
        assert "(workspace_id, created_at DESC)" in idx


def test_rls_enabled_and_forced_on_new_tables() -> None:
    with _owner().connect() as c:
        rows = {r.relname: (r.relrowsecurity, r.relforcerowsecurity) for r in c.execute(text(
            "select relname, relrowsecurity, relforcerowsecurity from pg_class where relname = any(:t)"),
            {"t": list(_NEW_TABLES)})}
        for t in _NEW_TABLES:
            assert rows.get(t) == (True, True), f"{t}: RLS not enabled+forced ({rows.get(t)})"
        pols = set(c.execute(text("select policyname from pg_policies")).scalars())
        for t in _NEW_TABLES:
            assert f"{t}_ws_isolation" in pols


# --------------------------------------------------------------------------------------------------------------------
# Behaviour as ac_app (RLS applies)
# --------------------------------------------------------------------------------------------------------------------
async def _set_ws(c, ws) -> None:
    await c.execute(text("select set_config('app.workspace_id', :w, true)"), {"w": str(ws)})


async def _world(engine) -> dict:
    """Two workspaces; in each an agent with v1 + v2, a suite with a case, and a completed + failed run."""
    out = {}
    for side in ("a", "b"):
        async with engine.begin() as c:
            ws = (await c.execute(text("insert into workspaces (name) values (:n) returning id"),
                                  {"n": f"m16-{side}-{uuid.uuid4().hex[:6]}"})).scalar_one()
            await _set_ws(c, ws)
            q = lambda sql, **p: c.execute(text(sql), {"w": ws, **p})  # noqa: E731
            agent = (await q("insert into agents (workspace_id, name) values (:w, 'a') returning id")).scalar_one()
            other = (await q("insert into agents (workspace_id, name) values (:w, 'o') returning id")).scalar_one()
            vids = []
            for ag, lbl in ((agent, "v1"), (agent, "v2"), (other, "v1")):
                vids.append((await q(
                    "insert into agent_versions (workspace_id, agent_id, label, system_prompt, prompt_hash, "
                    "snapshot_hash, model_config) values (:w, :a, :l, 'p', 'ph', 'sh', cast(:mc as jsonb)) returning id",
                    a=ag, l=lbl, mc=_MC)).scalar_one())
            suite = (await q("insert into eval_suites (workspace_id, agent_id, name) values (:w, :a, 's1') returning id",
                             a=agent)).scalar_one()
            suite2 = (await q("insert into eval_suites (workspace_id, agent_id, name) values (:w, :a, 's2') returning id",
                              a=agent)).scalar_one()
            case = (await q("insert into eval_cases (workspace_id, suite_id, input, rubric, content_hash) "
                            "values (:w, :s, cast(:i as jsonb), cast(:r as jsonb), 'h') returning id",
                            s=suite, r=_RUBRIC, i='{"m": 1}')).scalar_one()
            run = await _run(c, ws, agent, suite, vids[0], "completed")
            failed = await _run(c, ws, agent, suite, vids[0], "failed")
            run_s2 = await _run(c, ws, agent, suite2, vids[0], "completed")
        out[side] = {"ws": str(ws), "agent": str(agent), "other": str(other), "v1": str(vids[0]), "v2": str(vids[1]),
                     "other_v1": str(vids[2]), "suite": str(suite), "suite2": str(suite2), "case": str(case),
                     "run": str(run), "failed": str(failed), "run_s2": str(run_s2)}
    return out


async def _run(c, ws, agent, suite, vid, status: str, **over):
    verdict = None if status == "failed" else "low_confidence"
    reason = "timeout" if status == "failed" else "no_baseline"
    params = {"w": ws, "a": agent, "s": suite, "v": vid, "st": status, "vd": verdict, "rc": reason, "p": _POLICY}
    params.update(over)
    return (await c.execute(text(
        "insert into eval_runs (workspace_id, agent_id, suite_id, version_id, status, verdict, reason_code, reason, "
        "compared_cases, new_cases, dropped_cases, total_cases, policy, created_by, started_at, finished_at) "
        "values (:w, :a, :s, :v, :st, :vd, :rc, 'r', 0, 1, 0, 1, cast(:p as jsonb), 'tester', now(), now()) "
        "returning id"), params)).scalar_one()


async def test_version_fk_refuses_other_agents_version(engine) -> None:
    w = await _world(engine)
    a = w["a"]
    with pytest.raises(IntegrityError) as ei:
        async with engine.begin() as c:
            await _set_ws(c, a["ws"])
            await _run(c, a["ws"], a["agent"], a["suite"], a["other_v1"], "completed")
    assert _sqlstate(ei.value) == "23503"


async def test_composite_ws_fk_refuses_cross_workspace_suite(engine) -> None:
    w = await _world(engine)
    a, b = w["a"], w["b"]
    with pytest.raises(IntegrityError) as ei:
        async with engine.begin() as c:
            await _set_ws(c, a["ws"])
            await c.execute(text("insert into eval_cases (workspace_id, suite_id, input, rubric, content_hash) "
                                 "values (:w, :s, '{}'::jsonb, cast(:r as jsonb), 'h')"),
                            {"w": a["ws"], "s": b["suite"], "r": _RUBRIC})
    assert _sqlstate(ei.value) == "23503"
    with pytest.raises(IntegrityError) as ei2:
        async with engine.begin() as c:
            await _set_ws(c, a["ws"])
            await c.execute(text("insert into eval_suites (workspace_id, agent_id, name) values (:w, :a, 'x')"),
                            {"w": a["ws"], "a": b["agent"]})
    assert _sqlstate(ei2.value) == "23503"


async def test_rubric_dimension_check(engine) -> None:
    w = await _world(engine)
    a = w["a"]
    for rubric in ([], [{"name": f"d{i}", "weight": 1} for i in range(9)]):
        with pytest.raises(IntegrityError) as ei:
            async with engine.begin() as c:
                await _set_ws(c, a["ws"])
                await c.execute(text("insert into eval_cases (workspace_id, suite_id, input, rubric, content_hash) "
                                     "values (:w, :s, '{}'::jsonb, cast(:r as jsonb), 'h')"),
                                {"w": a["ws"], "s": a["suite"], "r": json.dumps(rubric)})
        assert _sqlstate(ei.value) == "23514"


async def test_status_and_verdict_checks(engine) -> None:
    w = await _world(engine)
    a = w["a"]
    for over in ({"st": "running"}, {"vd": "great"}):
        with pytest.raises(IntegrityError) as ei:
            async with engine.begin() as c:
                await _set_ws(c, a["ws"])
                await _run(c, a["ws"], a["agent"], a["suite"], a["v1"], "completed", **over)
        assert _sqlstate(ei.value) == "23514"


async def test_unique_suite_name_and_result_case(engine) -> None:
    w = await _world(engine)
    a = w["a"]
    with pytest.raises(IntegrityError) as ei:
        async with engine.begin() as c:
            await _set_ws(c, a["ws"])
            await c.execute(text("insert into eval_suites (workspace_id, agent_id, name) values (:w, :a, 's1')"),
                            {"w": a["ws"], "a": a["agent"]})
    assert _sqlstate(ei.value) == "23505"
    ins = ("insert into eval_results (workspace_id, eval_run_id, case_id, case_hash, passed_deterministic, "
           "judge_low_confidence, hard_failure, candidate_output, in_comparison) "
           "values (:w, :r, :c, 'h', true, false, false, 'out', true)")
    with pytest.raises(IntegrityError) as ei2:
        async with engine.begin() as c:
            await _set_ws(c, a["ws"])
            await c.execute(text(ins), {"w": a["ws"], "r": a["run"], "c": a["case"]})
            await c.execute(text(ins), {"w": a["ws"], "r": a["run"], "c": a["case"]})
    assert _sqlstate(ei2.value) == "23505"


async def test_cascade_and_restrict_behaviour(engine) -> None:
    w = await _world(engine)
    a = w["a"]
    # suite with runs cannot be deleted (RESTRICT)
    with pytest.raises(IntegrityError) as ei:
        async with engine.begin() as c:
            await _set_ws(c, a["ws"])
            await c.execute(text("delete from eval_suites where id = :s"), {"s": a["suite"]})
    assert _sqlstate(ei.value) == "23503"
    # a suite without runs deletes and cascades its cases
    async with engine.begin() as c:
        await _set_ws(c, a["ws"])
        s3 = (await c.execute(text("insert into eval_suites (workspace_id, agent_id, name) values (:w, :a, 's3') "
                                   "returning id"), {"w": a["ws"], "a": a["agent"]})).scalar_one()
        await c.execute(text("insert into eval_cases (workspace_id, suite_id, input, rubric, content_hash) "
                             "values (:w, :s, '{}'::jsonb, cast(:r as jsonb), 'h')"),
                        {"w": a["ws"], "s": s3, "r": _RUBRIC})
        await c.execute(text("delete from eval_suites where id = :s"), {"s": s3})
        left = (await c.execute(text("select count(*) from eval_cases where suite_id = :s"), {"s": s3})).scalar()
    assert left == 0


async def test_baseline_trigger_refuses_failed_and_other_suite_run(engine) -> None:
    w = await _world(engine)
    a = w["a"]
    ins = ("insert into eval_baselines (suite_id, workspace_id, eval_run_id, frozen_by, frozen_at) "
           "values (:s, :w, :r, 'admin', now())")
    for run in (a["failed"], a["run_s2"]):
        with pytest.raises(DBAPIError) as ei:
            async with engine.begin() as c:
                await _set_ws(c, a["ws"])
                await c.execute(text(ins), {"s": a["suite"], "w": a["ws"], "r": run})
        assert "eval_baselines_run_check" in str(ei.value) or _sqlstate(ei.value) in ("23514", "P0001")
    async with engine.begin() as c:   # a completed run of the same suite is accepted
        await _set_ws(c, a["ws"])
        await c.execute(text(ins), {"s": a["suite"], "w": a["ws"], "r": a["run"]})


async def test_rls_isolation_on_new_tables(engine) -> None:
    w = await _world(engine)
    a, b = w["a"], w["b"]
    async with engine.begin() as c:
        await _set_ws(c, a["ws"])
        await c.execute(text("insert into audit_logs (workspace_id, actor, action, target_type, target_id, meta) "
                             "values (:w, 'u', 'agent.publish', 'agent', :t, '{}'::jsonb)"),
                        {"w": a["ws"], "t": a["agent"]})
        await c.execute(text("insert into eval_results (workspace_id, eval_run_id, case_id, case_hash, "
                             "passed_deterministic, judge_low_confidence, hard_failure, candidate_output, "
                             "in_comparison) values (:w, :r, :c, 'h', true, false, false, 'o', true)"),
                        {"w": a["ws"], "r": a["run"], "c": a["case"]})
        await c.execute(text("insert into eval_baselines (suite_id, workspace_id, eval_run_id, frozen_by, frozen_at) "
                             "values (:s, :w, :r, 'admin', now())"), {"s": a["suite"], "w": a["ws"], "r": a["run"]})
    async with engine.begin() as c:
        await _set_ws(c, b["ws"])
        for t, col, val in (("eval_suites", "id", a["suite"]), ("eval_cases", "id", a["case"]),
                            ("eval_runs", "id", a["run"]), ("eval_results", "eval_run_id", a["run"]),
                            ("eval_baselines", "suite_id", a["suite"]), ("audit_logs", "workspace_id", a["ws"])):
            n = (await c.execute(text(f"select count(*) from {t} where {col} = :v"), {"v": val})).scalar()
            assert n == 0, f"{t} leaked across workspaces"
    # a write that claims another workspace is refused by WITH CHECK
    with pytest.raises(DBAPIError):
        async with engine.begin() as c:
            await _set_ws(c, b["ws"])
            await c.execute(text("insert into audit_logs (workspace_id, actor, action, meta) "
                                 "values (:w, 'u', 'agent.publish', '{}'::jsonb)"), {"w": a["ws"]})


async def test_audit_logs_append_only_for_app_role(engine) -> None:
    w = await _world(engine)
    a = w["a"]
    async with engine.begin() as c:
        await _set_ws(c, a["ws"])
        await c.execute(text("insert into audit_logs (workspace_id, actor, action, meta) "
                             "values (:w, 'u', 'agent.archive', '{}'::jsonb)"), {"w": a["ws"]})
    for stmt in ("update audit_logs set actor = 'x' where workspace_id = :w",
                 "delete from audit_logs where workspace_id = :w"):
        with pytest.raises(DBAPIError) as ei:
            async with engine.begin() as c:
                await _set_ws(c, a["ws"])
                await c.execute(text(stmt), {"w": a["ws"]})
        assert _sqlstate(ei.value) == "42501", stmt


# --- T011 smoke: the 026 test seeders drive the ORM mirror against the real 0016 schema --------------------------
async def test_026_seeders_smoke():
    from tests._api_helpers import (
        freeze, seed_agent, seed_eval_run, seed_suite, seed_version, seed_workspace, set_draft,
    )
    from api.db import tenant_session
    from api.models import Agent, EvalBaseline, EvalResult
    from sqlalchemy import select

    ws = await seed_workspace()
    a = await seed_agent(ws)
    v = await seed_version(ws, a)
    await set_draft(ws, a, system_prompt="draft prompt")
    await set_draft(ws, a, description="merged")
    suite, (c1,) = await seed_suite(ws, a, "smoke", cases=[{"input": {"message": "q"}, "rubric": [{"name": "r", "weight": 1}]}])
    ok = await seed_eval_run(ws, a, suite, v, results=[{"case_id": c1}])
    failed = await seed_eval_run(ws, a, suite, v, status="failed", reason_code="infra")
    await freeze(ws, suite, ok)
    with pytest.raises(Exception, match="eval_baselines_run_check"):
        await freeze(ws, suite, failed)                     # trigger: only a completed run may be the baseline
    async with tenant_session(ws) as s:
        agent = await s.get(Agent, uuid.UUID(a))
        assert agent.draft == {"system_prompt": "draft prompt", "description": "merged"}
        assert (await s.get(EvalBaseline, uuid.UUID(suite))).eval_run_id == uuid.UUID(ok)
        assert len((await s.scalars(select(EvalResult).where(EvalResult.eval_run_id == uuid.UUID(ok)))).all()) == 1
