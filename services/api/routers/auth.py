"""Console login routes (feature 019).

`GET /auth/login` → 302 to the IdP (auth-code + PKCE), stashing state/nonce/verifier in a short signed cookie;
`GET /auth/callback` → validate → mint the session + csrf cookies → 302 to the app; `POST /auth/logout` → clear
the cookies (204). Under the dev profile (no OIDC configured) `/auth/login` returns a clear, fail-closed refusal
(503 auth.unavailable) rather than pretending (D4). An IdP unreachable at the callback fails VISIBLY (503), never
fail-open — no session is established. Never logs the token/secret/cookie (FR-011/FR-028).
"""
from __future__ import annotations

from fastapi import APIRouter, Response
from fastapi.responses import RedirectResponse
from starlette.requests import Request

import logging

from api import sync
from api.auth import oidc_login
from api.auth import session as sess
from api.auth.registry import AuthConfigError, get_active_provider
from api.errors import InputInvalid
from api.settings import get_settings

router = APIRouter(tags=["auth"])
log = logging.getLogger("api.auth")


def _cookie_kwargs(settings) -> dict:
    """Shared cookie attributes: SameSite=Lax (pinned), Secure by default (relaxable only under dev), path=/."""
    return {"secure": settings.session_cookie_secure, "samesite": "lax", "path": "/"}


@router.get("/auth/login")
async def login() -> RedirectResponse:
    settings = get_settings()
    config = oidc_login.config_from_settings(settings)          # raises AuthConfigError (503) under dev (D4)
    challenge = oidc_login.begin_login(config)
    resp = RedirectResponse(challenge.authorize_url, status_code=302)
    state_cookie = sess.issue_login_state(challenge.state, challenge.nonce, challenge.code_verifier)
    resp.set_cookie(sess.LOGIN_STATE_COOKIE_NAME, state_cookie, httponly=True,
                    max_age=sess.LOGIN_STATE_TTL_S, **_cookie_kwargs(settings))
    return resp


@router.get("/auth/callback")
async def callback(request: Request) -> RedirectResponse:
    settings = get_settings()
    config = oidc_login.config_from_settings(settings)
    code = request.query_params.get("code", "")
    state = request.query_params.get("state", "")
    stash = sess.read_login_state(request)
    if not stash or not code:
        raise InputInvalid("invalid or expired login state", detail={"reason": "login_state"})
    try:
        subject = await oidc_login.complete_login(
            config, code=code, state=state, expected_state=stash["state"],
            nonce=stash["nonce"], code_verifier=stash["cv"],
        )
    except oidc_login.LoginUpstreamError as exc:               # IdP unreachable → VISIBLE 503, no session (not fail-open)
        raise AuthConfigError("identity provider unavailable during login") from exc

    # feature 020: best-effort IdP group → members-store sync on login (no-op for the claim-less pilot IdP, D3).
    # run_login_sync swallows its own errors; guard the provider acquisition too so a sync hiccup never fails login.
    try:
        await sync.run_login_sync(subject, get_active_provider(settings))
    except Exception:                                          # noqa: BLE001 — login must not fail on group-sync
        log.warning("login group-sync dispatch failed (best-effort; login proceeds)", exc_info=True)

    cookie_value, csrf = sess.issue_session(subject, config.issuer, config.audience)
    resp = RedirectResponse(settings.oidc_post_login_path, status_code=302)
    resp.set_cookie(sess.SESSION_COOKIE_NAME, cookie_value, httponly=True,
                    max_age=settings.session_ttl_s, **_cookie_kwargs(settings))
    resp.set_cookie(sess.CSRF_COOKIE_NAME, csrf, httponly=False,       # readable → double-submit
                    max_age=settings.session_ttl_s, **_cookie_kwargs(settings))
    resp.delete_cookie(sess.LOGIN_STATE_COOKIE_NAME, path="/")
    return resp


@router.post("/auth/logout")
async def logout() -> Response:
    resp = Response(status_code=204)
    resp.delete_cookie(sess.SESSION_COOKIE_NAME, path="/")
    resp.delete_cookie(sess.CSRF_COOKIE_NAME, path="/")
    return resp
