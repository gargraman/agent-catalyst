"""OIDC authorization-code login client (feature 019).

Drives the browser auth-code flow against the org IdP: `begin_login` builds the authorize URL (PKCE S256 +
`state` + `nonce`, via authlib's RFC 7636 helpers); `complete_login` validates `state`/`nonce`, exchanges the
`code` at the token endpoint, and verifies the returned `id_token` against the **same** `iam_issuer/iam_audience/
iam_jwks_uri` trust config the `trellix-iam` provider uses (feature 012) — swapping IdP vendors stays a config
change (FR-005). Returns the subject. IdP-unreachable at the exchange raises `LoginUpstreamError` → the router
maps it to a visible 503 (never fail-open).

**authlib** supplies the security-critical PKCE S256 challenge + CSPRNG `state`/`nonce` (D3). The code→token
exchange is a thin `httpx` POST and the `id_token` verify reuses the already-trusted 012 pyjwt/JWKS path — both
behind INJECTABLE seams (`_new_http_client`, `_resolve_signing_key`) so the suite runs fully offline (no live/paid
call). Endpoint discovery is derived from the issuer here; a production refinement is an OIDC discovery fetch.
This module logs nothing (no token/secret, FR-011).
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import jwt
from authlib.common.security import generate_token
from authlib.oauth2.rfc7636 import create_s256_code_challenge

from starlette.requests import Request  # noqa: F401 — kept for type context / parity with the auth package

from api.auth.base import AuthError
from api.auth.registry import AuthConfigError


class LoginUpstreamError(Exception):
    """The IdP token endpoint (or JWKS) was unreachable/failed during the exchange. The router maps this to a
    user-visible 503 — login fails CLOSED, never fail-open (edge case)."""


@dataclass(frozen=True)
class OIDCConfig:
    client_id: str
    client_secret: str
    authorize_endpoint: str
    token_endpoint: str
    jwks_uri: str
    issuer: str
    audience: str
    redirect_uri: str
    allowed_algs: tuple[str, ...] = ("RS256",)   # id_token sig algs; the token-header alg is never trusted


@dataclass(frozen=True)
class LoginChallenge:
    """The begin_login result: the IdP authorize URL + the secrets the callback must echo back. `state`/`nonce`/
    `code_verifier` are stashed by the router in a short signed cookie and re-supplied to `complete_login`."""
    authorize_url: str
    state: str
    nonce: str
    code_verifier: str


def config_from_settings(settings) -> OIDCConfig:
    """Build the OIDC client config from settings (reusing the 012 iam_* trust config), or raise
    `AuthConfigError` when interactive login is not configured for this deployment (D4 — the dev profile leaves
    the OIDC client fields unset, so login is cleanly disabled rather than half-working)."""
    cid, csecret, redirect = settings.oidc_client_id, settings.oidc_client_secret, settings.oidc_redirect_uri
    if not (cid and csecret and redirect):
        raise AuthConfigError("interactive console login is not configured for this deployment")
    issuer = (settings.iam_issuer or "").rstrip("/")
    if not issuer:
        raise AuthConfigError("interactive console login requires an IdP issuer")
    return OIDCConfig(
        client_id=cid, client_secret=csecret,
        authorize_endpoint=f"{issuer}/authorize",
        token_endpoint=f"{issuer}/token",
        jwks_uri=settings.iam_jwks_uri,
        issuer=settings.iam_issuer,
        audience=settings.iam_audience or cid,
        redirect_uri=redirect,
        allowed_algs=tuple(a.strip() for a in settings.iam_allowed_algs.split(",") if a.strip()) or ("RS256",),
    )


def begin_login(config: OIDCConfig) -> LoginChallenge:
    """Build the authorize URL with PKCE S256 + `state` + `nonce` (authlib CSPRNG + RFC 7636 challenge)."""
    state = generate_token(24)
    nonce = generate_token(24)
    verifier = generate_token(48)
    challenge = create_s256_code_challenge(verifier)
    params = {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "scope": "openid profile email",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    sep = "&" if "?" in config.authorize_endpoint else "?"
    url = f"{config.authorize_endpoint}{sep}{urlencode(params)}"
    return LoginChallenge(authorize_url=url, state=state, nonce=nonce, code_verifier=verifier)


async def complete_login(config: OIDCConfig, *, code: str, state: str, expected_state: str,
                         nonce: str, code_verifier: str, http_client=None, key_resolver=None) -> str:
    """Validate `state`, exchange `code`→tokens, verify the `id_token` (sig via JWKS + iss/aud/nonce), return the
    subject. `AuthError` on a bad state/nonce/id_token; `LoginUpstreamError` on an unreachable IdP (→ 503)."""
    if not state or not expected_state or not secrets.compare_digest(str(state), str(expected_state)):
        raise AuthError("oidc state mismatch", detail={"reason": "state_mismatch"})
    try:
        token_resp = await _post_token(config, code, code_verifier, http_client)
    except LoginUpstreamError:
        raise
    except Exception as exc:                               # any transport/HTTP failure → fail CLOSED visibly (503)
        raise LoginUpstreamError("token endpoint exchange failed") from exc
    id_token = token_resp.get("id_token")
    if not id_token:
        raise AuthError("no id_token in token response", detail={"reason": "no_id_token"})
    return _verify_id_token(config, id_token, nonce, key_resolver)


# ---- injectable seams (stubbed offline in tests) -------------------------------------------------
def _new_http_client():
    """A fresh async HTTP client for the token exchange. Overridden in tests for an offline stub IdP."""
    import httpx
    return httpx.AsyncClient(timeout=10.0)


def _resolve_signing_key(config: OIDCConfig, id_token: str):
    """Resolve the IdP signing key for `id_token` via a cached JWKS client (same source as the 012 provider).
    Overridden in tests to return a local public key (offline)."""
    client = jwt.PyJWKClient(config.jwks_uri)
    return client.get_signing_key_from_jwt(id_token).key


async def _post_token(config: OIDCConfig, code: str, code_verifier: str, http_client) -> dict:
    client = http_client or _new_http_client()
    owns = http_client is None
    try:
        resp = await client.post(config.token_endpoint, data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config.redirect_uri,
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "code_verifier": code_verifier,
        })
        resp.raise_for_status()
        return resp.json()
    finally:
        if owns:
            await client.aclose()


def _verify_id_token(config: OIDCConfig, id_token: str, nonce: str, key_resolver) -> str:
    resolve = key_resolver or _resolve_signing_key
    try:
        key = resolve(config, id_token)
    except Exception as exc:                               # JWKS fetch failure → upstream (503), not a bad token
        raise LoginUpstreamError("jwks unavailable") from exc
    # OIDC Core §2 mandates the id_token `aud` CONTAIN the client_id — that is the primary, spec-correct check.
    # We also accept the configured resource audience (`config.audience`, the 012 access-token audience) in case
    # the IdP stamps it too; pyjwt passes when ANY listed audience matches and still requires the claim present,
    # so this is strictly more correct than verifying the resource audience alone (which rejects a conformant
    # id_token). The exact Trellix-IAM id_token aud is unverified → accepting both avoids gambling on it.
    accepted_aud = [a for a in dict.fromkeys([config.client_id, config.audience]) if a]
    try:
        claims = jwt.decode(
            id_token, key,
            algorithms=list(config.allowed_algs),         # explicit allow-list; token-header alg never trusted
            audience=accepted_aud, issuer=config.issuer,
            options={"require": ["exp", "iss", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthError("id_token verification failed", detail={"reason": type(exc).__name__}) from exc
    if nonce and claims.get("nonce") != nonce:            # replay / mismatched login → reject
        raise AuthError("id_token nonce mismatch", detail={"reason": "nonce_mismatch"})
    sub = claims.get("sub")
    if not sub:
        raise AuthError("id_token missing subject", detail={"reason": "no_sub"})
    return str(sub)
