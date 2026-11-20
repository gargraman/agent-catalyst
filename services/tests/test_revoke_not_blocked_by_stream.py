"""Feature 026 US3 L15 (live-found, 2026-11-17) — revoking a token must NOT wait for that token's open stream.

Found by the L15 live check: `caller_db` touched `api_tokens.last_used_at` INSIDE the session the streaming generator
holds open for the whole stream, so the row lock lived until the stream ended. `DELETE /tokens/{id}` (an UPDATE of the
same row) blocked behind it, and the deny-set was armed only after — the in-flight stream was never cut (SC-006).
The touch must commit on its own, before the long-lived stream session starts.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text

from api.db import tenant_session
from api.deps import caller_db
from api.models import ApiToken
from api.publish.token_auth import CallerCtx
from tests._api_helpers import client, dev_headers, seed_agent, seed_version, seed_workspace

pytestmark = pytest.mark.integration


async def _token(ws: str, aid: str) -> str:
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/tokens", json={"name": "stream"},
                         headers=dev_headers(workspace=ws, claims={ws: "publisher"}))
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_revoke_is_not_blocked_by_an_open_published_stream_session():
    ws = await seed_workspace()
    aid = await seed_agent(ws)
    await seed_version(ws, aid)
    tid = await _token(ws, aid)
    caller = CallerCtx(workspace_id=ws, token_id=tid, agent_id=aid, subject=f"token:{tid}", teams=())

    gen = caller_db(caller)
    await gen.__anext__()                       # the stream's session is now open (as during a long SSE response)
    try:
        async with tenant_session(ws) as s:     # the revoke, from another request
            await s.execute(text("SET LOCAL lock_timeout = '1s'"))
            await s.execute(text("UPDATE api_tokens SET revoked_at = now() WHERE id = :i"), {"i": tid})
            row = (await s.execute(select(ApiToken).where(ApiToken.id == uuid.UUID(tid)))).scalar_one()
            assert row.last_used_at is not None  # the touch is committed and visible while the stream is still open
    finally:
        await gen.aclose()
