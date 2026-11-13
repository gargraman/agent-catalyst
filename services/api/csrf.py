"""Double-submit CSRF guard (feature 019).

An ASGI middleware (one choke point — a new route can't forget it) that, on state-changing requests
authenticated by a **session cookie**, requires a readable `csrf` cookie matching the `X-CSRF-Token` header **and**
matching the `csrf` claim bound into the signed session cookie; any mismatch/absent → `403 csrf.invalid`.

The session binding is what makes this a *signed* double-submit rather than a plain one: an attacker who can plant
a cookie on the victim (sibling subdomain / MITM — our cookies carry no `__Host-` prefix) could set `ac_csrf=X` and
send `X-CSRF-Token: X`, defeating cookie==header alone. Requiring the header to also equal the HS256-signed
session's `csrf` closes that — the attacker cannot forge a session JWT carrying their chosen token.

Only cookie-authenticated requests are at CSRF risk (CSRF abuses the browser's AMBIENT cookie credential). A
request with no session cookie — a bearer/caller-token or the dev-header path, including the entire existing test
suite — carries no ambient credential and is NOT gated (D2/D5 regression safety). Exempt regardless: safe methods,
the published `/v1` caller-token plane (bearer, not cookie), `/auth/login|callback`, and `/healthz|/readyz|/metrics`.
NOTE: `/auth/logout` is deliberately NOT exempt — it is a cookie-authenticated state change, so the SPA MUST read
the readable `ac_csrf` cookie and send it as `X-CSRF-Token` on logout, or the request is refused `403 csrf.invalid`.

Runs OUTSIDE the AppError exception handler (middleware wraps the app), so it builds the 403 envelope from the
`CsrfInvalid` class directly. Reads only scope-derived cookies/headers (never the body), so `receive` is untouched.
"""
from __future__ import annotations

import hmac

from fastapi.responses import JSONResponse
from starlette.requests import Request

from api.auth.session import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME, read_session
from api.errors import CsrfInvalid

# Methods that never mutate state → never CSRF-checked.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

# Path prefixes exempt from CSRF: the published bearer plane and unauthenticated meta/login endpoints.
EXEMPT_PREFIXES: tuple[str, ...] = (
    "/v1/",            # feature 018 published caller-token invoke plane (bearer, not cookie)
    "/auth/login",     # the login redirect itself
    "/auth/callback",  # the IdP redirect-back (no session cookie yet; carries its own state/nonce)
    "/healthz",
    "/readyz",
    "/metrics",
)


def _is_exempt_path(path: str) -> bool:
    return any(path.startswith(p) for p in EXEMPT_PREFIXES)


class CSRFMiddleware:
    """Pure-ASGI double-submit CSRF guard (feature 019, D5)."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)                               # cookies/headers only — body (receive) untouched
        if self._needs_check(request):
            cookie = request.cookies.get(CSRF_COOKIE_NAME)
            header = request.headers.get("X-CSRF-Token")
            # Bind the double-submit token to THIS session: header must equal the readable csrf cookie AND the
            # `csrf` claim in the signed session cookie. The session read is signature-verified (read_session), so
            # a planted cookie+header that don't match the real session's token are rejected. A missing/invalid
            # session on a state change (claims is None) also fails closed here.
            claims = read_session(request)
            if (not cookie or not header
                    or not hmac.compare_digest(cookie, header)
                    or claims is None
                    or not hmac.compare_digest(header, claims.csrf)):
                err = CsrfInvalid("CSRF token missing or does not match")
                resp = JSONResponse(status_code=err.http_status, content=err.envelope())
                await resp(scope, receive, send)
                return
        await self.app(scope, receive, send)

    @staticmethod
    def _needs_check(request: Request) -> bool:
        if request.method in SAFE_METHODS:
            return False
        if _is_exempt_path(request.url.path):
            return False
        # Only cookie-authenticated requests carry an ambient credential CSRF can abuse.
        return SESSION_COOKIE_NAME in request.cookies
