"""Feature 026 US2 (T030, research D3) — console invoke `target`: run the DRAFT, the active version, or a specific
version through the SAME `_run_invoke` → `runtime.invoke` path (P6).

- `target:"draft"` runs the agent's saved draft as a transient (never-persisted) snapshot. The run row has
  `version_id IS NULL`; `run_start.version == "draft"`.
- `target:"version"` requires `version_id` (else 400); another agent's version → 404; `run_start.version` is the label.
- `target` defaults to `"active"`.
- The published plane has no `target` field: a `target:"draft"` there is ignored and never runs the draft.
- A draft naming a binding that no longer exists → 404 with `detail.binding` (FR-005), before any provider call.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from api.db import tenant_session
from api.models import AgentVersion, Run
from runtime.providers import gateway
from tests._api_helpers import client, dev_headers, parse_sse, seed_api_token, seed_workspace, set_draft
from tests._invoke_helpers import stop_ev, text_ev, usage_ev

pytestmark = pytest.mark.integration

IO = {"input_schema": {"type": "object", "properties": {"message": {"type": "string"}}}, "output_format": "text"}
ACTIVE_PROMPT = "You are the ACTIVE version."
DRAFT_PROMPT = "You are the DRAFT."
INPUTS = {"message": "hi"}


def _recording_gateway():
    """gateway.stream stand-in that records each NormalizedRequest (so a test can see WHICH prompt ran)."""
    seen: list = []

    async def _stream(req, *, api_key, profile=None, timeout=None, **_resilience):
        seen.append(req)
        for ev in (text_ev("ok"), usage_ev(), stop_ev()):
            yield ev
    return _stream, seen


async def _agent(c, ws, name="A") -> dict:
    r = await c.post("/api/v1/agents", json={"name": name, "provider": "openai", "model": "gpt-5-mini",
                                             "system_prompt": ACTIVE_PROMPT, "io_contract": IO},
                     headers=dev_headers(workspace=ws))
    assert r.status_code == 201, r.text
    return r.json()


async def _invoke(c, ws, aid, **body):
    return await c.post(f"/api/v1/agents/{aid}/invoke", headers=dev_headers(workspace=ws),
                        json={"inputs": INPUTS, **body})


async def _runs(ws, aid) -> list[Run]:
    async with tenant_session(ws) as s:
        return list((await s.execute(select(Run).where(Run.agent_id == uuid.UUID(aid)))).scalars())


async def _version_ids(ws, aid) -> set[uuid.UUID]:
    async with tenant_session(ws) as s:
        return set((await s.execute(select(AgentVersion.id).where(AgentVersion.agent_id == uuid.UUID(aid)))).scalars())


def _frame(r, name) -> dict:
    return next(d for n, d in parse_sse(r.text) if n == name)


# ---- draft target -------------------------------------------------------------------------------------------
async def test_draft_target_snapshot_carries_the_drafts_tool_skill_and_kb_bindings(monkeypatch):
    """Red-review H2 (the G3 class on the draft-RUN path): a draft's tool/skill/KB ids must reach the transient
    snapshot the runtime executes — not be silently dropped. Captures `_resolve_snapshot`'s output, then stops the
    request before any provider/retrieval work (resolution is what's under test)."""
    from api.errors import InputInvalid
    from api.routers import invoke as invoke_router
    from tests._api_helpers import seed_kb, seed_skill, seed_tool
    captured: list = []
    real = invoke_router._resolve_snapshot

    async def _capture(*a, **k):
        snap, is_draft = await real(*a, **k)
        captured.append((snap, is_draft))
        raise InputInvalid("stop after resolution", detail={"test": "captured"})
    monkeypatch.setattr(invoke_router, "_resolve_snapshot", _capture)
    ws = await seed_workspace()
    tool, skill, kb = await seed_tool(ws), await seed_skill(ws), await seed_kb(ws)
    async with client() as c:
        a = await _agent(c, ws)
        await set_draft(ws, a["id"], provider="openai", model="gpt-5-mini", model_params={},
                        system_prompt=DRAFT_PROMPT, io_contract=IO, kb_ids=[kb], tool_ids=[tool], skill_ids=[skill])
        r = await _invoke(c, ws, a["id"], target="draft")
    assert r.status_code == 400 and r.json()["error"]["detail"] == {"test": "captured"}, r.text
    ((snap, is_draft),) = captured
    assert is_draft and snap.label == "draft" and snap.system_prompt == DRAFT_PROMPT
    assert [t.schema_snapshot["name"] for t in snap.tools] == ["repo-tool"]
    assert [s.skill_id for s in snap.skills] == [skill]
    assert [ref.kb_id for ref in snap.retrieval] == [kb]


async def test_draft_target_tool_binding_reaches_the_provider_request(monkeypatch):
    """H2, end to end: the draft's bound tool is offered to the model on a real draft run."""
    from tests._api_helpers import seed_tool
    stream, seen = _recording_gateway()
    monkeypatch.setattr(gateway, "stream", stream)
    ws = await seed_workspace()
    tool = await seed_tool(ws, name="lookup-order")
    async with client() as c:
        a = await _agent(c, ws)
        await set_draft(ws, a["id"], provider="openai", model="gpt-5-mini", model_params={},
                        system_prompt=DRAFT_PROMPT, io_contract=IO, kb_ids=[], tool_ids=[tool], skill_ids=[])
        r = await _invoke(c, ws, a["id"], target="draft")
    assert r.status_code == 200, r.text
    assert seen and [t["name"] if isinstance(t, dict) else t.name for t in seen[-1].tools] == ["lookup-order"]



async def test_draft_target_runs_the_draft_prompt_and_records_a_null_version(monkeypatch):
    stream, seen = _recording_gateway()
    monkeypatch.setattr(gateway, "stream", stream)
    ws = await seed_workspace()
    async with client() as c:
        a = await _agent(c, ws)
        await set_draft(ws, a["id"], provider="openai", model="gpt-5-mini", model_params={},
                        system_prompt=DRAFT_PROMPT, io_contract=IO, kb_ids=[], tool_ids=[], skill_ids=[])
        before = await _version_ids(ws, a["id"])
        r = await _invoke(c, ws, a["id"], target="draft")
    assert r.status_code == 200, r.text
    assert seen and seen[-1].system == DRAFT_PROMPT                      # the draft's prompt, not the active one
    start = _frame(r, "run_start")
    assert start["version"] == "draft" and start.get("version_id") is None
    assert _frame(r, "done")["status"] == "success"
    (run,) = await _runs(ws, a["id"])
    assert run.version_id is None and run.source == "playground"         # a draft run names no stored version
    assert await _version_ids(ws, a["id"]) == before                     # transient: no version row was written


async def test_draft_target_with_null_draft_runs_the_active_config_as_draft(monkeypatch):
    """`agents.draft IS NULL` means "the active version's config" (D1) — so a draft run is the active content,
    still labelled and recorded as a draft."""
    stream, seen = _recording_gateway()
    monkeypatch.setattr(gateway, "stream", stream)
    ws = await seed_workspace()
    async with client() as c:
        a = await _agent(c, ws)
        r = await _invoke(c, ws, a["id"], target="draft")
    assert r.status_code == 200, r.text
    assert seen[-1].system == ACTIVE_PROMPT
    assert _frame(r, "run_start")["version"] == "draft"
    (run,) = await _runs(ws, a["id"])
    assert run.version_id is None


async def test_draft_with_a_missing_kb_binding_is_404_with_binding_detail(monkeypatch):
    stream, seen = _recording_gateway()
    monkeypatch.setattr(gateway, "stream", stream)
    ws = await seed_workspace()
    gone = str(uuid.uuid4())                                             # shape-valid id naming no KB (deleted)
    async with client() as c:
        a = await _agent(c, ws)
        await set_draft(ws, a["id"], provider="openai", model="gpt-5-mini", model_params={},
                        system_prompt=DRAFT_PROMPT, io_contract=IO, kb_ids=[gone], tool_ids=[], skill_ids=[])
        r = await _invoke(c, ws, a["id"], target="draft")
    assert r.status_code == 404, r.text
    err = r.json()["error"]
    assert err["code"] == "not_found" and err["detail"]["binding"] == "knowledge_base" and err["detail"]["id"] == gone
    assert seen == [] and await _runs(ws, a["id"]) == []                 # refused before any provider work


# ---- version target -----------------------------------------------------------------------------------------
async def test_version_target_without_version_id_is_400(monkeypatch):
    monkeypatch.setattr(gateway, "stream", _recording_gateway()[0])
    ws = await seed_workspace()
    async with client() as c:
        a = await _agent(c, ws)
        r = await _invoke(c, ws, a["id"], target="version")
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "input.invalid"


@pytest.mark.parametrize("target", ["active", "draft"])
async def test_version_id_without_version_target_is_400(monkeypatch, target):
    """`version_id` is required IFF target="version" (contracts §B) — an ambiguous body is refused, not guessed."""
    monkeypatch.setattr(gateway, "stream", _recording_gateway()[0])
    ws = await seed_workspace()
    async with client() as c:
        a = await _agent(c, ws)
        r = await _invoke(c, ws, a["id"], target=target, version_id=a["versions"][0]["id"])
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "input.invalid"


async def test_version_target_with_another_agents_version_is_404(monkeypatch):
    stream, seen = _recording_gateway()
    monkeypatch.setattr(gateway, "stream", stream)
    ws = await seed_workspace()
    async with client() as c:
        a = await _agent(c, ws, "A")
        b = await _agent(c, ws, "B")
        r = await _invoke(c, ws, a["id"], target="version", version_id=b["versions"][0]["id"])
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "not_found" and seen == []


async def test_version_target_runs_that_version_and_reports_its_label(monkeypatch):
    stream, seen = _recording_gateway()
    monkeypatch.setattr(gateway, "stream", stream)
    ws = await seed_workspace()
    async with client() as c:
        a = await _agent(c, ws)
        v1 = a["versions"][0]
        await set_draft(ws, a["id"], provider="openai", model="gpt-5-mini", model_params={},
                        system_prompt=DRAFT_PROMPT, io_contract=IO, kb_ids=[], tool_ids=[], skill_ids=[])
        v2 = (await c.post(f"/api/v1/agents/{a['id']}/versions", headers=dev_headers(workspace=ws))).json()
        r = await _invoke(c, ws, a["id"], target="version", version_id=v2["id"])     # v2 is NOT active
    assert r.status_code == 200, r.text
    assert seen[-1].system == DRAFT_PROMPT
    start = _frame(r, "run_start")
    assert start["version"] == v2["label"] == "v2" and start["version_id"] == v2["id"]
    (run,) = await _runs(ws, a["id"])
    assert str(run.version_id) == v2["id"] != v1["id"]


# ---- default + published plane ------------------------------------------------------------------------------
async def test_target_defaults_to_active(monkeypatch):
    stream, seen = _recording_gateway()
    monkeypatch.setattr(gateway, "stream", stream)
    ws = await seed_workspace()
    async with client() as c:
        a = await _agent(c, ws)
        await set_draft(ws, a["id"], provider="openai", model="gpt-5-mini", model_params={},
                        system_prompt=DRAFT_PROMPT, io_contract=IO, kb_ids=[], tool_ids=[], skill_ids=[])
        r = await _invoke(c, ws, a["id"])                                 # no target
    assert r.status_code == 200, r.text
    assert seen[-1].system == ACTIVE_PROMPT
    v1 = a["versions"][0]
    start = _frame(r, "run_start")
    assert start["version"] == v1["label"] and start["version_id"] == v1["id"]
    (run,) = await _runs(ws, a["id"])
    assert str(run.version_id) == v1["id"]


async def test_published_plane_never_runs_the_draft(monkeypatch):
    stream, seen = _recording_gateway()
    monkeypatch.setattr(gateway, "stream", stream)
    ws = await seed_workspace()
    secret = "ac_live_" + uuid.uuid4().hex
    async with client() as c:
        a = await _agent(c, ws)
        await set_draft(ws, a["id"], provider="openai", model="gpt-5-mini", model_params={},
                        system_prompt=DRAFT_PROMPT, io_contract=IO, kb_ids=[], tool_ids=[], skill_ids=[])
        await seed_api_token(ws, a["id"], secret)
        r = await c.post(f"/v1/agents/{a['id']}/invoke", headers={"Authorization": f"Bearer {secret}"},
                         json={"inputs": INPUTS, "target": "draft"})
    assert r.status_code == 200, r.text
    assert seen[-1].system == ACTIVE_PROMPT                              # `target` is not part of the published body
    assert _frame(r, "run_start")["version"] != "draft"
    (run,) = await _runs(ws, a["id"])
    assert run.source == "published" and str(run.version_id) == a["versions"][0]["id"]


async def test_done_frame_carries_the_persisted_runs_latency_and_cost(monkeypatch):
    """US2 L8: the playground turn shows cost/latency that EQUAL the Runs row — so `done` carries the run's own
    `latency_ms` + `cost_usd` (the usage frame has cost but no latency, and client wall-clock ≠ the server's)."""
    import asyncio

    async def _slow(req, *, api_key, profile=None, timeout=None, **_resilience):
        await asyncio.sleep(0.06)        # non-zero latency, so 0 == 0 can't pass (Red-review M)
        for ev in (text_ev("ok"), usage_ev(), stop_ev()):
            yield ev
    monkeypatch.setattr(gateway, "stream", _slow)
    ws = await seed_workspace()
    async with client() as c:
        a = await _agent(c, ws)
        r = await _invoke(c, ws, a["id"])
    assert r.status_code == 200, r.text
    done = _frame(r, "done")
    (run,) = await _runs(ws, a["id"])
    assert done["run_id"] == str(run.id).replace("-", "") or done["run_id"] == str(run.id)
    assert done["latency_ms"] >= 50 and done["latency_ms"] == run.latency_ms
    assert done["cost_usd"] == pytest.approx(float(run.cost_usd))
