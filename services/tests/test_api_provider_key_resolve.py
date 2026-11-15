"""Feature 014 US2 — resolve-at-invoke / eval draws the provider credential from custody, fail-closed.

Two lanes (per the feature-012 dev-auth constraint):
- HTTP integration (AC_ENV=dev, the conftest default): proves invoke AND eval-run resolve/use the STORED key
  (captured via a mocked gateway), and that each workspace resolves its own key (RLS, cross-tenant).
- UNIT on `resolve_provider_key` (AC_ENV monkeypatched): proves prod fails closed on a missing key while dev
  falls back to PROVIDER_API_KEY, and that a configured-yet-unresolvable key fails closed in ANY env.
No live/paid provider calls — the gateway is always mocked and the secrets backend is the in-process `local`.
"""
from __future__ import annotations

import json

import pytest

from api import deps
from api.db import tenant_session
from api.errors import ConfigError
from api.provider_keys import resolve_provider_key
from api.secrets import build_secrets_provider
from api.settings import get_settings
from runtime.providers import gateway
from tests._api_helpers import (
    client, dev_headers, seed_agent, seed_provider_key, seed_version, seed_workspace,
)
from tests._evals_helpers import VALID_OUTPUT, make_routed_gateway
from tests._invoke_helpers import stop_ev, text_ev, usage_ev

pytestmark = pytest.mark.integration


def _capturing_gateway(captured: list):
    async def _stream(req, *, api_key, profile=None, timeout=None):
        captured.append(api_key)
        yield text_ev("ok")
        yield usage_ev()
        yield stop_ev()
    return _stream


async def _capture_key(c, ws: str, provider: str, label: str, value: str):
    adm = dev_headers(workspace=ws, subject="adm", claims={ws: "admin"})
    r = await c.post("/api/v1/provider-keys", headers=adm,
                     json={"provider": provider, "label": label, "value": value})
    assert r.status_code == 201, r.text


# ---- invoke uses the STORED key (dev HTTP) -------------------------------------------------------
async def test_invoke_uses_stored_provider_key(monkeypatch):
    captured: list = []
    monkeypatch.setattr(gateway, "stream", _capturing_gateway(captured))
    ws = await seed_workspace()
    aid = await seed_agent(ws)
    await seed_version(ws, aid)                      # provider = openai
    async with client() as c:
        await _capture_key(c, ws, "openai", "k", "sk-STORED-abcdef1234")
        r = await c.post(f"/api/v1/agents/{aid}/invoke",
                         headers=dev_headers(workspace=ws), json={"inputs": {"message": "hi"}})
        assert r.status_code == 200
    assert captured == ["sk-STORED-abcdef1234"]      # the stored secret flowed through, not the env default


# ---- eval-run uses the STORED key (dev HTTP) -----------------------------------------------------
async def test_eval_run_uses_stored_provider_key(monkeypatch):
    routed = make_routed_gateway(candidate_cost=0.001,
                                 judge_body={"rubric": {"accuracy": 5.0}, "pairwise": "candidate",
                                             "rationale": "great"})
    captured: list = []

    async def _cap(req, *, api_key, profile=None, timeout=None):
        captured.append(api_key)
        async for ev in routed(req, api_key=api_key, profile=profile, timeout=timeout):
            yield ev

    monkeypatch.setattr(gateway, "stream", _cap)
    ws = await seed_workspace()
    aid = await seed_agent(ws)
    vid = await seed_version(ws, aid)
    cases = [{"id": f"c{i}", "input": "an XDR alert fired",
              "rubric": [{"name": "accuracy", "weight": 1.0}]} for i in range(3)]
    baseline = {"quality": 90.0, "cost_usd": 0.003, "latency_ms": 100,
                "per_case": {f"c{i}": json.dumps(VALID_OUTPUT) for i in range(3)}}
    body = {"version_id": vid, "cases": cases, "baseline": baseline,
            "policy": {"min_cases": 3, "quality_threshold": 10.0, "efficiency_budget_pct": 10.0}}
    async with client() as c:
        await _capture_key(c, ws, "openai", "k", "sk-EVAL-STORED-5678")
        r = await c.post(f"/api/v1/agents/{aid}/eval-runs",
                         headers=dev_headers(workspace=ws, claims={ws: "publisher"}), json=body)
        assert r.status_code == 200, r.text
    assert captured and all(k == "sk-EVAL-STORED-5678" for k in captured)


# ---- cross-tenant: each workspace resolves ITS OWN key (RLS) --------------------------------------
async def test_invoke_resolves_own_workspace_key_only(monkeypatch):
    captured: list = []
    monkeypatch.setattr(gateway, "stream", _capturing_gateway(captured))
    ws_a = await seed_workspace()
    ws_b = await seed_workspace()
    aid_a = await seed_agent(ws_a); await seed_version(ws_a, aid_a)
    aid_b = await seed_agent(ws_b); await seed_version(ws_b, aid_b)
    async with client() as c:
        await _capture_key(c, ws_a, "openai", "k", "sk-A-SECRET-aaaa")
        await _capture_key(c, ws_b, "openai", "k", "sk-B-SECRET-bbbb")
        await c.post(f"/api/v1/agents/{aid_a}/invoke",
                     headers=dev_headers(workspace=ws_a), json={"inputs": {"message": "hi"}})
        await c.post(f"/api/v1/agents/{aid_b}/invoke",
                     headers=dev_headers(workspace=ws_b), json={"inputs": {"message": "hi"}})
    assert captured == ["sk-A-SECRET-aaaa", "sk-B-SECRET-bbbb"]


# ---- invoke fails closed (no stream/provider call) when a configured key can't resolve -----------
async def test_invoke_fails_closed_config_error_when_key_unresolvable(monkeypatch):
    async def _boom(req, *, api_key, profile=None, timeout=None):
        raise AssertionError("gateway must not be called when key resolution fails")
        yield  # pragma: no cover
    monkeypatch.setattr(gateway, "stream", _boom)
    ws = await seed_workspace()
    aid = await seed_agent(ws)
    await seed_version(ws, aid)
    # active key whose opaque ref the backend doesn't hold → resolve fails closed (even in dev)
    await seed_provider_key(ws, provider="openai", label="broken",
                            vault_path="local://x/y/deadbeef", status="active")
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/invoke",
                         headers=dev_headers(workspace=ws), json={"inputs": {"message": "hi"}})
    assert r.status_code == 503 and r.json()["error"]["code"] == "config_error"


# ---- UNIT: resolve_provider_key env semantics ----------------------------------------------------
async def test_resolve_active_key_returns_stored_value():
    ws = await seed_workspace()
    vp = await build_secrets_provider().put(ws, "k", "sk-UNIT-stored-999")
    await seed_provider_key(ws, provider="openai", label="k", vault_path=vp, status="active")
    async with tenant_session(ws) as s:
        assert await resolve_provider_key(s, ws, "openai") == "sk-UNIT-stored-999"


async def test_resolve_no_key_dev_falls_back_to_env():
    get_settings.cache_clear()           # conftest default AC_ENV=dev
    ws = await seed_workspace()
    async with tenant_session(ws) as s:
        assert await resolve_provider_key(s, ws, "openai") == deps.PROVIDER_API_KEY
    get_settings.cache_clear()


async def test_resolve_no_key_prod_fails_closed(monkeypatch):
    monkeypatch.setenv("AC_ENV", "prod")
    monkeypatch.setenv("AC_STORAGE_PROVIDER", "s3")   # prod requires durable uploads
    monkeypatch.setenv("AC_TELEMETRY_FINGERPRINT_KEY", "test-prod-fingerprint-key")  # 023: prod requires it
    get_settings.cache_clear()
    try:
        ws = await seed_workspace()
        async with tenant_session(ws) as s:
            with pytest.raises(ConfigError):
                await resolve_provider_key(s, ws, "openai")
    finally:
        get_settings.cache_clear()


async def test_resolve_configured_but_unresolvable_fails_closed_even_in_dev():
    get_settings.cache_clear()           # dev
    ws = await seed_workspace()
    await seed_provider_key(ws, provider="openai", label="broken",
                            vault_path="local://x/y/deadbeef", status="active")
    async with tenant_session(ws) as s:
        with pytest.raises(ConfigError):
            await resolve_provider_key(s, ws, "openai")
    get_settings.cache_clear()
