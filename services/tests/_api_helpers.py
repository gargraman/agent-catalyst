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
from api.models import Agent, AgentVersion, Workspace
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
