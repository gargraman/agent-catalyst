"""Red suite — 016 M5 Rate Limiting (F5). Constitution P9: these are written FIRST and must FAIL on
behavior (the `rate_limit(route_class)` factory + sliding-window engine do not exist yet), not on import.

Infra-free: `fakeredis` stands in for Redis; the limiter dependency is exercised directly (no DB, no live
provider) by calling the returned callable with a constructed `Principal` + FastAPI `Response`. Per-token
invoke keying is out of this slice (→ 018); the engine is keyed per principal here (spec FR-001).
"""
from __future__ import annotations

import fakeredis.aioredis
import pytest
from fastapi import Response

from api import deps, metrics, ratelimit, redis_client
from api.deps import Principal, rate_limit
from api.errors import RateLimited
from api.settings import get_settings


# ---- fixtures / helpers (T007 scaffold) ----------------------------------------------------------
@pytest.fixture
def fake_redis(monkeypatch):
    """Point the shared redis client at an in-memory fakeredis for the duration of a test."""
    client = fakeredis.aioredis.FakeRedis()
    monkeypatch.setattr(redis_client, "get_redis", lambda: client)
    return client


def _prin(subject: str = "user-a") -> Principal:
    return Principal(subject=subject, workspace_claims={"ws1": "builder"})


def _degraded_count() -> float:
    return metrics.ac_rate_limit_degraded_total._value.get()


def _rate_limited_count(route_class: str) -> float:
    return metrics.ac_rate_limited_total.labels(route_class=route_class)._value.get()


def test_scaffold_imports():
    """T007: module + limiter surface import cleanly so the Red tests below fail on behavior, not import."""
    assert hasattr(ratelimit, "check") and hasattr(ratelimit, "Decision")
    assert hasattr(deps, "rate_limit") and hasattr(redis_client, "get_redis")


# ---- T008: over-limit → 429 + headers, no provider work ------------------------------------------
async def test_over_limit_429_headers(fake_redis, monkeypatch):
    monkeypatch.setitem(get_settings().rate_limits, "invoke", (60, 3))
    dep = rate_limit("invoke")
    p = _prin("burst-caller")
    before = _rate_limited_count("invoke")
    for _ in range(3):
        assert await dep(principal=p, response=Response()) is None   # under limit → allowed
    with pytest.raises(RateLimited) as ei:                            # (N+1)th → rejected before any work
        await dep(principal=p, response=Response())
    exc = ei.value
    assert exc.code == "rate.limited"
    h = exc.headers or {}
    assert "Retry-After" in h
    assert h.get("X-RateLimit-Limit") == "3"
    assert h.get("X-RateLimit-Remaining") == "0"
    assert "X-RateLimit-Reset" in h
    assert _rate_limited_count("invoke") == before + 1                # 429 telemetry fired (FR-009)


# ---- T009: expensive console route throttled before work ----------------------------------------
async def test_console_route_throttled_before_work(fake_redis, monkeypatch):
    monkeypatch.setitem(get_settings().rate_limits, "eval", (60, 1))
    dep = rate_limit("eval")                                          # eval = expensive console op
    p = _prin("builder-x")
    assert await dep(principal=p, response=Response()) is None
    with pytest.raises(RateLimited):                                  # raised at the dependency = before handler
        await dep(principal=p, response=Response())


# ---- T010: per-identity isolation ---------------------------------------------------------------
async def test_per_identity_isolation(fake_redis, monkeypatch):
    monkeypatch.setitem(get_settings().rate_limits, "invoke", (60, 1))
    dep = rate_limit("invoke")
    a, b = _prin("ident-a"), _prin("ident-b")
    assert await dep(principal=a, response=Response()) is None
    with pytest.raises(RateLimited):
        await dep(principal=a, response=Response())                   # a throttled
    assert await dep(principal=b, response=Response()) is None        # b unaffected


# ---- T011: window reset re-allows ---------------------------------------------------------------
async def test_window_reset_allows(fake_redis, monkeypatch):
    clock = {"ms": 1_000_000}
    monkeypatch.setattr(ratelimit, "_now_ms", lambda: clock["ms"])
    monkeypatch.setitem(get_settings().rate_limits, "invoke", (1, 1))  # 1 req / 1s window
    dep = rate_limit("invoke")
    p = _prin("waiter")
    assert await dep(principal=p, response=Response()) is None
    with pytest.raises(RateLimited):
        await dep(principal=p, response=Response())
    clock["ms"] += 2000                                               # advance past the window
    assert await dep(principal=p, response=Response()) is None        # succeeds again


# ---- T012: Redis down → fail OPEN + alert (availability control) ---------------------------------
async def test_redis_down_fails_open_alerts(monkeypatch):
    class _Boom:
        def __getattr__(self, name):                                  # any backend touch explodes
            raise ConnectionError("redis down")

    monkeypatch.setattr(redis_client, "get_redis", lambda: _Boom())
    before = _degraded_count()
    dep = rate_limit("invoke")
    assert await dep(principal=_prin("degraded-caller"), response=Response()) is None  # allowed, no 429
    assert _degraded_count() == before + 1                            # alert signal incremented


# ---- T013: limits table drives window/limit per route_class -------------------------------------
async def test_limits_table_applied(fake_redis):
    r_eval = Response()
    await rate_limit("eval")(principal=_prin("t13-eval"), response=r_eval)
    assert r_eval.headers["X-RateLimit-Limit"] == "20"               # eval default = 20
    r_inv = Response()
    await rate_limit("invoke")(principal=_prin("t13-inv"), response=r_inv)
    assert r_inv.headers["X-RateLimit-Limit"] == "120"              # invoke default = 120


# ---- T013b: unlisted route_class falls back to the `default` bucket (forces rate_limit_for) -------
async def test_unlisted_route_class_falls_back_to_default(fake_redis):
    r = Response()
    await rate_limit("mcp")(principal=_prin("t13-fallback"), response=r)  # 'mcp' not in the table
    assert r.headers["X-RateLimit-Limit"] == "300"                  # must fall back to default (300), not KeyError


# ---- T014: success headers + no secret in key ---------------------------------------------------
async def test_success_headers_and_no_secret(monkeypatch):
    captured: dict[str, str] = {}

    async def _spy(key: str, limit: int, window_s: int) -> ratelimit.Decision:
        captured["key"] = key
        return ratelimit.Decision(allowed=True, remaining=limit - 1,
                                  reset_epoch=1_700_000_060, retry_after=0, degraded=False)

    monkeypatch.setattr(ratelimit, "check", _spy)
    resp = Response()
    p = Principal(subject="subj-42", workspace_claims={"ws1": "builder"},
                  scopes=("xdr.secret.scope",), issuer="https://iam.example")
    await rate_limit("invoke")(principal=p, response=resp)
    assert resp.headers["X-RateLimit-Limit"] == "120"
    assert resp.headers["X-RateLimit-Remaining"] == "119"
    assert resp.headers["X-RateLimit-Reset"] == "1700000060"
    # key is per-principal by SUBJECT — no token/scope/issuer material leaks into it (FR-005/FR-028, P5)
    assert captured["key"] == "rl:prin:subj-42:invoke"
    assert "secret" not in captured["key"] and "iam.example" not in captured["key"]
