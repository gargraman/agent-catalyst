"""Feature 012 (M5) US1 — bearer verification at the API edge (contract §5 items 2–11).

session_principal is overridden to a JwtBearerProvider wired to a LOCAL keypair (offline, FR-015), so the full
request pipeline (provider → AppError → 401 envelope) is exercised without any live IdP. Asserts the 401 matrix,
the auth.required envelope, no token leak, and that a valid token passes authN (reaching tenancy → 403, not 401).
"""
from __future__ import annotations

import contextlib
import uuid

import pytest
from starlette.requests import Request

from api.deps import session_principal
from api.main import app
from tests._api_helpers import client, make_rsa_keypair, mint_test_jwt, build_jwt_provider

pytestmark = pytest.mark.integration


@contextlib.contextmanager
def _use_jwt_provider(public_pem):
    provider = build_jwt_provider(public_pem)

    async def _override(request: Request):
        return await provider.verify(request)

    app.dependency_overrides[session_principal] = _override
    try:
        yield
    finally:
        app.dependency_overrides.pop(session_principal, None)


async def _get_agents(*, token=None, raw=None, ws=None):
    headers = {"X-Workspace-Id": ws or str(uuid.uuid4())}
    if raw is not None:
        headers["Authorization"] = raw
    elif token is not None:
        headers["Authorization"] = f"Bearer {token}"
    async with client() as c:
        return await c.get("/api/v1/agents", headers=headers)


async def test_invalid_tokens_rejected_401_with_envelope_and_no_leak():
    priv, pub = make_rsa_keypair()
    other_priv, _ = make_rsa_keypair()
    cases = {
        "expired": mint_test_jwt(priv, exp_delta=-120),
        "wrong_aud": mint_test_jwt(priv, aud="nope"),
        "wrong_iss": mint_test_jwt(priv, iss="https://evil/iam"),
        "tampered": mint_test_jwt(other_priv),
        "alg_none": mint_test_jwt("", alg="none"),
        "unknown_kid": mint_test_jwt(priv, kid="rotated"),
    }
    with _use_jwt_provider(pub):
        for label, tok in cases.items():
            r = await _get_agents(token=tok)
            assert r.status_code == 401, f"{label}: expected 401, got {r.status_code}"
            assert r.json()["error"]["code"] == "auth.required", label
            assert tok not in r.text, f"{label}: token leaked into response"
        # missing / malformed Authorization
        r = await _get_agents(raw="Token xyz")
        assert r.status_code == 401
        async with client() as c:
            r2 = await c.get("/api/v1/agents", headers={"X-Workspace-Id": str(uuid.uuid4())})
        assert r2.status_code == 401


async def test_valid_token_passes_authn_then_tenancy_denies():
    priv, pub = make_rsa_keypair()
    tok = mint_test_jwt(priv, sub="svc-1")
    with _use_jwt_provider(pub):
        r = await _get_agents(token=tok)                 # valid token, but no membership for the ws
    assert r.status_code == 403                           # authN passed (not 401) → tenancy fail-closed (FR-014)
    assert r.json()["error"]["code"] == "access.denied"
