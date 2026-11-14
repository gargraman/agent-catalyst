"""Feature 023 US1 (T010): GET/PUT /api/v1/workspace/telemetry — admin-only, audited content-logging opt-in."""
from __future__ import annotations

import json
import logging
import uuid as _uuid

import pytest
from sqlalchemy import select

from api.db import tenant_session
from api.models import WorkspaceSettings
from api.settings import get_settings
from tests._api_helpers import client, dev_headers, seed_workspace

pytestmark = pytest.mark.integration

URL = "/api/v1/workspace/telemetry"


def _adm(ws, subject="adm"):
    return dev_headers(workspace=ws, subject=subject, claims={ws: "admin"})


def _audit_lines(caplog):
    out = []
    for r in caplog.records:
        if r.name == "api.security" and "telemetry.content_logging.changed" in r.getMessage():
            out.append(json.loads(r.getMessage()))
    return out


async def _stored(ws):
    async with tenant_session(ws) as s:
        return (await s.execute(select(WorkspaceSettings.content_logging)
                                .where(WorkspaceSettings.workspace_id == _uuid.UUID(ws)))).scalar_one_or_none()


async def test_get_defaults_without_row():
    ws = await seed_workspace()
    async with client() as c:
        r = await c.get(URL, headers=_adm(ws))
    assert r.status_code == 200
    assert r.json() == {"content_logging": False, "effective": get_settings().telemetry_content_override}
    assert await _stored(ws) is None                     # GET never creates the row


async def test_admin_put_persists_and_audits_once(caplog):
    caplog.set_level(logging.INFO, logger="api.security")
    ws = await seed_workspace()
    async with client() as c:
        r = await c.put(URL, headers={**_adm(ws), "X-Correlation-Id": "audit-1"}, json={"content_logging": True})
        assert r.status_code == 200 and r.json() == {"content_logging": True, "effective": True}
        assert await _stored(ws) is True
        lines = _audit_lines(caplog)
        assert len(lines) == 1
        a = lines[0]
        assert (a["subject"], a["workspace_id"], a["old"], a["new"], a["correlation_id"]) == \
            ("adm", ws, False, True, "audit-1")
        assert a["ts"]
        # Same-value PUT → 200, no new audit line.
        r2 = await c.put(URL, headers=_adm(ws), json={"content_logging": True})
        assert r2.status_code == 200 and len(_audit_lines(caplog)) == 1
        # Change back → second audit line.
        r3 = await c.put(URL, headers=_adm(ws), json={"content_logging": False})
        assert r3.status_code == 200 and r3.json()["content_logging"] is False
        assert [x["new"] for x in _audit_lines(caplog)] == [True, False]


@pytest.mark.parametrize("role", ["viewer", "builder", "publisher"])
async def test_non_admin_put_denied_without_write(caplog, role):
    caplog.set_level(logging.INFO, logger="api.security")
    ws = await seed_workspace()
    async with client() as c:
        r = await c.put(URL, headers=dev_headers(workspace=ws, subject="u", claims={ws: role}),
                        json={"content_logging": True})
        assert r.status_code == 403 and r.json()["error"]["code"] == "access.denied"
        # Non-admins may still read it (`view`).
        assert (await c.get(URL, headers=dev_headers(workspace=ws, subject="u", claims={ws: role}))).status_code == 200
    assert await _stored(ws) is None
    assert _audit_lines(caplog) == []


async def test_unknown_body_key_rejected():
    ws = await seed_workspace()
    async with client() as c:
        r = await c.put(URL, headers=_adm(ws), json={"content_logging": True, "log_everything": True})
    assert r.status_code == 422                     # repo convention: FastAPI body-schema validation


async def test_cross_workspace_isolation():
    a, b = await seed_workspace(), await seed_workspace()
    async with client() as c:
        assert (await c.put(URL, headers=_adm(a), json={"content_logging": True})).status_code == 200
        rb = await c.get(URL, headers=_adm(b))
    assert rb.json()["content_logging"] is False
    assert await _stored(b) is None
