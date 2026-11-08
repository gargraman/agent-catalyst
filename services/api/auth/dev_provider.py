"""Dev/test IdP provider (feature 012).

Trusts the `X-Dev-Principal` JSON header VERBATIM — it is UNVERIFIED and exists only for local development and
the test suite. The registry refuses to return it under a production profile (`AC_ENV=prod`, FR-008), so the
unverified path is unreachable in production. Preserves the exact pre-012 principal shape so existing tests are
unchanged (research R4).
"""
from __future__ import annotations

import json

from starlette.requests import Request

from api.auth.base import AuthError
from api.deps import Principal


class DevHeaderProvider:
    id = "dev"

    async def verify(self, request: Request) -> Principal:
        raw = request.headers.get("X-Dev-Principal")
        if not raw:
            raise AuthError("authentication required")
        try:
            d = json.loads(raw)
            return Principal(subject=str(d["subject"]), workspace_claims=dict(d.get("workspace_claims", {})))
        except (ValueError, KeyError, TypeError) as exc:
            raise AuthError("invalid session principal") from exc
