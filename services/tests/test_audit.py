"""Feature 026 (T005) — `api.audit.record`: a minimal append-only audit writer (data-model `audit_logs`, FR-023).

  * one row, actor = principal subject, written in the CALLER's transaction (a rollback leaves 0 rows)
  * the action allow-list is closed: anything else → ValueError
  * `meta` holds ids only: a value that is not a UUID / string id / None → ValueError (no content in audit rows)
  * the row is invisible from another workspace (RLS)
Integration: needs the compose Postgres at head (0016).
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from api.db import tenant_session
from api.deps import Principal
from api.models import Workspace

pytestmark = pytest.mark.integration

_ACTIONS = ("baseline.freeze", "baseline.clear", "agent.publish", "agent.repoint", "agent.archive", "agent.restore")


async def _ws() -> uuid.UUID:
    async with tenant_session(uuid.uuid4()) as s:
        w = Workspace(name=f"audit-{uuid.uuid4().hex[:8]}")
        s.add(w)
        await s.flush()
        return w.id


async def _rows(ws) -> list:
    async with tenant_session(ws) as s:
        return list((await s.execute(text(
            "select actor, action, target_type, target_id, meta from audit_logs order by created_at"))).all())


def _audit():
    import api.audit as audit  # imported lazily so the Red failure is the missing module, reported per test
    return audit


async def test_record_inserts_one_row_with_actor_subject():
    ws = await _ws()
    target, frm, to = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with tenant_session(ws) as s:
        await _audit().record(s, Principal(subject="alice@example.com"), "agent.repoint", "agent", target,
                              {"from_version_id": frm, "to_version_id": str(to)})
    rows = await _rows(ws)
    assert len(rows) == 1
    r = rows[0]
    assert r.actor == "alice@example.com" and r.action == "agent.repoint" and r.target_type == "agent"
    assert r.target_id == target
    assert r.meta == {"from_version_id": str(frm), "to_version_id": str(to)}


async def test_rollback_leaves_no_row():
    ws = await _ws()
    with pytest.raises(RuntimeError):
        async with tenant_session(ws) as s:
            await _audit().record(s, Principal(subject="bob"), "agent.archive", "agent", uuid.uuid4(), {})
            raise RuntimeError("caller transaction fails after the audit write")
    assert await _rows(ws) == []


@pytest.mark.parametrize("action", _ACTIONS)
async def test_every_allowed_action_is_accepted(action):
    ws = await _ws()
    async with tenant_session(ws) as s:
        await _audit().record(s, Principal(subject="u"), action, "agent", uuid.uuid4(), {})
    assert [r.action for r in await _rows(ws)] == [action]


@pytest.mark.parametrize("action", ["agent.delete", "", "AGENT.PUBLISH", "baseline.freeze ", "token.mint"])
async def test_unknown_action_is_refused(action):
    ws = await _ws()
    async with tenant_session(ws) as s:
        with pytest.raises(ValueError):
            await _audit().record(s, Principal(subject="u"), action, "agent", uuid.uuid4(), {})
    assert await _rows(ws) == []


@pytest.mark.parametrize("meta", [
    {"prompt": "You are a helpful assistant that ..."},      # content, not an id
    {"n": 3},
    {"nested": {"id": str(uuid.uuid4())}},
    {"ids": [str(uuid.uuid4())]},
    {"flag": True},
])
async def test_meta_holds_ids_only(meta):
    ws = await _ws()
    async with tenant_session(ws) as s:
        with pytest.raises(ValueError):
            await _audit().record(s, Principal(subject="u"), "agent.publish", "agent", uuid.uuid4(), meta)
    assert await _rows(ws) == []


async def test_meta_accepts_uuid_str_id_and_none():
    ws = await _ws()
    rid = uuid.uuid4()
    async with tenant_session(ws) as s:
        await _audit().record(s, Principal(subject="u"), "baseline.freeze", "eval_suite", uuid.uuid4(),
                              {"previous_run_id": None, "new_run_id": rid})
    assert (await _rows(ws))[0].meta == {"previous_run_id": None, "new_run_id": str(rid)}


async def test_row_invisible_from_another_workspace():
    ws, other = await _ws(), await _ws()
    async with tenant_session(ws) as s:
        await _audit().record(s, Principal(subject="u"), "agent.publish", "agent", uuid.uuid4(), {})
    assert len(await _rows(ws)) == 1
    assert await _rows(other) == []
