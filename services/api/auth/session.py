"""Console session-cookie codec (feature 019).

The session is a **stateless, signed cookie**, not a server-side store (R4: a store widens the Redis SPOF;
immediate revocation is already the 018 deny-set, wired into `session_principal`). The cookie is an **HS256 JWT**
signed with `AC_SESSION_SECRET` via the already-present `pyjwt` (no `itsdangerous`). It carries
`{sub, iss, aud, exp, csrf}`; `exp` is hard-capped at 1h (FR-029). A bound CSRF token is returned alongside so the
router can set a readable `csrf` cookie for the double-submit check.

A short-lived **login-state** cookie (same HMAC) stashes the PKCE verifier + `state` + `nonce` across the
auth-code round-trip so the callback is stateless too (no server-side login store).

**Never log or echo `AC_SESSION_SECRET` or the cookie value (FR-028, P5).** This module logs nothing.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

import jwt

from starlette.requests import Request

from api.settings import get_settings

# Cookie names. The session cookie is HttpOnly; the csrf cookie is readable (double-submit). The login-state
# cookie is a short-lived signed stash for PKCE verifier + state + nonce across the auth-code round-trip.
SESSION_COOKIE_NAME = "ac_session"
CSRF_COOKIE_NAME = "ac_csrf"
LOGIN_STATE_COOKIE_NAME = "ac_login_state"

# FR-029 hard cap: a session can never be minted to live longer than 1h, regardless of config.
MAX_SESSION_TTL_S = 3600
# The login round-trip is interactive and brief; the stash must not outlive a reasonable login attempt.
LOGIN_STATE_TTL_S = 600

_ALG = "HS256"


class SessionConfigError(Exception):
    """No `AC_SESSION_SECRET` configured but a session mint was attempted. The router maps this to a 503 — a
    real login needs a configured signing key; message carries no secret."""


@dataclass(frozen=True)
class SessionClaims:
    """The verified claims read back from a session cookie. `iss`/`aud` are the IdP context carried at login
    (audit); the HMAC signature is the trust anchor. `csrf` binds the double-submit token to this session."""
    sub: str
    iss: str | None
    aud: str | None
    exp: int
    csrf: str


def _signing_key() -> str | None:
    return get_settings().session_secret


def _cookie_value(request: Request, name: str) -> str | None:
    cookies = getattr(request, "cookies", None)       # Starlette Request.cookies; SimpleNamespace in unit tests
    if not cookies:
        return None
    return cookies.get(name)


def issue_session(subject: str, iss: str | None, aud: str | None, *,
                  secret: str | None = None, ttl_s: int | None = None) -> tuple[str, str]:
    """Sign an HS256 session JWT (`exp = now + min(ttl_s, MAX_SESSION_TTL_S)`) + a bound CSRF token.
    Returns `(cookie_value, csrf_token)`. Raises `SessionConfigError` if no signing key is configured."""
    key = secret or _signing_key()
    if not key:
        raise SessionConfigError("session signing key not configured")
    ttl = ttl_s if ttl_s is not None else get_settings().session_ttl_s
    ttl = max(1, min(int(ttl), MAX_SESSION_TTL_S))     # FR-029: never beyond 1h (defense in depth vs. settings)
    now = int(time.time())
    csrf = secrets.token_urlsafe(32)
    payload = {"sub": subject, "iss": iss, "aud": aud, "csrf": csrf, "iat": now, "exp": now + ttl}
    return jwt.encode(payload, key, algorithm=_ALG), csrf


def read_session(request: Request) -> SessionClaims | None:
    """Verify the session cookie (signature + required claims + exp) and return `SessionClaims`, or `None` when
    the cookie is absent/invalid/expired or no signing key is configured. Never raises on a bad cookie — the
    caller falls back to the IdP provider path."""
    raw = _cookie_value(request, SESSION_COOKIE_NAME)
    if not raw:
        return None
    key = _signing_key()
    if not key:
        return None                                    # no trust anchor → ignore any cookie (header path instead)
    try:
        claims = jwt.decode(
            raw, key, algorithms=[_ALG],
            options={"require": ["exp", "sub", "iss", "aud", "csrf"], "verify_aud": False},
        )
    except jwt.PyJWTError:
        return None                                    # tampered / expired / malformed → not ours
    return SessionClaims(sub=str(claims["sub"]), iss=claims.get("iss"), aud=claims.get("aud"),
                         exp=int(claims["exp"]), csrf=str(claims["csrf"]))


def issue_login_state(state: str, nonce: str, code_verifier: str, *, secret: str | None = None) -> str:
    """Sign the short-lived login-state cookie carrying the PKCE verifier + `state` + `nonce` (stateless login)."""
    key = secret or _signing_key()
    if not key:
        raise SessionConfigError("session signing key not configured")
    now = int(time.time())
    payload = {"state": state, "nonce": nonce, "cv": code_verifier, "iat": now, "exp": now + LOGIN_STATE_TTL_S}
    return jwt.encode(payload, key, algorithm=_ALG)


def read_login_state(request: Request) -> dict | None:
    """Verify + decode the login-state cookie → `{state, nonce, cv}`, or `None` if absent/invalid/expired."""
    raw = _cookie_value(request, LOGIN_STATE_COOKIE_NAME)
    if not raw:
        return None
    key = _signing_key()
    if not key:
        return None
    try:
        claims = jwt.decode(raw, key, algorithms=[_ALG],
                            options={"require": ["exp", "state", "nonce", "cv"], "verify_aud": False})
    except jwt.PyJWTError:
        return None
    return {"state": str(claims["state"]), "nonce": str(claims["nonce"]), "cv": str(claims["cv"])}
