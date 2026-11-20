"""Deployment infra slice — liveness/readiness probes (/healthz, /readyz)."""
from __future__ import annotations

import pytest

from tests._api_helpers import client

pytestmark = pytest.mark.integration


async def test_healthz_ok():
    async with client() as c:
        r = await c.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok"


async def test_readyz_ready_when_db_up():
    async with client() as c:
        r = await c.get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready" and body["checks"]["database"] == "ok"


async def test_readyz_not_ready_when_db_down(monkeypatch):
    # Force the readiness DB check to fail → the probe must report 503 not-ready (fail-closed),
    # so orchestration holds traffic. (main.py calls db.app_engine() at request time, so this patch takes.)
    from api import db

    def _boom():
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(db, "app_engine", _boom)
    async with client() as c:
        r = await c.get("/readyz")
    assert r.status_code == 503
    assert r.json()["status"] == "not-ready" and r.json()["checks"]["database"] == "down"
