"""US4 — workspace settings/limits/eval-policy + rename (feature 006)."""
from __future__ import annotations

import uuid as _uuid

import pytest
from sqlalchemy import select

from api.db import tenant_session
from api.models import WorkspaceSettings
from tests._api_helpers import client, dev_headers, seed_workspace

pytestmark = pytest.mark.integration


async def test_get_settings_does_not_create_a_row():
    """F4 — a GET is read-only: it returns in-memory defaults without persisting a settings row."""
    ws = await seed_workspace()
    async with client() as c:
        r = await c.get("/api/v1/workspace/settings", headers=dev_headers(workspace=ws, claims={ws: "admin"}))
    assert r.status_code == 200 and r.headers.get("ETag") == "0"
    async with tenant_session(ws) as s:
        row = (await s.execute(
            select(WorkspaceSettings).where(WorkspaceSettings.workspace_id == _uuid.UUID(ws)))).scalar_one_or_none()
    assert row is None                                       # GET wrote nothing


async def test_settings_get_defaults_then_patch_with_if_match():
    ws = await seed_workspace()
    async with client() as c:
        got = await c.get("/api/v1/workspace/settings", headers=dev_headers(workspace=ws, claims={ws: "admin"}))
        assert got.status_code == 200 and got.headers.get("ETag")
        etag = got.headers["ETag"]
        patched = await c.patch("/api/v1/workspace/settings", headers=dev_headers(workspace=ws, claims={ws: "admin"}, if_match=etag),
                                json={"default_provider": "openai", "default_model": "gpt-5-mini",
                                      "limits": {"monthlySpendUsd": 500, "maxConcurrent": 4, "requestTimeoutS": 30},
                                      "eval_policy": {"minCases": 5, "qualityThreshold": 70.0,
                                                      "efficiencyBudgetPct": 10.0, "allowLowConfidenceOverride": False}})
        assert patched.status_code == 200 and patched.json()["default_model"] == "gpt-5-mini"
        assert patched.json()["limits"]["monthlySpendUsd"] == 500
        # reuse of the stale etag → 409
        stale = await c.patch("/api/v1/workspace/settings", headers=dev_headers(workspace=ws, claims={ws: "admin"}, if_match=etag),
                              json={"default_model": "x"})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "conflict.stale"


async def test_settings_patch_requires_if_match():
    ws = await seed_workspace()
    async with client() as c:
        await c.get("/api/v1/workspace/settings", headers=dev_headers(workspace=ws, claims={ws: "admin"}))
        r = await c.patch("/api/v1/workspace/settings", headers=dev_headers(workspace=ws, claims={ws: "admin"}),
                          json={"default_model": "x"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "input.invalid"


async def test_rename_workspace():
    ws = await seed_workspace(name="Original")
    async with client() as c:
        r = await c.patch("/api/v1/workspace", headers=dev_headers(workspace=ws, claims={ws: "admin"}), json={"name": "Renamed WS"})
    assert r.status_code == 200 and r.json()["name"] == "Renamed WS" and r.json()["id"] == ws
