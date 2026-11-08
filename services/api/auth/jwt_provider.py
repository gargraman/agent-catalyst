"""JWT bearer IdP provider (feature 012) — the real-token path.

Verifies an inbound `Authorization: Bearer <jwt>` against a provider's signing keys: signature + `iss` + `aud`
+ temporal claims within a bounded skew, accepting only an explicit algorithm allow-list (never the token's own
`alg` header — closes alg-confusion / `alg=none`, FR-004). The signing-key source is INJECTABLE: production uses
a JWKS client (fetch+cache, refresh-once on unknown `kid`, FR-017); tests inject a local resolver so the suite
runs offline with no secret (FR-015). Normalizes to a `Principal` carrying subject + scopes + issuer; the AC
role is NOT taken from the token (resolved from the membership store downstream, FR-013). Never logs the token
(FR-011).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import jwt

from starlette.requests import Request

from api.auth.base import AuthError
from api.deps import Principal

# token -> signing key. Raises on an unresolvable key (unknown kid / JWKS unavailable) — caller maps to 401.
SigningKeyResolver = Callable[[str], Any]


class JwtBearerProvider:
    def __init__(self, *, id: str, issuer: str, audience: str, algorithms: list[str],
                 leeway: int, key_resolver: SigningKeyResolver) -> None:
        self.id = id
        self._issuer = issuer
        self._audience = audience
        self._algorithms = list(algorithms)
        self._leeway = leeway
        self._resolve_key = key_resolver

    async def verify(self, request: Request) -> Principal:
        token = _bearer(request)
        try:
            key = self._resolve_key(token)
        except AuthError:
            raise
        except Exception as exc:                              # unknown kid / JWKS fetch failure → fail closed
            raise AuthError("could not resolve signing key", detail={"reason": "unresolved_key"}) from exc
        try:
            claims = jwt.decode(
                token, key,
                algorithms=self._algorithms,                 # explicit allow-list; token-header alg never trusted
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway,
                options={"require": ["exp", "iss", "aud"]},
            )
        except jwt.PyJWTError as exc:
            # reason is the exception CLASS name only (e.g. ExpiredSignatureError) — non-sensitive, no token.
            raise AuthError("token verification failed", detail={"reason": type(exc).__name__}) from exc
        sub = claims.get("sub")
        if not sub:
            raise AuthError("token missing subject", detail={"reason": "no_sub"})
        scope = claims.get("scope") or ""                    # absent/empty scope → empty scopes, NOT a failure
        scopes = tuple(scope.split()) if isinstance(scope, str) else tuple(scope)
        return Principal(subject=str(sub), workspace_claims={}, scopes=scopes, issuer=claims.get("iss"))


def _bearer(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise AuthError("missing bearer token", detail={"reason": "no_bearer"})
    token = header[len("Bearer "):].strip()
    if not token:
        raise AuthError("empty bearer token", detail={"reason": "empty_bearer"})
    return token


def make_jwks_resolver(jwks_uri: str) -> SigningKeyResolver:
    """Production key source: a cached JWKS client. `PyJWKClient` fetches + caches keys and re-fetches on an
    unknown `kid` (refresh-once semantics), then raises if still absent — which `verify` maps to 401 (FR-017)."""
    client = jwt.PyJWKClient(jwks_uri)

    def resolve(token: str) -> Any:
        return client.get_signing_key_from_jwt(token).key

    return resolve
