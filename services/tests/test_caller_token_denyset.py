"""Red suite — 018 caller-token deny-set + console-session deny check (unit tier, no DB).

Constitution P9: written FIRST, must FAIL on behavior (the deny-set engine + the `session_principal` deny
check do not exist yet), not on import. `fakeredis` stands in for Redis; the DB-backed caller-token routes +
published invoke + in-flight cut live in `test_api_tokens.py` (integration tier).

The deny-set is the SECURITY-control inverse of the 016 limiter: it fails CLOSED on a configured-Redis error
(a raise the caller turns into 401) and no-op-allows only when Redis is UNSET (dev/test parity).
"""
from __future__ import annotations

import fakeredis.aioredis
import pytest

from api.auth import denyset
from api import redis_client


@pytest.fixture
def fake_redis(monkeypatch):
    client = fakeredis.aioredis.FakeRedis()
    monkeypatch.setattr(redis_client, "get_redis", lambda: client)
    return client


class _BoomPipe:
    """A pipeline whose queueing is sync (chainable) but whose awaited `execute()` explodes — mirrors
    `redis.asyncio` shape so the fail-closed path is exercised at the real failure point."""
    def execute(self):
        async def _raise():
            raise ConnectionError("redis down")
        return _raise()
    def __getattr__(self, name):            # exists()/set()/… queue synchronously and chain
        return lambda *a, **k: self


class _Boom:
    """A configured-but-unreachable Redis: `pipeline()` is sync (real shape); `execute()` raises."""
    def pipeline(self, *a, **k):
        return _BoomPipe()
    def __getattr__(self, name):            # any direct op (ping, etc.) also explodes when awaited
        async def _raise(*a, **k):
            raise ConnectionError("redis down")
        return _raise


def test_denyset_scaffold_imports():
    assert hasattr(denyset, "is_denied") and hasattr(denyset, "deny")


# ---- no-op parity when Redis is UNSET (get_redis → None) -----------------------------------------
async def test_is_denied_false_when_redis_unconfigured(monkeypatch):
    monkeypatch.setattr(redis_client, "get_redis", lambda: None)
    assert await denyset.is_denied("subj-x", "tok-x") is False     # nothing to consult → allow (parity)


async def test_deny_is_noop_when_redis_unconfigured(monkeypatch):
    monkeypatch.setattr(redis_client, "get_redis", lambda: None)
    await denyset.deny(subject="subj-x", ttl_s=3600)               # must not raise with no backend


# ---- presence rejects (subject + token-id) ------------------------------------------------------
async def test_deny_subject_then_is_denied(fake_redis):
    assert await denyset.is_denied("subj-1", None) is False
    await denyset.deny(subject="subj-1", ttl_s=3600)
    assert await denyset.is_denied("subj-1", None) is True
    assert await denyset.is_denied("subj-OTHER", None) is False     # isolated per subject


async def test_deny_token_id_then_is_denied(fake_redis):
    await denyset.deny(token_id="tok-9", ttl_s=3600)
    assert await denyset.is_denied(None, "tok-9") is True
    assert await denyset.is_denied("unrelated-subj", "tok-OTHER") is False


async def test_is_denied_matches_either_key(fake_redis):
    """A subject on the set rejects even when the presented token-id is clean, and vice-versa."""
    await denyset.deny(subject="banned", ttl_s=3600)
    assert await denyset.is_denied("banned", "clean-token") is True


# ---- TTL floor (FR-014) -------------------------------------------------------------------------
async def test_deny_sets_ttl_at_least_requested(fake_redis):
    await denyset.deny(subject="ttl-subj", ttl_s=3600)
    ttl = await fake_redis.ttl("deny:sub:ttl-subj")
    assert 0 < ttl <= 3600                                          # bounded lifetime, ≥ ~1h window


# ---- fail CLOSED on a configured-Redis error (FR-024b) ------------------------------------------
async def test_is_denied_raises_on_redis_error(monkeypatch):
    monkeypatch.setattr(redis_client, "get_redis", lambda: _Boom())
    with pytest.raises(Exception):                                  # caller converts this to 401 (fail closed)
        await denyset.is_denied("subj", "tok")


async def test_deny_raises_on_redis_error(monkeypatch):
    monkeypatch.setattr(redis_client, "get_redis", lambda: _Boom())
    with pytest.raises(Exception):                                  # a revoke that didn't take must not be silent
        await denyset.deny(subject="subj", ttl_s=3600)


# ==================================================================================================
# Console-session deny check (T021) — session_principal must reject a deny-set-listed subject (FR-013)
# so an admin force-logout kills an ACTIVE console session on its next request, not just caller tokens.
# ==================================================================================================
from types import SimpleNamespace  # noqa: E402

from api import deps  # noqa: E402
from api.auth.base import AuthError  # noqa: E402


def _stub_provider(monkeypatch, subject: str):
    """Make session_principal's active provider return a fixed Principal (no real IdP/header needed)."""
    prin = deps.Principal(subject=subject, workspace_claims={"ws1": "builder"})

    class _P:
        id = "stub"
        async def verify(self, request):
            return prin

    monkeypatch.setattr("api.auth.registry.get_active_provider", lambda settings: _P())
    return prin


async def test_session_principal_allows_clean_subject(fake_redis, monkeypatch):
    prin = _stub_provider(monkeypatch, "clean-user")
    assert await deps.session_principal(SimpleNamespace()) is prin   # not denied → passes through unchanged


async def test_session_principal_rejects_denied_subject(fake_redis, monkeypatch):
    _stub_provider(monkeypatch, "banned-user")
    await denyset.deny(subject="banned-user", ttl_s=3600)            # admin force-logout
    with pytest.raises(AuthError):                                   # active session cut on next request (FR-013)
        await deps.session_principal(SimpleNamespace())


async def test_session_principal_fails_closed_when_denyset_down(monkeypatch):
    _stub_provider(monkeypatch, "any-user")
    monkeypatch.setattr(redis_client, "get_redis", lambda: _Boom())  # configured Redis, unreachable
    with pytest.raises(AuthError):                                   # fail CLOSED (D2/FR-024b), not pass-through
        await deps.session_principal(SimpleNamespace())


async def test_session_principal_allows_when_redis_unconfigured(monkeypatch):
    prin = _stub_provider(monkeypatch, "dev-user")
    monkeypatch.setattr(redis_client, "get_redis", lambda: None)     # no Redis (dev/test) → no deny-set → allow
    assert await deps.session_principal(SimpleNamespace()) is prin
