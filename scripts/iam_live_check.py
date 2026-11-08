"""OPT-IN live Trellix-IAM check for feature 012 — NOT part of the automated test suite (FR-015/SC-007).

Fetches a real client-credentials token from Trellix IAM (creds read from gitignored mvp/.env) and verifies it
end-to-end through the SAME `JwtBearerProvider` the API uses, against the live JWKS. Proves the `trellix-iam`
provider accepts a real token. NEVER prints the token or any secret — only the masked token + resolved subject
and scopes.

Usage (from repo root, with network to preprod IAM):
    python3 scripts/iam_live_check.py mvp/.env
"""
from __future__ import annotations

import asyncio
import base64
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))

from api.auth.jwt_provider import JwtBearerProvider, make_jwks_resolver  # noqa: E402
from starlette.requests import Request  # noqa: E402

DEFAULTS = {
    "issuer": "https://preprod.iam.cloud.trellix.com/iam/v1.0",
    "audience": "mcafee",
    "jwks_uri": "https://preprod.iam.cloud.trellix.com/iam/v1.0/jwks",
    "token_endpoint": "https://preprod.iam.cloud.trellix.com/iam/v1.0/token",
}


def _load_env(path: str) -> dict:
    env = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
                v = v[1:-1]
            env[k.strip()] = v
    return env


def _fetch_token(env: dict) -> str:
    url = env.get("TOKEN_ENDPOINT", DEFAULTS["token_endpoint"])
    body = {"grant_type": "client_credentials", "scope": env.get("IAM_SCOPES") or env.get("IAM_SCOPE", "")}
    basic = base64.b64encode(f"{env['IAM_CLIENT_ID']}:{env['IAM_CLIENT_SECRET']}".encode()).decode()
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(body).encode(),
        headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        import json
        return json.loads(resp.read())["access_token"]


def _mask(tok: str) -> str:
    return f"{tok[:8]}…{tok[-4:]} (len={len(tok)})"


async def main() -> int:
    env_path = sys.argv[1] if len(sys.argv) > 1 else "mvp/.env"
    env = _load_env(env_path)
    token = _fetch_token(env)
    print(f"live token obtained: {_mask(token)}")

    provider = JwtBearerProvider(
        id="trellix-iam",
        issuer=env.get("IAM_ISSUER", DEFAULTS["issuer"]),
        audience=env.get("IAM_AUDIENCE", DEFAULTS["audience"]),
        algorithms=["RS256"],
        leeway=60,
        key_resolver=make_jwks_resolver(env.get("IAM_JWKS_URI", DEFAULTS["jwks_uri"])),
    )
    request = Request({"type": "http", "method": "GET", "path": "/", "query_string": b"",
                       "headers": [(b"authorization", b"Bearer " + token.encode())]})
    principal = await provider.verify(request)
    print(f"VERIFIED via JwtBearerProvider ✓  subject={principal.subject}  scopes={principal.scopes}")
    print("(note: AC role would come from the members table for the caller's workspace — FR-013)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
