"""Shared harness for the thin-API tests (M4, feature 005).

An httpx `ASGITransport` in-process client (no running server); dev-principal/workspace headers; and DB seed
helpers (via the real `tenant_session`, so RLS applies). The runtime/evals provider boundary is mocked by
monkeypatching `runtime.providers.gateway.stream` in each test — no live/paid calls. DB-touching helpers are
used by integration tests (the compose Postgres); pure-contract tests need neither DB nor these seeders.
"""
from __future__ import annotations

import json
import uuid

import httpx

from api.db import tenant_session
from api.main import app
from api.models import (
    Agent, AgentVersion, ApiToken, KnowledgeBase, Member, McpServer, McpTool, ProviderKey, RetrievalBinding,
    Run, Skill, SkillBinding, Tool, ToolBinding, Workspace, WorkspaceSettings,
)
from tests._invoke_helpers import PROFILE_SNAPSHOT

_IO_CONTRACT = {
    "input_schema": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]},
    "output_format": "text",
    "output_schema": None,
}
_MODEL_CONFIG = {"provider": "openai", "model": "gpt-5-mini", "params": {"temperature": 0.2, "max_tokens": 256}}


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def dev_headers(*, workspace: str, subject: str = "u1", claims: dict | None = None,
                idempotency_key: str | None = None, if_match: str | None = None) -> dict:
    """Headers for a dev-stub authenticated request. `claims` defaults to builder on the selected workspace."""
    claims = claims if claims is not None else {workspace: "builder"}
    h = {
        "X-Dev-Principal": json.dumps({"subject": subject, "workspace_claims": claims}),
        "X-Workspace-Id": workspace,
    }
    if idempotency_key:
        h["Idempotency-Key"] = idempotency_key
    if if_match:
        h["If-Match"] = if_match
    return h


# ---- DB seeders (integration; via the real tenant_session so RLS applies) ------------------------
async def seed_workspace(name: str | None = None, idp_binding: dict | None = None) -> str:
    name = name or f"ws-{uuid.uuid4().hex[:8]}"
    async with tenant_session(uuid.uuid4()) as s:        # workspaces = tenant root (no RLS)
        w = Workspace(name=name, idp_binding=idp_binding or {})   # feature 020: optional group→ref binding
        s.add(w)
        await s.flush()
        return str(w.id)


async def seed_agent(workspace_id: str, name: str = "Seed Agent", access_policy: dict | None = None) -> str:
    async with tenant_session(workspace_id) as s:
        a = Agent(workspace_id=uuid.UUID(workspace_id), name=name, status="draft",
                  access_policy=access_policy or {"visibility": "workspace"})
        s.add(a)
        await s.flush()
        return str(a.id)


async def seed_version(workspace_id: str, agent_id: str, label: str = "v1",
                       io_contract: dict | None = None) -> str:
    async with tenant_session(workspace_id) as s:
        v = AgentVersion(
            workspace_id=uuid.UUID(workspace_id), agent_id=uuid.UUID(agent_id), label=label,
            system_prompt="You are a test agent.", prompt_hash="ph-test", snapshot_hash="sh-test",
            model_config_=_MODEL_CONFIG, profile_snapshot=PROFILE_SNAPSHOT,
            io_contract=io_contract or _IO_CONTRACT,
        )
        s.add(v)
        await s.flush()
        # link it as the agent's active version so a no-version-id invoke resolves it
        agent = await s.get(Agent, uuid.UUID(agent_id))
        agent.active_version_id = v.id
        await s.flush()
        return str(v.id)


# ---- M4-remainder seeders (feature 006; via tenant_session so RLS applies) ----------------------
async def seed_tool(workspace_id: str, name: str = "repo-tool", http_config: dict | None = None) -> str:
    async with tenant_session(workspace_id) as s:
        t = Tool(workspace_id=uuid.UUID(workspace_id), name=name, description="seed",
                 kind="http", http_config=http_config or {"endpoint": "https://x.example"}, risk_tier="read")
        s.add(t)
        await s.flush()
        return str(t.id)


async def seed_skill(workspace_id: str, slug: str = "skill-a", status: str = "draft") -> str:
    async with tenant_session(workspace_id) as s:
        sk = Skill(workspace_id=uuid.UUID(workspace_id), slug=slug, name=slug.title(),
                   instructions="do the thing", resources=[], content_hash="ch-0", status=status)
        s.add(sk)
        await s.flush()
        return str(sk.id)


async def seed_mcp_server(workspace_id: str, name: str = "mcp-a", with_tool: bool = False) -> str:
    async with tenant_session(workspace_id) as s:
        m = McpServer(workspace_id=uuid.UUID(workspace_id), name=name, url="https://mcp.example")
        s.add(m)
        await s.flush()
        if with_tool:
            s.add(McpTool(workspace_id=uuid.UUID(workspace_id), server_id=m.id, name="t1"))
            await s.flush()
        return str(m.id)


async def seed_kb(workspace_id: str, slug: str = "kb-a", access_policy: dict | None = None) -> str:
    async with tenant_session(workspace_id) as s:
        kb = KnowledgeBase(workspace_id=uuid.UUID(workspace_id), slug=slug, name=slug.title(),
                           embedding_model="text-embedding-3-small", embedding_dim=1536,
                           access_policy=access_policy or {"visibility": "workspace"})
        s.add(kb)
        await s.flush()
        return str(kb.id)


async def seed_member(workspace_id: str, subject: str = "m1", role: str = "builder",
                      teams: list[str] | None = None) -> str:
    async with tenant_session(workspace_id) as s:
        m = Member(workspace_id=uuid.UUID(workspace_id), subject=subject, display_name=subject,
                   email=f"{subject}@example.com", console_role=role, teams=teams or [])
        s.add(m)
        await s.flush()
        return str(m.id)


async def seed_run(workspace_id: str, source: str = "playground", outcome: str = "ok",
                   cost_usd: float = 0.001, created_at=None) -> str:
    async with tenant_session(workspace_id) as s:
        r = Run(workspace_id=uuid.UUID(workspace_id), model="gpt-5-mini", outcome=outcome,
                source=source, cost_usd=cost_usd, usage={"input": 10, "output": 5, "total": 15}, latency_ms=42)
        if created_at is not None:
            r.created_at = created_at                      # feature 009: seed a prior-month run for MTD-window tests
        s.add(r)
        await s.flush()
        return str(r.id)


async def seed_provider_key(workspace_id: str, provider: str = "openai", label: str = "default",
                            masked: str = "sk-pro••••••••WXYZ", vault_path: str = "local://seed/none/0",
                            status: str = "active", region: str | None = None) -> str:
    """Seed a provider_keys row directly (via tenant_session so RLS applies). `vault_path` is a stub ref —
    resolve tests that need a real plaintext use the custody capture path or the local provider."""
    async with tenant_session(workspace_id) as s:
        k = ProviderKey(workspace_id=uuid.UUID(workspace_id), provider=provider, label=label,
                        masked=masked, vault_path=vault_path, status=status, region=region)
        s.add(k)
        await s.flush()
        return str(k.id)


async def seed_api_token(workspace_id: str, agent_id: str, secret: str, name: str = "seed-token",
                         teams: list[str] | None = None, created_at=None, expires_at=None,
                         revoked_at=None, created_by=None) -> str:
    """Seed an api_tokens row directly (via tenant_session so RLS applies). Stores only sha256(secret)+prefix —
    the raw `secret` is the caller's bearer, never persisted (feature 018). Returns the token id. Pass
    `created_at` in the past (with a later-but-still-past `expires_at`) to model an already-expired token
    without violating the CHECK (expires_at > created_at)."""
    from api.publish.token_auth import hash_secret
    async with tenant_session(workspace_id) as s:
        t = ApiToken(workspace_id=uuid.UUID(workspace_id), agent_id=uuid.UUID(agent_id), name=name,
                     token_hash=hash_secret(secret), prefix=secret[:12],
                     scopes={"invoke": True, "teams": teams or []},
                     expires_at=expires_at, revoked_at=revoked_at, created_by=created_by)
        if created_at is not None:
            t.created_at = created_at
        s.add(t)
        await s.flush()
        return str(t.id)


async def seed_limits(workspace_id: str, monthly_spend_usd) -> None:
    """Feature 009: set the workspace's monthly spend cap (WorkspaceSettings.limits.monthlySpendUsd)."""
    async with tenant_session(workspace_id) as s:
        ws = await s.get(WorkspaceSettings, uuid.UUID(workspace_id))
        if ws is None:
            s.add(WorkspaceSettings(workspace_id=uuid.UUID(workspace_id), default_provider="",
                                    default_model="", limits={"monthlySpendUsd": monthly_spend_usd},
                                    eval_policy={}))
        else:
            ws.limits = {**(ws.limits or {}), "monthlySpendUsd": monthly_spend_usd}
        await s.flush()


async def seed_tool_binding(workspace_id: str, version_id: str, tool_id: str) -> None:
    """Bind a registry tool into an agent version (so drift can flag the owning agent)."""
    async with tenant_session(workspace_id) as s:
        s.add(ToolBinding(workspace_id=uuid.UUID(workspace_id), version_id=uuid.UUID(version_id),
                          kind="http", tool_id=uuid.UUID(tool_id), schema_snapshot={}, config_snapshot={}))
        await s.flush()


async def seed_skill_binding(workspace_id: str, version_id: str, skill_id: str) -> None:
    async with tenant_session(workspace_id) as s:
        s.add(SkillBinding(workspace_id=uuid.UUID(workspace_id), version_id=uuid.UUID(version_id),
                           skill_id=uuid.UUID(skill_id), skill_content_hash="ch-0"))
        await s.flush()


async def seed_retrieval_binding(workspace_id: str, version_id: str, kb_id: str) -> None:
    async with tenant_session(workspace_id) as s:
        s.add(RetrievalBinding(workspace_id=uuid.UUID(workspace_id), version_id=uuid.UUID(version_id),
                               kb_id=uuid.UUID(kb_id), kb_index_version=1, retrieval_config={}, injection="tool"))
        await s.flush()


def parse_sse(body: str) -> list[tuple[str, dict]]:
    """Parse an SSE response body into a list of (event, data-dict)."""
    frames: list[tuple[str, dict]] = []
    event = None
    for line in body.splitlines():
        if line.startswith("event:"):
            event = line[len("event:"):].strip()
        elif line.startswith("data:") and event is not None:
            frames.append((event, json.loads(line[len("data:"):].strip())))
            event = None
    return frames


# ---- JWT test helpers (feature 012) — LOCAL keys only: no live IdP, no real secret in the suite (FR-015) ----
import datetime as _dt

import jwt as _jwt
from cryptography.hazmat.primitives import serialization as _ser
from cryptography.hazmat.primitives.asymmetric import rsa as _rsa

TEST_ISSUER = "https://test.idp.local/iam"
TEST_AUDIENCE = "mcafee"
TEST_KID = "test-key-1"


def make_rsa_keypair() -> tuple[bytes, bytes]:
    """Return (private_pem, public_pem) for signing/verifying test JWTs. Ephemeral per call."""
    key = _rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(_ser.Encoding.PEM, _ser.PrivateFormat.PKCS8, _ser.NoEncryption())
    pub = key.public_key().public_bytes(_ser.Encoding.PEM, _ser.PublicFormat.SubjectPublicKeyInfo)
    return priv, pub


def mint_test_jwt(private_pem, *, kid: str = TEST_KID, sub: str = "svc-1", aud: str = TEST_AUDIENCE,
                  iss: str = TEST_ISSUER, scope: str = "xdr.alr.rw xdr.srh.rw", exp_delta: int = 600,
                  nbf_delta: int = 0, alg: str = "RS256", include=("exp", "iss", "aud", "nbf", "iat"),
                  extra=None) -> str:
    now = _dt.datetime.now(_dt.timezone.utc)
    claims: dict = {"sub": sub, "scope": scope}
    if "iat" in include: claims["iat"] = int(now.timestamp())
    if "nbf" in include: claims["nbf"] = int((now + _dt.timedelta(seconds=nbf_delta)).timestamp())
    if "exp" in include: claims["exp"] = int((now + _dt.timedelta(seconds=exp_delta)).timestamp())
    if "iss" in include: claims["iss"] = iss
    if "aud" in include: claims["aud"] = aud
    if extra: claims.update(extra)
    return _jwt.encode(claims, private_pem, algorithm=alg, headers={"kid": kid})


def local_key_resolver(public_pem, *, known_kids=(TEST_KID,)):
    """A key resolver over a fixed local key set; raises on an unknown kid (→ provider 401)."""
    def resolve(token: str):
        kid = _jwt.get_unverified_header(token).get("kid")
        if kid not in known_kids:
            raise KeyError(f"unknown kid: {kid}")
        return public_pem
    return resolve


def build_jwt_provider(public_pem, *, issuer: str = TEST_ISSUER, audience: str = TEST_AUDIENCE,
                      algs=("RS256",), leeway: int = 60, known_kids=(TEST_KID,)):
    """A JwtBearerProvider wired to a LOCAL key set (offline). id='trellix-iam' so it stands in for the real one."""
    from api.auth.jwt_provider import JwtBearerProvider
    return JwtBearerProvider(id="trellix-iam", issuer=issuer, audience=audience, algorithms=list(algs),
                             leeway=leeway, key_resolver=local_key_resolver(public_pem, known_kids=known_kids))
