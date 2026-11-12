"""Red suite (T007, T009-T017) — 019 console login + session + CSRF. Unit tier, fully offline.

Constitution P9: written FIRST; fails on BEHAVIOR (501 /auth routes, no CSRF middleware, no cookie identity path
in session_principal) until T018-T023. No live IdP / paid call — the token exchange + JWKS are stubbed in-process
(`stub_oidc`); `fakeredis` stands in for the 018 deny-set.

Coverage: auth-code login establishes a signed session cookie (T009/FR-017); the cookie is validated every request
and tampered/expired/foreign cookies are rejected (T010/FR-018); a deny-set-listed subject is cut despite a valid
cookie, and a configured-Redis outage fails closed (T011/T012/FR-018/FR-027); double-submit CSRF guards cookie-auth
state changes while the published /v1 bearer plane is exempt (T013/T014/FR-019); logout ends the session
(T015/FR-020); the signing key + cookie never hit logs (T016/FR-028); an unreachable IdP fails visibly, no session
(T017). D4: interactive login is disabled under the dev profile.
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import fakeredis.aioredis
import httpx
import jwt as _jwt
import pytest

from api import deps, redis_client
from api.auth import denyset, oidc_login
from api.auth import session as sess
from api.auth.base import AuthError
from api.main import app
from api.settings import get_settings
from tests._api_helpers import make_rsa_keypair

# Throwaway signing material for the unit run (NOT credentials — never leave the test process).
SIGN_KEY = "unit-test-signing-material-" + ("0" * 12)
IDP_ISS = "https://stub-idp.local/iam"
IDP_AUD = "mcafee"


@pytest.fixture(autouse=True)
def _session_env(monkeypatch):
    monkeypatch.setenv("AC_SESSION_SECRET", SIGN_KEY)
    monkeypatch.setenv("AC_SESSION_COOKIE_SECURE", "false")   # in-process http test client
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def fake_redis(monkeypatch):
    client = fakeredis.aioredis.FakeRedis()
    monkeypatch.setattr(redis_client, "get_redis", lambda: client)
    return client


class _BoomPipe:
    def execute(self):
        async def _raise():
            raise ConnectionError("redis down")
        return _raise()
    def __getattr__(self, name):
        return lambda *a, **k: self


class _Boom:
    """A configured-but-unreachable Redis (pipeline sync, execute raises) — drives the fail-closed path."""
    def pipeline(self, *a, **k):
        return _BoomPipe()
    def __getattr__(self, name):
        async def _raise(*a, **k):
            raise ConnectionError("redis down")
        return _raise


def _aclient() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _req_with_session(cookie_value):
    """Minimal request exposing .cookies — all session_principal's cookie path reads."""
    return SimpleNamespace(cookies={sess.SESSION_COOKIE_NAME: cookie_value})


def _stub_provider_rejects(monkeypatch):
    """Make the fallback IdP provider reject, so a passing auth proves the COOKIE path authenticated."""
    class _P:
        id = "stub"
        async def verify(self, request):
            raise AuthError("no bearer")
    monkeypatch.setattr("api.auth.registry.get_active_provider", lambda settings: _P())


@pytest.fixture
def stub_oidc(monkeypatch):
    """A fully in-process OIDC IdP: a fixed config + a local RSA keypair standing in for the IdP JWKS, plus an
    id_token minter. The token-exchange HTTP client is wired per-test (the id_token must echo the server nonce)."""
    priv, pub = make_rsa_keypair()
    config = oidc_login.OIDCConfig(
        client_id="ac-console", client_secret="shh",
        authorize_endpoint="https://stub-idp.local/authorize",
        token_endpoint="https://stub-idp.local/token",
        jwks_uri="https://stub-idp.local/jwks",
        issuer=IDP_ISS, audience=IDP_AUD,
        redirect_uri="http://test/auth/callback",
    )
    monkeypatch.setattr(oidc_login, "config_from_settings", lambda settings: config)
    monkeypatch.setattr(oidc_login, "_resolve_signing_key", lambda cfg, id_token: pub)   # offline JWKS

    def mint_id_token(sub, nonce, *, iss=IDP_ISS, aud=IDP_AUD, exp_delta=600):
        now = dt.datetime.now(dt.timezone.utc)
        claims = {"sub": sub, "iss": iss, "aud": aud, "nonce": nonce,
                  "iat": int(now.timestamp()),
                  "exp": int((now + dt.timedelta(seconds=exp_delta)).timestamp())}
        return _jwt.encode(claims, priv, algorithm="RS256", headers={"kid": "stub-1"})

    return SimpleNamespace(priv=priv, pub=pub, config=config, mint_id_token=mint_id_token)


def _wire_token_endpoint(monkeypatch, id_token):
    """Point oidc_login._new_http_client at a stub whose /token POST returns `id_token`."""
    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"id_token": id_token, "access_token": "a", "token_type": "Bearer"}
    class _Client:
        async def post(self, *a, **k): return _Resp()
        async def aclose(self): pass
    monkeypatch.setattr(oidc_login, "_new_http_client", lambda: _Client())


async def _do_login_flow(c, monkeypatch, stub_oidc, *, sub="idp-user-7"):
    """Drive /auth/login → /auth/callback against the stub IdP; return the established session cookie value."""
    r1 = await c.get("/auth/login")
    assert r1.status_code == 302, f"login should redirect, got {r1.status_code}"
    login_state = r1.cookies.get(sess.LOGIN_STATE_COOKIE_NAME)
    assert login_state, "login must set the signed login-state cookie"
    decoded = _jwt.decode(login_state, SIGN_KEY, algorithms=["HS256"], options={"verify_aud": False})
    state, nonce = decoded["state"], decoded["nonce"]
    _wire_token_endpoint(monkeypatch, stub_oidc.mint_id_token(sub, nonce))
    r2 = await c.get(f"/auth/callback?code=abc&state={state}",
                     cookies={sess.LOGIN_STATE_COOKIE_NAME: login_state})
    assert r2.status_code == 302, f"callback should redirect to the app, got {r2.status_code}"
    session_cookie = r2.cookies.get(sess.SESSION_COOKIE_NAME)
    assert session_cookie, "callback must set the session cookie"
    return session_cookie


# ---- T009 / FR-017: auth-code login establishes a server-validated session --------------------------
async def test_login_redirects_with_pkce_and_state(stub_oidc):
    async with _aclient() as c:
        r = await c.get("/auth/login")
    assert r.status_code == 302
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["response_type"] == ["code"]
    assert q["code_challenge_method"] == ["S256"]       # PKCE S256 (authlib)
    assert q["client_id"] == ["ac-console"]
    assert q.get("state") and q.get("nonce")


async def test_authcode_callback_establishes_session(stub_oidc, fake_redis, monkeypatch):
    async with _aclient() as c:
        session_cookie = await _do_login_flow(c, monkeypatch, stub_oidc, sub="idp-user-7")
    claims = sess.read_session(_req_with_session(session_cookie))
    assert claims is not None and claims.sub == "idp-user-7"
    prin = await deps.session_principal(_req_with_session(session_cookie))   # per-request validation accepts it
    assert prin.subject == "idp-user-7"


async def test_id_token_aud_is_client_id_accepted(stub_oidc, fake_redis, monkeypatch):
    """OIDC Core §2: an id_token whose `aud` is the client_id (the spec-mandated value) MUST be accepted — not
    only the 012 resource audience. The old code verified against the resource audience alone and would 401 a
    conformant IdP; the fix accepts client_id OR the resource audience."""
    async with _aclient() as c:
        r1 = await c.get("/auth/login")
        login_state = r1.cookies.get(sess.LOGIN_STATE_COOKIE_NAME)
        decoded = _jwt.decode(login_state, SIGN_KEY, algorithms=["HS256"], options={"verify_aud": False})
        state, nonce = decoded["state"], decoded["nonce"]
        # Mint the id_token with aud = client_id (OIDC-conformant), NOT the resource audience.
        _wire_token_endpoint(monkeypatch,
                             stub_oidc.mint_id_token("conformant-sub", nonce, aud=stub_oidc.config.client_id))
        r2 = await c.get(f"/auth/callback?code=abc&state={state}",
                         cookies={sess.LOGIN_STATE_COOKIE_NAME: login_state})
    assert r2.status_code == 302, f"a client_id-audience id_token must be accepted, got {r2.status_code}"
    session_cookie = r2.cookies.get(sess.SESSION_COOKIE_NAME)
    assert session_cookie, "callback must establish a session from a conformant id_token"
    claims = sess.read_session(_req_with_session(session_cookie))
    assert claims is not None and claims.sub == "conformant-sub"


# ---- T010 / FR-018: the cookie is validated on every request ----------------------------------------
async def test_valid_cookie_authenticates_without_provider(fake_redis, monkeypatch):
    _stub_provider_rejects(monkeypatch)
    cookie, _ = sess.issue_session("cookie-user", IDP_ISS, IDP_AUD)
    prin = await deps.session_principal(_req_with_session(cookie))
    assert prin.subject == "cookie-user"               # authenticated by the cookie, not the (rejecting) provider


async def test_tampered_cookie_rejected(fake_redis, monkeypatch):
    _stub_provider_rejects(monkeypatch)
    cookie, _ = sess.issue_session("cookie-user", IDP_ISS, IDP_AUD)
    tampered = cookie[:-2] + ("aa" if not cookie.endswith("aa") else "bb")
    with pytest.raises(AuthError):
        await deps.session_principal(_req_with_session(tampered))


async def test_expired_cookie_rejected(fake_redis, monkeypatch):
    _stub_provider_rejects(monkeypatch)
    now = int(time.time())
    expired = _jwt.encode({"sub": "u", "iss": IDP_ISS, "aud": IDP_AUD, "csrf": "c",
                           "iat": now - 100, "exp": now - 5}, SIGN_KEY, algorithm="HS256")
    with pytest.raises(AuthError):
        await deps.session_principal(_req_with_session(expired))


# ---- T011 / FR-018: deny-set cut despite a valid cookie (reuses the 018 block) -----------------------
async def test_denied_subject_rejected_despite_valid_cookie(fake_redis):
    await denyset.deny(subject="banned", ttl_s=3600)
    cookie, _ = sess.issue_session("banned", IDP_ISS, IDP_AUD)
    with pytest.raises(AuthError):
        await deps.session_principal(_req_with_session(cookie))


# ---- T012 / FR-027: deny-set fail-closed on Redis down; no-op allow when unset ----------------------
async def test_session_denyset_redis_down_fails_closed(monkeypatch):
    monkeypatch.setattr(redis_client, "get_redis", lambda: _Boom())
    cookie, _ = sess.issue_session("u", IDP_ISS, IDP_AUD)
    with pytest.raises(AuthError):
        await deps.session_principal(_req_with_session(cookie))


async def test_session_denyset_redis_unset_allows(monkeypatch):
    monkeypatch.setattr(redis_client, "get_redis", lambda: None)
    cookie, _ = sess.issue_session("u", IDP_ISS, IDP_AUD)
    prin = await deps.session_principal(_req_with_session(cookie))
    assert prin.subject == "u"


# ---- T013 / FR-019: double-submit CSRF on cookie-auth state changes ---------------------------------
async def test_csrf_required_on_state_change(fake_redis):
    cookie, _csrf = sess.issue_session("u", IDP_ISS, IDP_AUD)
    async with _aclient() as c:
        r = await c.post("/api/v1/agents", cookies={sess.SESSION_COOKIE_NAME: cookie}, json={})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "csrf.invalid"   # blocked at the middleware, before routing/auth


async def test_csrf_matching_token_passes(fake_redis):
    cookie, csrf = sess.issue_session("u", IDP_ISS, IDP_AUD)
    async with _aclient() as c:
        r = await c.post("/api/v1/agents",
                         cookies={sess.SESSION_COOKIE_NAME: cookie, sess.CSRF_COOKIE_NAME: csrf},
                         headers={"X-CSRF-Token": csrf}, json={})
    # a matching double-submit passes CSRF (whatever the route then does — never csrf.invalid)
    assert not (r.status_code == 403 and r.json().get("error", {}).get("code") == "csrf.invalid")


async def test_get_is_csrf_exempt(fake_redis):
    cookie, _ = sess.issue_session("u", IDP_ISS, IDP_AUD)
    async with _aclient() as c:
        r = await c.get("/api/v1/agents", cookies={sess.SESSION_COOKIE_NAME: cookie})
    assert not (r.status_code == 403 and r.json().get("error", {}).get("code") == "csrf.invalid")


# ---- T014 / FR-019: the published /v1 bearer plane is CSRF-exempt -----------------------------------
async def test_published_plane_exempt_from_csrf(fake_redis):
    cookie, _ = sess.issue_session("u", IDP_ISS, IDP_AUD)
    async with _aclient() as c:
        r = await c.post("/v1/invoke", cookies={sess.SESSION_COOKIE_NAME: cookie}, json={})
    assert not (r.status_code == 403 and r.json().get("error", {}).get("code") == "csrf.invalid")


# ---- T015 / FR-020: logout ends the session --------------------------------------------------------
async def test_logout_ends_session(fake_redis):
    cookie, csrf = sess.issue_session("u", IDP_ISS, IDP_AUD)
    async with _aclient() as c:
        r = await c.post("/auth/logout",
                         cookies={sess.SESSION_COOKIE_NAME: cookie, sess.CSRF_COOKIE_NAME: csrf},
                         headers={"X-CSRF-Token": csrf})
    assert r.status_code in (200, 204)
    set_cookie = r.headers.get("set-cookie", "")
    assert sess.SESSION_COOKIE_NAME in set_cookie           # the session cookie is cleared


# ---- T016 / FR-028, P5: the signing key + cookie never hit logs -------------------------------------
async def test_session_secret_never_logged(stub_oidc, fake_redis, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    async with _aclient() as c:
        session_cookie = await _do_login_flow(c, monkeypatch, stub_oidc)
        await deps.session_principal(_req_with_session(session_cookie))
    blob = " ".join(rec.getMessage() for rec in caplog.records)
    assert SIGN_KEY not in blob
    assert session_cookie not in blob


# ---- T017: an unreachable IdP fails visibly, no session --------------------------------------------
async def test_idp_unreachable_fails_visibly(stub_oidc, monkeypatch):
    async with _aclient() as c:
        r1 = await c.get("/auth/login")
        login_state = r1.cookies.get(sess.LOGIN_STATE_COOKIE_NAME)
        state = _jwt.decode(login_state, SIGN_KEY, algorithms=["HS256"],
                            options={"verify_aud": False})["state"]

        class _Dead:
            async def post(self, *a, **k): raise httpx.ConnectError("boom")
            async def aclose(self): pass
        monkeypatch.setattr(oidc_login, "_new_http_client", lambda: _Dead())
        r2 = await c.get(f"/auth/callback?code=xyz&state={state}",
                         cookies={sess.LOGIN_STATE_COOKIE_NAME: login_state})
    assert r2.status_code == 503                             # visible failure, not fail-open
    assert sess.SESSION_COOKIE_NAME not in r2.headers.get("set-cookie", "")


# ---- D4: interactive login is disabled under the dev profile (no OIDC configured) -------------------
async def test_login_not_configured_is_clear_error():
    # No stub_oidc → config_from_settings sees no OIDC config (dev) → a clear, non-500 refusal.
    async with _aclient() as c:
        r = await c.get("/auth/login")
    assert r.status_code in (400, 503)
    assert r.json()["error"]["code"] in ("auth.unavailable", "input.invalid")
