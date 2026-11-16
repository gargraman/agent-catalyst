"""Editable agent draft (feature 026, research D1/D2/D3).

The draft is the agent's "saved but not yet a version" config, stored normalized in `agents.draft` (jsonb). `NULL`
means "the active version's config" — so existing agents need no backfill, and `draft_dirty` is false until edited.

    Draft {provider, model, model_params, system_prompt, io_contract, kb_ids[], tool_ids[], skill_ids[]}

- Validation at SAVE (pydantic → 422): known chat profile for provider/model, `io_contract.input_schema`, allow-listed
  `model_params`. Binding ids are shape-checked only — existence is checked when a version is built (FR-005) or a
  draft is run, because a KB may be deleted between save and use anyway.
- `normalize` is the single canonical form: sorted unique id lists, unset (None) params dropped, plain JSON. Storage
  AND dirty-comparison both go through it, so "reorder ids" / "add an unset param" can never read as a change.

Pure (no I/O): the router does the DB work and hands loaded rows in.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from api.models import AgentVersion
from runtime.hashing import compute_snapshot_hash
from runtime.providers.profiles import _PROFILE_DIR

# Params flow straight into the provider transport's kwargs (runtime/providers/_common.py), so the allow-list is a
# security boundary too: an unknown key (api_key, base_url, …) is refused, never forwarded.
_PARAM_RANGES: dict[str, tuple[float, float, bool]] = {      # name → (lo, hi, integer)
    "temperature": (0.0, 2.0, False),
    "top_p": (0.0, 1.0, False),
    "max_tokens": (1, 10**9, True),                          # upper bound tightened to the profile's max_output_tokens
}


@lru_cache(maxsize=1)
def _chat_profiles() -> dict[tuple[str, str], dict]:
    out = {}
    for path in sorted(Path(_PROFILE_DIR).glob("*.json")):
        doc = json.loads(path.read_text())
        if doc.get("kind", "chat") == "chat":
            out[(doc["provider"], doc["model"])] = doc
    return out


def profile_doc(provider: str, model: str) -> dict:
    """The versioned chat profile document (frozen into `profile_snapshot`). KeyError when unknown."""
    return _chat_profiles()[(provider, model)]


class Draft(BaseModel):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    model_params: dict[str, Any] = Field(default_factory=dict)
    system_prompt: str = ""
    io_contract: dict[str, Any]
    kb_ids: list[uuid.UUID] = Field(default_factory=list)
    tool_ids: list[uuid.UUID] = Field(default_factory=list)
    skill_ids: list[uuid.UUID] = Field(default_factory=list)

    @field_validator("io_contract")
    @classmethod
    def _input_schema_required(cls, v: dict) -> dict:
        if not isinstance(v.get("input_schema"), dict):
            raise ValueError("io_contract must contain an input_schema object")
        return v

    @model_validator(mode="after")
    def _profile_and_params(self) -> "Draft":
        providers = {p for p, _ in _chat_profiles()}
        if self.provider not in providers:
            raise ValueError(f"unknown provider {self.provider!r}")
        try:
            doc = profile_doc(self.provider, self.model)
        except KeyError:
            raise ValueError(f"no capability profile for {self.provider}/{self.model}") from None
        max_out = (doc.get("limits") or {}).get("max_output_tokens")
        for k, v in self.model_params.items():
            if k not in _PARAM_RANGES:
                raise ValueError(f"model_params.{k} is not a supported parameter ({sorted(_PARAM_RANGES)})")
            if v is None:
                continue
            lo, hi, integer = _PARAM_RANGES[k]
            if k == "max_tokens" and max_out:
                hi = max_out
            if isinstance(v, bool) or not isinstance(v, int if integer else (int, float)):
                raise ValueError(f"model_params.{k} must be {'an integer' if integer else 'a number'}")
            if not lo <= v <= hi:
                raise ValueError(f"model_params.{k} must be within {lo}..{hi}")
        return self


def _ids(xs) -> list[str]:
    return sorted({str(x) for x in (xs or ())})


def normalize(draft: Draft | dict) -> dict:
    """Canonical, JSON-plain draft. Accepts a validated `Draft` or a stored/plain dict (not re-validated)."""
    d = draft.model_dump(mode="json") if isinstance(draft, Draft) else dict(draft)
    return {
        "provider": d.get("provider"),
        "model": d.get("model"),
        "model_params": {k: v for k, v in (d.get("model_params") or {}).items() if v is not None},
        "system_prompt": d.get("system_prompt") or "",
        "io_contract": d.get("io_contract") or {},
        "kb_ids": _ids(d.get("kb_ids")),
        "tool_ids": _ids(d.get("tool_ids")),
        "skill_ids": _ids(d.get("skill_ids")),
    }


def project(version: AgentVersion) -> dict:
    """An existing version projected onto the Draft shape (registry tools only — MCP/subagent are out of v1 draft edit)."""
    mc = version.model_config_ or {}
    return normalize({
        "provider": mc.get("provider"), "model": mc.get("model"), "model_params": mc.get("params") or {},
        "system_prompt": version.system_prompt, "io_contract": version.io_contract,
        "kb_ids": [b.kb_id for b in version.retrieval_bindings],
        "tool_ids": [b.tool_id for b in version.tool_bindings if b.kind in ("http", "builtin") and b.tool_id],
        "skill_ids": [b.skill_id for b in version.skill_bindings],
    })


def resolve(agent, active_version: AgentVersion | None) -> dict | None:
    """The effective draft: the stored one, else the active version's projection (None if neither exists)."""
    if agent.draft is not None:
        return normalize(agent.draft)
    return project(active_version) if active_version is not None else None


def dirty(agent, active_version: AgentVersion | None) -> bool:
    if agent.draft is None:
        return False
    if active_version is None:
        return True
    return normalize(agent.draft) != project(active_version)


def prompt_hash(system_prompt: str) -> str:
    return hashlib.sha256(system_prompt.encode()).hexdigest()[:16]


def version_from_draft(agent, draft: Draft | dict, *, label: str, base: AgentVersion | None = None,
                       created_by: str | None = None, retrieval=(), tools=(), skills=()) -> AgentVersion:
    """Build an UNSAVED AgentVersion from a draft. Non-draft config (guardrail/memory/context) is inherited from
    `base` (the active version) so a draft edit never silently resets it. Bindings are passed in pre-built (the
    router loads + existence-checks the referenced rows); the snapshot hash is computed over them."""
    n = normalize(draft)
    v = AgentVersion(
        workspace_id=agent.workspace_id, agent_id=agent.id, label=label,
        system_prompt=n["system_prompt"], prompt_hash=prompt_hash(n["system_prompt"]), snapshot_hash="",
        model_config_={"provider": n["provider"], "model": n["model"], "params": n["model_params"]},
        profile_snapshot=profile_doc(n["provider"], n["model"]), io_contract=n["io_contract"],
        guardrail_config=dict(base.guardrail_config or {}) if base is not None else {},
        memory_config=dict(base.memory_config or {}) if base is not None else {},
        context_config=dict(base.context_config or {}) if base is not None else {},
        created_by=created_by,
    )
    v.retrieval_bindings, v.tool_bindings, v.skill_bindings = list(retrieval), list(tools), list(skills)
    v.snapshot_hash = compute_snapshot_hash(v)
    return v


def transient_version(agent, draft: Draft | dict, *, base: AgentVersion | None = None,
                      retrieval=(), tools=(), skills=()) -> AgentVersion:
    """An unsaved version for running the DRAFT through the same runtime path (D3). Never added to a session.
    Its id is a fresh uuid that names no stored row — the run record's snapshot_hash, not the id, is its identity."""
    v = version_from_draft(agent, draft, label="draft", base=base, retrieval=retrieval, tools=tools, skills=skills)
    v.id = uuid.uuid4()
    return v
