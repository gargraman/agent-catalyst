"""Red suite — 018 caller-token plane (DB/integration tier): mint/list/revoke routes, `resolve_caller`,
the published `/v1` invoke plane, admin deny-set revocation, in-flight stream cut, and api_tokens RLS.

Constitution P9: written FIRST, must FAIL on behavior (the routes + resolve_caller do not exist yet). Uses the
RLS+ASGI integration harness; the gateway is mocked (no live/paid call); `fakeredis` stands in for Redis where
the deny-set is exercised. The one-time secret is shown once at mint and only its sha256+prefix persist
(FR-010/011). Test bearer values are synthesized at runtime (never literal) — they are not real credentials.
"""
from __future__ import annotations

import datetime as dt
import uuid

import fakeredis.aioredis
import pytest
from sqlalchemy import select

from api import redis_client
from api.db import tenant_session
from api.models import ApiToken
from runtime.providers import gateway
from tests._api_helpers import (
    client, dev_headers, parse_sse, seed_agent, seed_api_token, seed_version, seed_workspace,
)
from tests._invoke_helpers import make_gateway, stop_ev, text_ev, usage_ev

pytestmark = pytest.mark.integration


def mk_bearer(tag: str = "t") -> str:
    """A synthetic caller-token bearer (NOT a real credential) — built at runtime so no literal secret lives
    in source (and so each test gets a unique value)."""
    return "ac_" + "live_" + tag + "_" + uuid.uuid4().hex[:10]


@pytest.fixture
def fake_redis(monkeypatch):
    """In-memory Redis for the in-process ASGI app so the deny-set actually stores/consults state."""
    c = fakeredis.aioredis.FakeRedis()
    monkeypatch.setattr(redis_client, "get_redis", lambda: c)
    return c


async def _seed():
    ws = await seed_workspace()
    aid = await seed_agent(ws)                         # workspace-default access policy → executer can USE
    vid = await seed_version(ws, aid)
    return ws, aid, vid


def _auth(bearer: str) -> dict:
    return {"Authorization": "Bearer " + bearer}


def _ok_gateway(monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("hi"), usage_ev(), stop_ev()]))


# ================================ mint / list / RBAC (FR-010/011/016) ==============================
async def test_mint_shows_secret_once_persists_hash():
    ws, aid, _ = await _seed()
    async with client() as c:
        pub = dev_headers(workspace=ws, subject="pub", claims={ws: "publisher"})
        r = await c.post(f"/api/v1/agents/{aid}/tokens", headers=pub, json={"name": "ci-token"})
        assert r.status_code == 201, r.text
        out = r.json()
        shown = out["secret"]                           # full secret — returned EXACTLY once
        assert shown and out["prefix"] and out["id"] and out["name"] == "ci-token"
        lst = await c.get(f"/api/v1/agents/{aid}/tokens", headers=pub)
        assert lst.status_code == 200
        items = lst.json()
        assert len(items) == 1 and "secret" not in items[0]
        assert items[0]["prefix"] == out["prefix"]
        assert shown not in lst.text                     # never re-echoed
    async with tenant_session(ws) as s:
        row = (await s.execute(select(ApiToken))).scalar_one()
        assert row.token_hash != shown and shown not in row.token_hash
        assert len(row.token_hash) == 64                # sha256 hex
        assert row.prefix and shown not in (row.prefix or "")


async def test_rbac_builder_cannot_mint_or_revoke():
    ws, aid, _ = await _seed()
    async with client() as c:
        builder = dev_headers(workspace=ws, subject="b", claims={ws: "builder"})
        r = await c.post(f"/api/v1/agents/{aid}/tokens", headers=builder, json={"name": "x"})
        assert r.status_code == 403 and r.json()["error"]["code"] == "access.denied"
        tid = await seed_api_token(ws, aid, secret=mk_bearer("rbac"))
        d = await c.delete(f"/api/v1/tokens/{tid}", headers=builder)
        assert d.status_code == 403


# ================================ published invoke via resolve_caller ==============================
async def test_published_invoke_with_valid_token_streams(monkeypatch):
    _ok_gateway(monkeypatch)
    ws, aid, _ = await _seed()
    bearer = mk_bearer("valid")
    await seed_api_token(ws, aid, secret=bearer)
    async with client() as c:
        r = await c.post(f"/v1/agents/{aid}/invoke", headers=_auth(bearer), json={"inputs": {"message": "hi"}})
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/event-stream")
        names = [n for n, _ in parse_sse(r.text)]
        assert names[0] == "run_start" and names[-1] == "done" and "text_delta" in names


async def test_published_and_console_share_code_path(monkeypatch):
    _ok_gateway(monkeypatch)
    ws, aid, _ = await _seed()
    bearer = mk_bearer("shared")
    await seed_api_token(ws, aid, secret=bearer)
    async with client() as c:
        pub = dev_headers(workspace=ws, subject="pub", claims={ws: "publisher"})
        console = await c.post(f"/api/v1/agents/{aid}/invoke", headers=pub, json={"inputs": {"message": "hi"}})
        published = await c.post(f"/v1/agents/{aid}/invoke", headers=_auth(bearer), json={"inputs": {"message": "hi"}})
    assert console.status_code == 200 and published.status_code == 200
    assert [n for n, _ in parse_sse(console.text)] == [n for n, _ in parse_sse(published.text)]


async def test_unknown_token_rejected():
    ws, aid, _ = await _seed()
    async with client() as c:
        r = await c.post(f"/v1/agents/{aid}/invoke", headers=_auth(mk_bearer("nope")),
                         json={"inputs": {"message": "hi"}})
    assert r.status_code == 401


async def test_revoke_rejects_next_invoke(monkeypatch):
    _ok_gateway(monkeypatch)
    ws, aid, _ = await _seed()
    async with client() as c:
        pub = dev_headers(workspace=ws, subject="pub", claims={ws: "publisher"})
        out = (await c.post(f"/api/v1/agents/{aid}/tokens", headers=pub, json={"name": "revoke-me"})).json()
        bearer, tid = out["secret"], out["id"]
        assert (await c.post(f"/v1/agents/{aid}/invoke", headers=_auth(bearer),
                             json={"inputs": {"message": "hi"}})).status_code == 200
        assert (await c.delete(f"/api/v1/tokens/{tid}", headers=pub)).status_code == 204
        after = await c.post(f"/v1/agents/{aid}/invoke", headers=_auth(bearer), json={"inputs": {"message": "hi"}})
        assert after.status_code == 401


async def test_expired_token_rejected():
    ws, aid, _ = await _seed()
    now = dt.datetime.now(dt.timezone.utc)
    bearer = mk_bearer("expired")
    # minted 2h ago with a 1h life → expires_at (1h ago) > created_at (2h ago), yet already expired
    await seed_api_token(ws, aid, secret=bearer, created_at=now - dt.timedelta(hours=2),
                         expires_at=now - dt.timedelta(hours=1))
    async with client() as c:
        r = await c.post(f"/v1/agents/{aid}/invoke", headers=_auth(bearer), json={"inputs": {"message": "hi"}})
    assert r.status_code == 401


# ================================ admin deny-set revocation (FR-013) ==============================
async def test_revoke_user_cuts_caller_token(fake_redis, monkeypatch):
    _ok_gateway(monkeypatch)
    ws, aid, _ = await _seed()
    bearer = mk_bearer("denytok")
    tid = await seed_api_token(ws, aid, secret=bearer)
    async with client() as c:
        assert (await c.post(f"/v1/agents/{aid}/invoke", headers=_auth(bearer),
                             json={"inputs": {"message": "hi"}})).status_code == 200
        adm = dev_headers(workspace=ws, subject="adm", claims={ws: "admin"})
        rv = await c.post("/api/v1/org/revoke-user", headers=adm, json={"token_id": tid})
        assert rv.status_code == 204, rv.text
        after = await c.post(f"/v1/agents/{aid}/invoke", headers=_auth(bearer), json={"inputs": {"message": "hi"}})
        assert after.status_code == 401                 # deny-set cut on next call, not next login


async def test_revoke_user_cuts_console_session(fake_redis):
    ws, aid, _ = await _seed()
    async with client() as c:
        adm = dev_headers(workspace=ws, subject="adm", claims={ws: "admin"})
        pub = dev_headers(workspace=ws, subject="pub", claims={ws: "publisher"})
        assert (await c.get(f"/api/v1/agents/{aid}/tokens", headers=pub)).status_code == 200
        assert (await c.post("/api/v1/org/revoke-user", headers=adm, json={"subject": "victim"})).status_code == 204
        victim = dev_headers(workspace=ws, subject="victim", claims={ws: "publisher"})
        after = await c.get(f"/api/v1/agents/{aid}/tokens", headers=victim)
        assert after.status_code == 401                 # active console session cut on next request


async def test_revoke_user_requires_admin(fake_redis):
    ws, _, _ = await _seed()
    async with client() as c:
        builder = dev_headers(workspace=ws, subject="b", claims={ws: "builder"})
        r = await c.post("/api/v1/org/revoke-user", headers=builder, json={"subject": "x"})
        assert r.status_code == 403


async def test_denyset_redis_down_fails_closed_on_invoke(monkeypatch):
    """A configured-but-unreachable Redis must fail the caller CLOSED (401), not admit it (FR-024b)."""
    class _BoomPipe:
        def execute(self):
            async def _raise():
                raise ConnectionError("redis down")
            return _raise()
        def __getattr__(self, n):
            return lambda *a, **k: self

    class _Boom:
        def pipeline(self, *a, **k):
            return _BoomPipe()
        def __getattr__(self, n):
            async def _raise(*a, **k):
                raise ConnectionError("redis down")
            return _raise
    monkeypatch.setattr(redis_client, "get_redis", lambda: _Boom())
    _ok_gateway(monkeypatch)
    ws, aid, _ = await _seed()
    bearer = mk_bearer("failclosed")
    await seed_api_token(ws, aid, secret=bearer)
    async with client() as c:
        r = await c.post(f"/v1/agents/{aid}/invoke", headers=_auth(bearer), json={"inputs": {"message": "hi"}})
    assert r.status_code == 401                          # fail closed, not allowed through unchecked


# ================================ in-flight stream cut (FR-012, D3) ================================
async def test_inflight_stream_cut_on_revoke(monkeypatch):
    """Token valid at auth, revoked mid-stream → the stream re-check cuts it with an error frame."""
    from api.auth import denyset
    from api.settings import get_settings

    monkeypatch.setenv("AC_DENYSET_STREAM_RECHECK_S", "0")   # re-check every frame (deterministic)
    get_settings.cache_clear()

    calls = {"n": 0}
    async def _staged_is_denied(subject, token_id):
        calls["n"] += 1
        return calls["n"] > 1                            # False at auth (1st call), True on each stream re-check
    monkeypatch.setattr(denyset, "is_denied", _staged_is_denied)
    monkeypatch.setattr(gateway, "stream",
                        make_gateway([text_ev("a"), text_ev("b"), text_ev("c"), usage_ev(), stop_ev()]))

    ws, aid, _ = await _seed()
    bearer = mk_bearer("inflight")
    await seed_api_token(ws, aid, secret=bearer)
    try:
        async with client() as c:
            r = await c.post(f"/v1/agents/{aid}/invoke", headers=_auth(bearer), json={"inputs": {"message": "hi"}})
        assert r.status_code == 200                      # stream opened (200) then cut mid-flight
        frames = parse_sse(r.text)
        names = [n for n, _ in frames]
        assert "error" in names                          # an auth error frame was emitted
        err = next(d for n, d in frames if n == "error")
        assert err["code"] in ("auth.required", "auth.unauthorized", "revoked")
    finally:
        get_settings.cache_clear()


# ================================ review-fix regressions (post-018 code review) ====================
async def test_mint_naive_expires_at_in_past_is_clean_400():
    """A tz-naive `expires_at` must yield a clean 400 (input.invalid), not a 500 from comparing a naive value
    to an aware now(). Regression for the mint_token datetime fix."""
    ws, aid, _ = await _seed()
    async with client() as c:
        pub = dev_headers(workspace=ws, subject="pub", claims={ws: "publisher"})
        past_naive = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).replace(tzinfo=None).isoformat()
        r = await c.post(f"/api/v1/agents/{aid}/tokens", headers=pub,
                         json={"name": "naive", "expires_at": past_naive})
        assert r.status_code == 400, r.text
        assert r.json()["error"]["code"] == "input.invalid"


async def test_published_plane_honors_rate_limit_disabled(monkeypatch):
    """With AC_RATE_LIMIT_ENABLED=false the published caller plane must NOT consult the limiter (parity with the
    console `rate_limit` dep). Regression for the resolve_caller rate-limit gate."""
    from api import ratelimit
    from api.settings import get_settings

    monkeypatch.setenv("AC_RATE_LIMIT_ENABLED", "false")
    get_settings.cache_clear()
    calls = {"n": 0}
    orig = ratelimit.check

    async def _spy(*a, **k):
        calls["n"] += 1
        return await orig(*a, **k)

    monkeypatch.setattr(ratelimit, "check", _spy)
    _ok_gateway(monkeypatch)
    ws, aid, _ = await _seed()
    bearer = mk_bearer("ratedis")
    await seed_api_token(ws, aid, secret=bearer)
    try:
        async with client() as c:
            r = await c.post(f"/v1/agents/{aid}/invoke", headers=_auth(bearer),
                             json={"inputs": {"message": "hi"}})
        assert r.status_code == 200, r.text
        assert calls["n"] == 0            # limiter not consulted on the published plane when globally disabled
    finally:
        get_settings.cache_clear()


# NOTE: the caller_db SAVEPOINT fix (a failed last_used_at touch must not poison the request txn) is NOT unit-
# pinned here. Its genuine failure mode is a server-side abort mid-UPDATE (lock/statement timeout); a bad-bind
# input errors client-side without poisoning the txn, so it can't reproduce the bug, and forcing a real
# server-side abort of that benign UPDATE needs invasive session mocking. The fix is behavior-preserving (the
# 648 regression stays green) and verified by inspection.


# ================================ api_tokens RLS (SC-008 family) ==================================
async def test_api_tokens_rls_cross_tenant_isolated():
    ws_a = await seed_workspace()
    aid_a = await seed_agent(ws_a)
    await seed_api_token(ws_a, aid_a, secret=mk_bearer("tenantA"))
    ws_b = await seed_workspace()
    async with tenant_session(ws_b) as s:
        assert (await s.execute(select(ApiToken))).scalars().all() == []
    async with tenant_session(ws_a) as s:
        assert len((await s.execute(select(ApiToken))).scalars().all()) == 1
