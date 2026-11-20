"""`resolve_caller` — the per-invoke auth dependency for the published plane (feature 018).

Resolution order (contract caller-token.md §"Caller auth resolution"), fail-closed at each step:
  1. `sha256(bearer)` lookup in `api_tokens`; unknown or `revoked_at` set → 401.
  2. `expires_at` in the past → 401 (FR-015).
  3. deny-set check (`deny:tok:{id}`, `deny:sub:{subject}`) → match → 401. Redis down → 401 (fail closed).
  4. per-token rate limit (`rl:token:{id}`, reusing the 016 engine) → over → 429.
  5. build a `CallerCtx` (workspace pinned by the token, role `executer`, teams from `scopes.teams`).

The raw secret is never stored or logged — only `sha256(secret)` + a display prefix persist (FR-010/011).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
from dataclasses import dataclass, field

from sqlalchemy import text

from api.auth.base import AuthError
from api.errors import RateLimited
from api.settings import get_settings


@dataclass(frozen=True)
class CallerCtx:
    """A resolved caller-token principal for the published-invoke plane. `role` is always `executer` (a plane
    role, not a console RBAC role); authorization is the per-resource access policy keyed on `teams`. `subject`
    is a synthetic `token:{id}` identity (a token is not a user) — distinct from any console subject so an admin
    `revoke-user {subject}` never collides with a token and vice-versa."""
    workspace_id: str
    token_id: str
    agent_id: str
    subject: str
    role: str = "executer"
    teams: tuple[str, ...] = field(default_factory=tuple)


def hash_secret(secret: str) -> str:
    """Non-reversible token fingerprint stored in `api_tokens.token_hash` (never the raw secret, FR-010)."""
    return hashlib.sha256(secret.encode()).hexdigest()


def _bearer(request) -> str:
    """Extract the bearer credential, or raise 401. Never log/echo the value (FR-011)."""
    header = request.headers.get("authorization") or ""
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        raise AuthError("missing or malformed bearer token")
    return value.strip()


async def resolve_caller(request, session) -> CallerCtx:
    """Authenticate a published-invoke caller by its bearer caller-token. See module docstring for the order.

    `session` is the UNSCOPED auth-lookup session (`db.auth_lookup_session`): the only read it performs is the
    `api_token_resolve(hash)` SECURITY DEFINER function, which returns the single row for a globally-unique hash.
    """
    from api.auth import denyset  # lazy: keep this module importable without the redis client

    secret = _bearer(request)
    row = (await session.execute(
        text("SELECT id, workspace_id, agent_id, scopes, expires_at, revoked_at "
             "FROM api_token_resolve(:h)"),
        {"h": hash_secret(secret)},
    )).mappings().first()

    # 1. unknown or revoked → 401 (no token value echoed)
    if row is None or row["revoked_at"] is not None:
        raise AuthError("invalid or revoked caller token")
    # 2. expired → 401 (FR-015)
    expires_at = row["expires_at"]
    if expires_at is not None and expires_at <= _dt.datetime.now(_dt.timezone.utc):
        raise AuthError("caller token expired")

    token_id = str(row["id"])
    subject = f"token:{token_id}"
    # 3. deny-set → 401 (match → revoked; Redis configured-but-down → raise → fail closed, D2/FR-024b)
    try:
        revoked = await denyset.is_denied(subject, token_id)
    except Exception as e:  # noqa: BLE001 — deny-set unreachable → fail closed (no secret in the reason, P5)
        raise AuthError("authorization store unavailable") from e
    if revoked:
        raise AuthError("caller token revoked")

    # 4. per-token rate limit (reuses the 016 engine, now keyed per token not per principal). Honors the same
    #    `rate_limit_enabled` switch as the console `rate_limit` dep so disabling the limiter disables BOTH
    #    planes — otherwise a deployment with Redis configured only for the deny-set would silently keep
    #    rate-limiting published callers after the operator turned the limiter off.
    settings = get_settings()
    if settings.rate_limit_enabled:
        from api import ratelimit  # lazy: avoids importing the redis client at module load
        window_s, limit = settings.rate_limit_for("invoke")
        decision = await ratelimit.check(f"rl:token:{token_id}", limit, window_s)
        if not decision.degraded and not decision.allowed:
            raise RateLimited(f"rate limit exceeded; retry after {decision.retry_after}s",
                              headers={"Retry-After": str(decision.retry_after),
                                       "X-RateLimit-Limit": str(limit),
                                       "X-RateLimit-Remaining": str(decision.remaining),
                                       "X-RateLimit-Reset": str(decision.reset_epoch)})

    # 5. build the caller context — workspace pinned by the token, teams from scopes (access-policy source)
    scopes = row["scopes"] or {}
    teams = tuple(str(t) for t in (scopes.get("teams") or []))
    return CallerCtx(workspace_id=str(row["workspace_id"]), token_id=token_id,
                     agent_id=str(row["agent_id"]), subject=subject, teams=teams)
