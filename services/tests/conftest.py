"""Shared fixtures for the M0 infra-free unit tests.

``make_av`` builds a lightweight stand-in for a loaded ``AgentVersion`` ORM row with its
eager-loaded bindings — enough for ``compute_snapshot_hash`` to read, with NO database. The
real ORM ``AgentVersion`` + ``to_snapshot`` arrive in the integration batch (needs Postgres).
"""
from __future__ import annotations

import copy
from types import SimpleNamespace


def _tool_binding(**over):
    base = dict(
        id="bind-0001",            # per-row UUID — must NOT affect the hash (clone stability)
        kind="http",
        tool_id="tool-1",
        mcp_tool_id=None,
        subagent_config_id=None,
        schema_snapshot={"type": "object", "properties": {"q": {"type": "string"}}},
        config_snapshot={"url": "https://api.example.com/v1", "egress": ["api.example.com"]},
        risk_tier="read",
        requires_approval=False,
        auth_mode=None,
        mcp_scopes=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _retrieval_binding(**over):
    base = dict(
        kb_id="kb-1",
        kb=SimpleNamespace(embedding_model="text-embedding-3-small"),
        retrieval_config={"top_k": 5, "similarity_threshold": 0.2},
        injection="tool",
        kb_index_version=3,        # EXCLUDED from the hash (04 §9) — mutating it must NOT change it
    )
    base.update(over)
    return SimpleNamespace(**base)


def _skill_binding(**over):
    base = dict(skill_id="skill-1", skill_content_hash="sch-aaaa")
    base.update(over)
    return SimpleNamespace(**base)


def make_av(**over):
    """A baseline fake AgentVersion carrying one of each binding kind so every DRIFT_TRIGGER
    is exercisable. Deep-copy before mutating (the trigger tests do)."""
    base = dict(
        id="ver-0001",
        agent_id="agent-1",
        workspace_id="ws-1",
        label="v7",
        system_prompt="You are a helpful assistant.",
        prompt_hash="ph-0000",
        model_config_={
            "model": "claude-haiku-4-5",
            "provider": "anthropic",
            "api_version": "2026-01-01",
            "params": {"temperature": 0.2, "max_tokens": 1024},
        },
        guardrail_config={"input": [], "output": []},
        io_contract={"output_format": "text", "output_schema": None, "input_variables": []},
        memory_config={"session": True, "window": 10, "compaction": "summarize"},
        context_config={"limit_tokens": 8000, "truncation": "summarize"},
        profile_snapshot={"features": {"structured_output": True}, "fallback": {}},
        tool_bindings=[_tool_binding()],
        retrieval_bindings=[_retrieval_binding()],
        skill_bindings=[_skill_binding()],
    )
    base.update(over)
    return SimpleNamespace(**base)


def clone_av(av):
    return copy.deepcopy(av)


# --------------------------------------------------------------------------------------------------
# Integration fixtures (M0 integration batch) — require the local Postgres from deploy/compose.yaml.
# Tests marked @pytest.mark.integration SKIP cleanly when the DB is unreachable.
# --------------------------------------------------------------------------------------------------
import os  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

# Integration tests connect as the NON-superuser app role (09 §7) so RLS actually applies —
# the superuser `ac` bypasses RLS. Migrations still run as `ac` via Alembic's SYNC_URL.
AC_DB_URL = os.environ.get(
    "AC_DATABASE_URL", "postgresql+asyncpg://ac_app:ac_app_pw@localhost:5433/agent_catalyst"
)


@pytest_asyncio.fixture
async def engine():
    """A function-scoped async engine; skips the test if the DB can't be reached (so the unit
    suite stays green on a machine with no compose stack up)."""
    eng = create_async_engine(AC_DB_URL)
    try:
        async with eng.connect() as c:
            await c.execute(text("select 1"))
    except Exception as e:  # noqa: BLE001 — any connect failure → skip, not fail
        await eng.dispose()
        pytest.skip(f"integration DB unavailable: {e}")
    yield eng
    await eng.dispose()


async def set_ws(conn, ws) -> None:
    """Pin the tenant GUC for the current transaction (09 §7: SET LOCAL via set_config is_local=true)."""
    await conn.execute(text("select set_config('app.workspace_id', :w, true)"), {"w": str(ws)})


async def mk_ws(conn, name: str):
    r = await conn.execute(
        text("insert into workspaces (name) values (:n) returning id"), {"n": name}
    )
    return r.scalar_one()


async def mk_agent(conn, ws, name: str):
    r = await conn.execute(
        text("insert into agents (workspace_id, name, status) values (:w, :n, 'draft') returning id"),
        {"w": str(ws), "n": name},
    )
    return r.scalar_one()


async def mk_version(conn, agent_id, ws, label: str = "v1"):
    r = await conn.execute(
        text(
            """insert into agent_versions
                 (agent_id, workspace_id, label, system_prompt, prompt_hash, snapshot_hash, model_config)
               values (:a, :w, :l, 'sys', 'ph', 'sh', '{}'::jsonb) returning id"""
        ),
        {"a": str(agent_id), "w": str(ws), "l": label},
    )
    return r.scalar_one()
