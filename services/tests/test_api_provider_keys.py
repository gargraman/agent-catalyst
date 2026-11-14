"""Feature 014 — provider-key custody at the API (capture, list, rotate, revoke) + storage tenancy.

Uses the RLS+ASGI integration harness. The secrets backend is the in-process `local` provider (conftest
default config), so no boto3/LocalStack and no network. No raw secret or vault_path ever appears in a response.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from api.db import tenant_session
from api.errors import ConfigError
from api.models import ProviderKey
from api.provider_keys import mask_secret, resolve_provider_key
from api.secrets import build_secrets_provider
from api.settings import get_settings
from tests._api_helpers import client, dev_headers, seed_provider_key

pytestmark = pytest.mark.integration


async def _seed_ws() -> str:
    from tests._api_helpers import seed_workspace
    return await seed_workspace()


# ---- T009: table + RLS tenancy (storage layer) ---------------------------------------------------
async def test_provider_keys_table_roundtrip_and_rls_tenancy():
    ws_a = await _seed_ws()
    ws_b = await _seed_ws()
    kid = await seed_provider_key(ws_a, provider="openai", label="a-key")

    # readable back within its own workspace
    async with tenant_session(ws_a) as s:
        rows = (await s.execute(select(ProviderKey))).scalars().all()
        assert [str(r.id) for r in rows] == [kid]
        assert rows[0].provider == "openai" and rows[0].label == "a-key"

    # invisible to workspace B under RLS
    async with tenant_session(ws_b) as s:
        rows_b = (await s.execute(select(ProviderKey))).scalars().all()
        assert rows_b == []


# ---- T014 / US1: capture + list (masked-only, RBAC, duplicate, cross-tenant) ----------------------
RAW_VALUE = "sk-proj-SECRETvalue-1234567890"


async def test_capture_returns_masked_never_raw_and_stores_no_plaintext():
    ws = await _seed_ws()
    async with client() as c:
        adm = dev_headers(workspace=ws, subject="adm", claims={ws: "admin"})
        r = await c.post("/api/v1/provider-keys", headers=adm,
                         json={"provider": "openai", "label": "prod", "value": RAW_VALUE})
        assert r.status_code == 201, r.text
        out = r.json()
        # masked + status present; raw value + vault_path NEVER in the response
        assert out["masked"] == "sk-pro••••••••7890" and out["status"] == "active"
        assert out["provider"] == "openai" and out["label"] == "prod"
        body_text = r.text
        assert RAW_VALUE not in body_text and "vault_path" not in out and "value" not in out

    # the stored row holds no plaintext: masked only + an opaque local:// ref
    async with tenant_session(ws) as s:
        row = (await s.execute(select(ProviderKey))).scalar_one()
        assert row.masked == "sk-pro••••••••7890"
        assert RAW_VALUE not in row.masked
        assert row.vault_path.startswith("local://")


async def test_list_returns_masked_only():
    ws = await _seed_ws()
    async with client() as c:
        adm = dev_headers(workspace=ws, subject="adm", claims={ws: "admin"})
        await c.post("/api/v1/provider-keys", headers=adm,
                     json={"provider": "anthropic", "label": "k1", "value": RAW_VALUE})
        lst = await c.get("/api/v1/provider-keys", headers=adm)
        assert lst.status_code == 200
        items = lst.json()
        assert len(items) == 1
        assert items[0]["masked"] == "sk-pro••••••••7890"
        assert RAW_VALUE not in lst.text and "vault_path" not in items[0]


async def test_non_admin_capture_and_list_denied():
    ws = await _seed_ws()
    async with client() as c:
        builder = dev_headers(workspace=ws, subject="b", claims={ws: "builder"})
        r = await c.post("/api/v1/provider-keys", headers=builder,
                         json={"provider": "openai", "label": "x", "value": RAW_VALUE})
        assert r.status_code == 403 and r.json()["error"]["code"] == "access.denied"
        g = await c.get("/api/v1/provider-keys", headers=builder)
        assert g.status_code == 403


async def test_duplicate_label_conflicts():
    ws = await _seed_ws()
    async with client() as c:
        adm = dev_headers(workspace=ws, subject="adm", claims={ws: "admin"})
        body = {"provider": "openai", "label": "dup", "value": RAW_VALUE}
        assert (await c.post("/api/v1/provider-keys", headers=adm, json=body)).status_code == 201
        r2 = await c.post("/api/v1/provider-keys", headers=adm, json=body)
        assert r2.status_code == 409 and r2.json()["error"]["code"].startswith("conflict")


async def test_list_cross_tenant_omits_other_workspace_key():
    ws_a = await _seed_ws()
    ws_b = await _seed_ws()
    await seed_provider_key(ws_a, provider="openai", label="a-only")
    async with client() as c:
        adm_b = dev_headers(workspace=ws_b, subject="adm", claims={ws_b: "admin"})
        items = (await c.get("/api/v1/provider-keys", headers=adm_b)).json()
        assert items == []


# ---- T022 / US3: rotate --------------------------------------------------------------------------
NEW_VALUE = "sk-proj-NEWvalue-ABCDEFGH9999"


async def test_rotate_replaces_secret_updates_masked_and_rotatedat():
    ws = await _seed_ws()
    async with client() as c:
        adm = dev_headers(workspace=ws, subject="adm", claims={ws: "admin"})
        cap = (await c.post("/api/v1/provider-keys", headers=adm,
                            json={"provider": "openai", "label": "r", "value": RAW_VALUE})).json()
        kid = cap["id"]
        assert cap["rotatedAt"] is None
        r = await c.post(f"/api/v1/provider-keys/{kid}/rotate", headers=adm, json={"value": NEW_VALUE})
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["id"] == kid and out["label"] == "r" and out["status"] == "active"
        assert out["masked"] == mask_secret(NEW_VALUE) and out["rotatedAt"] is not None
        assert NEW_VALUE not in r.text
    # the new value now resolves; the old one is gone
    async with tenant_session(ws) as s:
        assert await resolve_provider_key(s, ws, "openai") == NEW_VALUE


async def test_rotate_rbac_and_cross_tenant():
    ws_a = await _seed_ws()
    ws_b = await _seed_ws()
    kid = await seed_provider_key(ws_a, provider="openai", label="r")
    async with client() as c:
        builder = dev_headers(workspace=ws_a, subject="b", claims={ws_a: "builder"})
        assert (await c.post(f"/api/v1/provider-keys/{kid}/rotate", headers=builder,
                             json={"value": "x-value-123456"})).status_code == 403
        adm_b = dev_headers(workspace=ws_b, subject="adm", claims={ws_b: "admin"})
        assert (await c.post(f"/api/v1/provider-keys/{kid}/rotate", headers=adm_b,
                             json={"value": "x-value-123456"})).status_code == 404


# ---- T024 / US4: revoke --------------------------------------------------------------------------
async def test_revoke_tombstones_row_and_is_idempotent():
    ws = await _seed_ws()
    async with client() as c:
        adm = dev_headers(workspace=ws, subject="adm", claims={ws: "admin"})
        kid = (await c.post("/api/v1/provider-keys", headers=adm,
                            json={"provider": "openai", "label": "rv", "value": RAW_VALUE})).json()["id"]
        r = await c.post(f"/api/v1/provider-keys/{kid}/revoke", headers=adm)
        assert r.status_code == 200 and r.json()["status"] == "revoked"
        r2 = await c.post(f"/api/v1/provider-keys/{kid}/revoke", headers=adm)   # idempotent
        assert r2.status_code == 200 and r2.json()["status"] == "revoked"
    # row RETAINED (tombstone), not deleted
    async with tenant_session(ws) as s:
        row = (await s.execute(select(ProviderKey).where(ProviderKey.id == uuid.UUID(kid)))).scalar_one()
        assert row.status == "revoked"


async def test_revoke_blocks_resolution_fail_closed_in_prod(monkeypatch):
    ws = await _seed_ws()
    vp = await build_secrets_provider().put(ws, "rv", "sk-REVOKEME-123456")
    kid = await seed_provider_key(ws, provider="openai", label="rv", vault_path=vp, status="active")
    async with client() as c:
        adm = dev_headers(workspace=ws, subject="adm", claims={ws: "admin"})
        assert (await c.post(f"/api/v1/provider-keys/{kid}/revoke", headers=adm)).status_code == 200
    monkeypatch.setenv("AC_ENV", "prod")
    monkeypatch.setenv("AC_TELEMETRY_FINGERPRINT_KEY", "test-prod-fingerprint-key")  # 023: prod requires it
    get_settings.cache_clear()
    try:
        async with tenant_session(ws) as s:
            with pytest.raises(ConfigError):
                await resolve_provider_key(s, ws, "openai")
    finally:
        get_settings.cache_clear()


async def test_revoke_rbac_and_cross_tenant():
    ws_a = await _seed_ws()
    ws_b = await _seed_ws()
    kid = await seed_provider_key(ws_a, provider="openai", label="rv")
    async with client() as c:
        builder = dev_headers(workspace=ws_a, subject="b", claims={ws_a: "builder"})
        assert (await c.post(f"/api/v1/provider-keys/{kid}/revoke", headers=builder)).status_code == 403
        adm_b = dev_headers(workspace=ws_b, subject="adm", claims={ws_b: "admin"})
        assert (await c.post(f"/api/v1/provider-keys/{kid}/revoke", headers=adm_b)).status_code == 404
