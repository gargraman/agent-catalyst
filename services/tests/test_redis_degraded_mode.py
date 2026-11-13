"""021 degraded-mode matrix (FR-024/025) — the shared cache (Redis) is a single point of failure for several
controls; this proves each one's *documented, deliberate* degraded behavior instead of silent weakening.

Honest scoping (021 D1): the headline behaviors already exist in code (016 limiter fail-open+alert; 018 deny-set
fail-closed; the Postgres spend cap; the in-process idempotency guard). This file is the **evidence** that ties
each to the degraded-mode decision record (``docs/backend/degraded-mode.md``), not new controls.

Per-control expectation on shared-cache unavailability (decision record / data-model table):

| Control             | Redis today | Degraded behavior          |
|---------------------|-------------|----------------------------|
| Deny-set (018)      | yes         | fail CLOSED → 401          |
| Rate limit (016)    | yes         | fail OPEN + alert          |
| Spend cap (009)     | no (PG)     | fail CLOSED on ledger error|
| Idempotency (D8)    | no (in-proc)| 409 retryable (Redis-indep)|
| /readyz (021 D3)    | yes         | 503 when configured-down   |

Mostly infra-free (``BoomRedis`` from ``_redis_degraded``); the ``/readyz`` cases are integration-tier (need the
compose DB for the ``database: ok`` leg) and skip cleanly when the DB is unreachable.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import Response

from api import deps, metrics, redis_client
from api.auth import denyset
from api.auth.base import AuthError
from api.deps import Principal, rate_limit
from api.errors import AppError, IdempotencyInProgress
from api.publish.token_auth import resolve_caller
from tests._redis_degraded import BoomRedis, RedisHolder, fake_up


# ---- scaffold (T004): the surface imports so Red tests fail on behavior, not import -----------------
def test_matrix_scaffold_imports():
    assert hasattr(denyset, "is_denied")
    assert hasattr(deps, "session_principal") and hasattr(deps, "rate_limit")
    assert hasattr(deps, "assert_within_spend_cap") and hasattr(deps, "idempotency_guard")


def _prin(subject: str = "user-a") -> Principal:
    return Principal(subject=subject, workspace_claims={"ws1": "builder"})


def _degraded_count() -> float:
    return metrics.ac_rate_limit_degraded_total._value.get()


def _stub_provider(monkeypatch, subject: str) -> Principal:
    """Make session_principal's active IdP provider return a fixed Principal (no real IdP / header)."""
    prin = Principal(subject=subject, workspace_claims={"ws1": "builder"})

    class _P:
        id = "stub"

        async def verify(self, request):
            return prin

    monkeypatch.setattr("api.auth.registry.get_active_provider", lambda settings: _P())
    return prin


# ---- caller-plane fakes (no DB): a valid token row resolved, so the deny-set line is reached ---------
class _FakeResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _FakeSession:
    """Stands in for the unscoped auth-lookup session: returns one valid caller-token row."""

    def __init__(self, row):
        self._row = row

    async def execute(self, *a, **k):
        return _FakeResult(self._row)


def _bearer_request(token: str = "ct-secret-xyz") -> SimpleNamespace:
    return SimpleNamespace(headers={"authorization": f"Bearer {token}"})


def _valid_token_row() -> dict:
    return {
        "id": "00000000-0000-0000-0000-0000000000aa",
        "workspace_id": "00000000-0000-0000-0000-0000000000bb",
        "agent_id": "00000000-0000-0000-0000-0000000000cc",
        "scopes": {"invoke": True, "teams": []},
        "expires_at": None,
        "revoked_at": None,
    }


# ==================================================================================================
# FR-024 (deny-set), AS1 — Redis down → BOTH planes fail closed (401)
# ==================================================================================================
async def test_denyset_fails_closed_both_planes(monkeypatch):
    monkeypatch.setattr(redis_client, "get_redis", lambda: BoomRedis())   # configured-but-unreachable

    # console plane: session_principal maps the deny-set raise → AuthError (→ 401), not pass-through
    _stub_provider(monkeypatch, "console-user")
    with pytest.raises(AuthError):
        await deps.session_principal(SimpleNamespace())

    # caller plane: a valid, non-revoked token row is resolved, then the deny-set check raises → AuthError
    with pytest.raises(AuthError):
        await resolve_caller(_bearer_request(), _FakeSession(_valid_token_row()))


# ==================================================================================================
# FR-024 (rate limit), AS1 — Redis down → fail OPEN + alert (availability control)
# ==================================================================================================
async def test_ratelimit_fails_open_and_alerts(monkeypatch):
    monkeypatch.setattr(redis_client, "get_redis", lambda: BoomRedis())
    before = _degraded_count()
    dep = rate_limit("invoke")
    # allowed (no 429, no raise) even though Redis is down — the limiter never self-inflicts an outage
    assert await dep(principal=_prin("degraded-caller"), response=Response()) is None
    assert _degraded_count() == before + 1                                 # ac_rate_limit_degraded_total alert


# ==================================================================================================
# FR-024 (spend cap), AS1 — fail CLOSED on a ledger error (Postgres-backed; independent of Redis, D2)
# ==================================================================================================
async def test_spend_cap_fails_closed(monkeypatch):
    async def _boom(*a, **k):
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr("api.spend.month_to_date_spend", _boom)
    with pytest.raises(AppError) as ei:
        await deps.assert_within_spend_cap(session=None, ws="ws-x")        # session unused before the raise
    assert ei.value.detail.get("reason") == "spend_indeterminate"          # controlled refusal, no paid call
    # and it does NOT falsely claim the budget is spent
    assert ei.value.code != "budget.exceeded"


# ==================================================================================================
# FR-024 (idempotency), AS1 — 409 retryable, independent of Redis (in-process guard, D2/D8)
# ==================================================================================================
async def test_idempotency_fails_closed_retryable():
    async with deps.idempotency_guard("ws-1", "key-1"):
        with pytest.raises(IdempotencyInProgress):                         # concurrent retry → 409
            async with deps.idempotency_guard("ws-1", "key-1"):
                pass
    # released after the first completes → re-acquirable (retryable, not a permanent lock)
    async with deps.idempotency_guard("ws-1", "key-1"):
        pass


# ==================================================================================================
# Edge case — Redis recovers mid-run → controls resume normal behavior WITHOUT a restart
# ==================================================================================================
async def test_controls_resume_after_redis_recovers(monkeypatch):
    holder = RedisHolder(BoomRedis())                                      # start: Redis down
    monkeypatch.setattr(redis_client, "get_redis", holder.get)

    # deny-set fails closed while down …
    with pytest.raises(Exception):
        await denyset.is_denied("subj", "tok")
    # rate limit fails open + alerts while down …
    before = _degraded_count()
    assert await rate_limit("invoke")(principal=_prin("mid"), response=Response()) is None
    assert _degraded_count() == before + 1

    holder.current = fake_up()                                            # Redis comes back — no restart

    # deny-set resumes normal (clean subject → allowed, no raise) …
    assert await denyset.is_denied("subj", "tok") is False
    # rate limit resumes enforcement (no further degraded increment) …
    after_recovery = _degraded_count()
    assert await rate_limit("invoke")(principal=_prin("mid"), response=Response()) is None
    assert _degraded_count() == after_recovery                            # not degraded anymore


# ==================================================================================================
# D3 — /readyz reflects the fail-closed posture: configured-but-down Redis → 503; unset → 200 (carve-out)
# (integration-tier: needs the compose DB for the database:ok leg)
# ==================================================================================================
@pytest.mark.integration
async def test_readyz_503_when_configured_redis_down(monkeypatch):
    from api.settings import get_settings
    from tests._api_helpers import client

    monkeypatch.setattr(get_settings(), "redis_url", "redis://configured-but-dead:6379/0")

    async def _down():
        return False

    monkeypatch.setattr(redis_client, "is_healthy", _down)
    async with client() as c:
        r = await c.get("/readyz")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "not-ready"
    assert body["checks"]["database"] == "ok"                             # DB is fine …
    assert body["checks"]["redis"] == "down"                              # … Redis is the reason (503)


@pytest.mark.integration
async def test_readyz_200_when_configured_redis_up(monkeypatch):
    from api.settings import get_settings
    from tests._api_helpers import client

    monkeypatch.setattr(get_settings(), "redis_url", "redis://configured:6379/0")

    async def _up():
        return True

    monkeypatch.setattr(redis_client, "is_healthy", _up)
    async with client() as c:
        r = await c.get("/readyz")
    assert r.status_code == 200
    assert r.json()["checks"]["redis"] == "ok"


@pytest.mark.integration
async def test_readyz_200_when_redis_unset_carveout(monkeypatch):
    from api.settings import get_settings
    from tests._api_helpers import client

    # Carve-out (parity with deny-set "unset → allow"): with AC_REDIS_URL unset, Redis is NOT a readiness
    # dependency — /readyz stays 200 and never consults Redis.
    monkeypatch.setattr(get_settings(), "redis_url", None)

    async def _should_not_be_called():
        raise AssertionError("is_healthy must not be consulted when AC_REDIS_URL is unset")

    monkeypatch.setattr(redis_client, "is_healthy", _should_not_be_called)
    async with client() as c:
        r = await c.get("/readyz")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"
    assert "redis" not in r.json()["checks"]                              # Redis not a dependency when unset
