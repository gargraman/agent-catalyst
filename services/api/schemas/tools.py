"""Pydantic contracts for the tool & MCP catalog (feature 006, US1) — mirror data.ts RepoTool/McpServer/McpTool.

http_config is NON-SECRET only: a validator does a **best-effort** rejection of secret-looking keys and values
(recursively) so a raw credential is not persisted here (FR-021). It is a guardrail, not a proof — real
credential custody is deferred to M5/Vault (write-once {masked, vaultPath}); callers must send secrets there,
never in http_config.
"""
from __future__ import annotations

import datetime
import re

from pydantic import BaseModel, field_validator

from api.errors import InputInvalid
from api.schemas.access_policy import AccessPolicy

# Secret-looking KEY names (any nesting level).
_SECRET_KEY_RE = re.compile(
    r"(?i)(secret|token|password|passwd|api[_-]?key|apikey|credential|authorization|"
    r"auth[_-]?token|bearer|private[_-]?key|client[_-]?secret|access[_-]?key)"
)
# High-signal credential patterns inside string VALUES (e.g. an inlined auth header).
_SECRET_VALUE_RE = re.compile(
    r"(?i)(authorization\s*:|bearer\s+\S|(api[_-]?key|token|secret|password)\s*[:=]\s*\S|\bsk-[A-Za-z0-9]{8,})"
)


def _reject_secrets(cfg: dict | None) -> dict:
    """Walk http_config (dicts + lists) rejecting secret-looking keys at any depth and obvious credential
    patterns in string values. Best-effort (FR-021) — cannot catch every secret in free text."""
    def walk(obj, path: str) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if _SECRET_KEY_RE.search(str(k)):
                    raise InputInvalid("http_config must not carry a secret (credential custody is deferred)",
                                       detail={"field": path and f"{path}.{k}" or k})
                walk(v, f"{path}.{k}" if path else str(k))
        elif isinstance(obj, (list, tuple)):
            for i, v in enumerate(obj):
                walk(v, f"{path}[{i}]")
        elif isinstance(obj, str):
            if _SECRET_VALUE_RE.search(obj):
                raise InputInvalid("http_config value looks like a credential (custody is deferred)",
                                   detail={"field": path})
    walk(cfg or {}, "")
    return cfg or {}


class ToolCreate(BaseModel):
    name: str
    description: str = ""
    kind: str = "http"
    http_config: dict = {}
    risk_tier: str = "read"
    access_policy: dict | None = None       # feature 013

    @field_validator("http_config")
    @classmethod
    def _no_secrets(cls, v: dict) -> dict:
        return _reject_secrets(v)


class ToolPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    http_config: dict | None = None
    risk_tier: str | None = None
    access_policy: dict | None = None       # feature 013

    @field_validator("http_config")
    @classmethod
    def _no_secrets(cls, v: dict | None) -> dict | None:
        return None if v is None else _reject_secrets(v)


class ToolOut(BaseModel):
    id: str
    name: str
    description: str
    kind: str
    http_config: dict
    risk_tier: str
    archived: bool
    created_at: datetime.datetime
    access_policy: AccessPolicy


class McpToolCreate(BaseModel):
    name: str
    description: str = ""
    enabled: bool = True
    risk_tier: str = "read"
    access_policy: dict | None = None       # feature 013


class McpToolPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    risk_tier: str | None = None
    access_policy: dict | None = None       # feature 013


class McpToolOut(BaseModel):
    id: str
    server_id: str
    name: str
    description: str
    enabled: bool
    risk_tier: str
    access_policy: AccessPolicy


class McpServerCreate(BaseModel):
    name: str
    url: str = ""
    transport: str = "http"
    access_policy: dict | None = None       # feature 013


class McpServerPatch(BaseModel):
    name: str | None = None
    url: str | None = None
    transport: str | None = None
    status: str | None = None
    access_policy: dict | None = None       # feature 013


class McpServerOut(BaseModel):
    id: str
    name: str
    url: str
    transport: str
    status: str
    archived: bool
    created_at: datetime.datetime
    access_policy: AccessPolicy
    tools: list[McpToolOut] = []
