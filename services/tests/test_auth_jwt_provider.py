"""Feature 012 (M5) US1 — JWT bearer verification, unit level (local keypair, no network, no secret, FR-015).

Exercises the real sig/iss/aud/exp/nbf/alg/kid checks of JwtBearerProvider and asserts every failure class
fails closed with AuthError (401) and never leaks the token (contract §5 items 1–11).
"""
from __future__ import annotations

import pytest
from starlette.requests import Request

from api.auth.base import AuthError
from tests._api_helpers import make_rsa_keypair, mint_test_jwt, build_jwt_provider

pytestmark = pytest.mark.integration  # keypair gen is CPU-only, but keep grouping consistent with the suite


def _req(*, token: str | None = None, raw_header: str | None = None) -> Request:
    headers = []
    if raw_header is not None:
        headers.append((b"authorization", raw_header.encode()))
    elif token is not None:
        headers.append((b"authorization", b"Bearer " + token.encode()))
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers, "query_string": b""})


@pytest.fixture
def keys():
    return make_rsa_keypair()  # (private_pem, public_pem)


async def test_valid_token_resolves_principal(keys):
    priv, pub = keys
    tok = mint_test_jwt(priv, sub="svc-1", scope="xdr.alr.rw xdr.srh.rw")
    p = await build_jwt_provider(pub).verify(_req(token=tok))
    assert p.subject == "svc-1"
    assert p.scopes == ("xdr.alr.rw", "xdr.srh.rw")
    assert p.issuer == "https://test.idp.local/iam"
    assert p.workspace_claims == {}          # role is NOT from the token (FR-013)


async def test_missing_scope_claim_yields_empty_scopes_not_failure(keys):
    priv, pub = keys
    tok = mint_test_jwt(priv, scope="")       # absent/empty scope (spec gap C1) → empty scopes, still valid authN
    p = await build_jwt_provider(pub).verify(_req(token=tok))
    assert p.scopes == ()


async def test_tampered_signature_rejected(keys):
    priv, pub = keys
    other_priv, _ = make_rsa_keypair()
    tok = mint_test_jwt(other_priv)            # signed by a key the resolver does NOT return
    with pytest.raises(AuthError):
        await build_jwt_provider(pub).verify(_req(token=tok))


async def test_wrong_audience_rejected(keys):
    priv, pub = keys
    tok = mint_test_jwt(priv, aud="not-mcafee")
    with pytest.raises(AuthError):
        await build_jwt_provider(pub).verify(_req(token=tok))


async def test_wrong_issuer_rejected(keys):
    priv, pub = keys
    tok = mint_test_jwt(priv, iss="https://evil.example/iam")
    with pytest.raises(AuthError):
        await build_jwt_provider(pub).verify(_req(token=tok))


async def test_expired_beyond_skew_rejected(keys):
    priv, pub = keys
    tok = mint_test_jwt(priv, exp_delta=-120)  # expired well beyond the 60s leeway
    with pytest.raises(AuthError):
        await build_jwt_provider(pub).verify(_req(token=tok))


async def test_not_yet_valid_beyond_skew_rejected(keys):
    priv, pub = keys
    tok = mint_test_jwt(priv, nbf_delta=120)
    with pytest.raises(AuthError):
        await build_jwt_provider(pub).verify(_req(token=tok))


async def test_alg_none_rejected(keys):
    priv, pub = keys
    tok = mint_test_jwt("", alg="none")        # unsigned token
    with pytest.raises(AuthError):
        await build_jwt_provider(pub).verify(_req(token=tok))


async def test_hs256_where_rs256_expected_rejected(keys):
    priv, pub = keys
    tok = mint_test_jwt("x" * 40, alg="HS256")   # alg-confusion attempt
    with pytest.raises(AuthError):
        await build_jwt_provider(pub).verify(_req(token=tok))


async def test_unknown_kid_rejected(keys):
    priv, pub = keys
    tok = mint_test_jwt(priv, kid="rotated-away")
    with pytest.raises(AuthError):
        await build_jwt_provider(pub).verify(_req(token=tok))      # resolver only knows TEST_KID


async def test_missing_and_malformed_authorization_rejected(keys):
    priv, pub = keys
    provider = build_jwt_provider(pub)
    with pytest.raises(AuthError):
        await provider.verify(_req())                       # no header
    with pytest.raises(AuthError):
        await provider.verify(_req(raw_header="Token abc"))  # wrong scheme
    with pytest.raises(AuthError):
        await provider.verify(_req(raw_header="Bearer "))    # empty token


async def test_error_never_contains_the_raw_token(keys):
    priv, pub = keys
    tok = mint_test_jwt(priv, aud="wrong")                  # any failure
    try:
        await build_jwt_provider(pub).verify(_req(token=tok))
        assert False, "expected AuthError"
    except AuthError as e:
        blob = f"{e} {getattr(e, 'detail', None)}"
        assert tok not in blob                              # FR-011: no token leak in the error
