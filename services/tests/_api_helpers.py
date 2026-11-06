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
    Agent, AgentVersion, KnowledgeBase, Member, McpServer, McpTool, RetrievalBinding, Run, Skill,
    SkillBinding, Tool, ToolBinding, Workspace,
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
async def seed_workspace(name: str | None = None) -> str:
    name = name or f"ws-{uuid.uuid4().hex[:8]}"
    async with tenant_session(uuid.uuid4()) as s:        # workspaces = tenant root (no RLS)
        w = Workspace(name=name)
        s.add(w)
        await s.flush()
        return str(w.id)


async def seed_agent(workspace_id: str, name: str = "Seed Agent") -> str:
    async with tenant_session(workspace_id) as s:
        a = Agent(workspace_id=uuid.UUID(workspace_id), name=name, status="draft")
        s.add(a)
        await s.flush()
        return str(a.id)


async def seed_version(workspace_id: str, agent_id: str, label: str = "v1") -> str:
    async with tenant_session(workspace_id) as s:
        v = AgentVersion(
            workspace_id=uuid.UUID(workspace_id), agent_id=uuid.UUID(agent_id), label=label,
            system_prompt="You are a test agent.", prompt_hash="ph-test", snapshot_hash="sh-test",
            model_config_=_MODEL_CONFIG, profile_snapshot=PROFILE_SNAPSHOT, io_contract=_IO_CONTRACT,
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


async def seed_kb(workspace_id: str, slug: str = "kb-a") -> str:
    async with tenant_session(workspace_id) as s:
        kb = KnowledgeBase(workspace_id=uuid.UUID(workspace_id), slug=slug, name=slug.title(),
                           embedding_model="text-embedding-3-small", embedding_dim=1536)
        s.add(kb)
        await s.flush()
        return str(kb.id)


async def seed_member(workspace_id: str, subject: str = "m1", role: str = "builder") -> str:
    async with tenant_session(workspace_id) as s:
        m = Member(workspace_id=uuid.UUID(workspace_id), subject=subject, display_name=subject,
                   email=f"{subject}@example.com", console_role=role)
        s.add(m)
        await s.flush()
        return str(m.id)


async def seed_run(workspace_id: str, source: str = "playground", outcome: str = "ok",
                   cost_usd: float = 0.001) -> str:
    async with tenant_session(workspace_id) as s:
        r = Run(workspace_id=uuid.UUID(workspace_id), model="gpt-5-mini", outcome=outcome,
                source=source, cost_usd=cost_usd, usage={"input": 10, "output": 5, "total": 15}, latency_ms=42)
        s.add(r)
        await s.flush()
        return str(r.id)


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
