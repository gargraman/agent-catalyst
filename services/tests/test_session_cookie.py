"""Red suite (T008) — 019 session-cookie codec: sign/verify round-trip, 1h cap, tamper, expiry, missing claims.

Constitution P9: written FIRST; fails on BEHAVIOR (issue_session/read_session raise NotImplementedError) until
T018. Unit tier — no DB, no Redis, no live IdP. The HMAC key is the trust anchor; these tests pin that a
tampered/expired/foreign-key cookie is rejected and that `exp` can never exceed 1h (FR-029).
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import jwt as _jwt
import pytest

from api.auth import session as sess
from api.settings import get_settings

# A throwaway HMAC signing value for the unit run (NOT a credential — never leaves the test process).
SIGN_KEY = "unit-test-signing-material-" + ("0" * 12)
OTHER_KEY = "unit-test-other-material-" + ("1" * 12)


@pytest.fixture(autouse=True)
def _session_key(monkeypatch):
    monkeypatch.setenv("AC_SESSION_SECRET", SIGN_KEY)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _req(cookie_value=None):
    cookies = {sess.SESSION_COOKIE_NAME: cookie_value} if cookie_value is not None else {}
    return SimpleNamespace(cookies=cookies)


def test_issue_read_roundtrip():
    cookie, csrf = sess.issue_session("user-1", "https://idp.local", "mcafee")
    assert isinstance(cookie, str) and isinstance(csrf, str) and csrf
    claims = sess.read_session(_req(cookie))
    assert claims is not None
    assert claims.sub == "user-1"
    assert claims.iss == "https://idp.local"
    assert claims.aud == "mcafee"
    assert claims.csrf == csrf                       # the CSRF token is bound into the session


def test_tampered_signature_rejected():
    cookie, _ = sess.issue_session("user-1", "i", "a")
    tampered = cookie[:-3] + ("aaa" if not cookie.endswith("aaa") else "bbb")
    assert sess.read_session(_req(tampered)) is None


def test_foreign_key_rejected(monkeypatch):
    cookie, _ = sess.issue_session("user-1", "i", "a")
    monkeypatch.setenv("AC_SESSION_SECRET", OTHER_KEY)
    get_settings.cache_clear()
    assert sess.read_session(_req(cookie)) is None   # minted under a different key → not ours → reject


def test_expired_cookie_rejected():
    now = int(time.time())
    expired = _jwt.encode(
        {"sub": "u", "iss": "i", "aud": "a", "csrf": "c", "iat": now - 100, "exp": now - 10},
        SIGN_KEY, algorithm="HS256")
    assert sess.read_session(_req(expired)) is None


def test_ttl_hard_capped_at_one_hour():
    before = int(time.time())
    cookie, _ = sess.issue_session("u", "i", "a", ttl_s=99999)   # ask for way more than 1h
    claims = sess.read_session(_req(cookie))
    assert claims is not None
    assert claims.exp - before <= 3600 + 2                       # FR-029: never longer than 1h


def test_absent_cookie_returns_none():
    assert sess.read_session(_req(None)) is None
    assert sess.read_session(SimpleNamespace()) is None          # no .cookies attr at all → graceful None


def test_no_key_returns_none(monkeypatch):
    monkeypatch.delenv("AC_SESSION_SECRET", raising=False)
    get_settings.cache_clear()
    now = int(time.time())
    some = _jwt.encode({"sub": "u", "exp": now + 100}, "whatever", algorithm="HS256")
    assert sess.read_session(_req(some)) is None                 # no signing key configured → no cookie trust


def test_issue_requires_key(monkeypatch):
    monkeypatch.delenv("AC_SESSION_SECRET", raising=False)
    get_settings.cache_clear()
    with pytest.raises(Exception):
        sess.issue_session("u", "i", "a")                        # cannot mint without a signing key


def test_missing_required_claims_rejected():
    now = int(time.time())
    no_csrf = _jwt.encode({"sub": "u", "iss": "i", "aud": "a", "exp": now + 100},
                          SIGN_KEY, algorithm="HS256")
    assert sess.read_session(_req(no_csrf)) is None              # a session cookie must carry its bound csrf
