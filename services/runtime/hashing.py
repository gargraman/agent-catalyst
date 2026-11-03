"""Canonical JSON serializer + the version identity hashes (01L §4).

An unstable hash is a silent correctness failure (05 §5/§6): if ``snapshot_hash`` changes
for an unrelated reason the verdict cache misses and re-bills; if it fails to change on a
behavior edit the publish gate is bypassed and a regressed agent ships. So serialization is
a hard contract.

F16 (critique): canonical_json collapses every real number (int/float/Decimal) to a single
canonical decimal string, so a param expressed as int 1, float 1.0, or Decimal("1.0") can never
desync the hash. The deeper F16 fix — hashing the VALIDATED Pydantic model rather than raw jsonb —
lands with ModelConfig validation (M4); until then compute_snapshot_hash reads the stored jsonb and
relies on _normalize.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal

# The authoritative set of version fields whose change MUST bust the verdict cache + re-arm
# the publish gate (05 §5; reconciliation table 01L §4.3). Deliberately EXCLUDED:
# kb_index_version (KB content is live & independent, 04 §9). Hashed anyway but NOT a trigger:
# io_contract (safe over-coverage). The C2 property test keeps this in lockstep with the mutators.
DRIFT_TRIGGERS: frozenset[str] = frozenset(
    {
        "model",
        "provider",
        "api_version",
        "params",
        "prompt_hash",
        "guardrail_config",
        "memory_config",
        "context_config",
        "tool_bindings",
        "skill_bindings",
        "embedding_model",
        "kb_binding",
        "retrieval_config",
        "capability_substitution",
    }
)


def _normalize(v):
    """Recursively canonicalize a value: Decimal/float → canonical decimal string (no binary
    drift), dict keys sorted, list/tuple order preserved (semantic lists; binding lists are
    pre-sorted by a stable key in compute_snapshot_hash)."""
    if isinstance(v, bool):
        return v  # bool is an int subclass — keep it a JSON boolean, never a number string
    if isinstance(v, (int, float, Decimal)):
        # F16: int 1, float 1.0, and Decimal("1.0") must hash identically — raw jsonb can carry any
        # of them for the same param (e.g. temperature:1 vs 1.0). Collapse all real numbers to one
        # decimal string; Decimal(str(v)) avoids float binary drift, normalize() drops trailing zeros.
        return format(Decimal(str(v)).normalize(), "f")
    if isinstance(v, dict):
        return {k: _normalize(v[k]) for k in sorted(v)}
    if isinstance(v, (list, tuple)):
        return [_normalize(x) for x in v]
    return v


def canonical_json(obj) -> bytes:
    """Deterministic bytes: keys sorted (in _normalize), list order preserved, no whitespace,
    UTF-8, floats/Decimals normalized to a canonical decimal string (01L §4.1)."""
    return json.dumps(
        _normalize(obj), separators=(",", ":"), ensure_ascii=False, sort_keys=False
    ).encode("utf-8")


def sha256_hex(obj) -> str:
    return hashlib.sha256(canonical_json(obj)).hexdigest()


def compute_prompt_hash(system_prompt: str) -> str:
    return hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()


def compute_skill_content_hash(instructions: str, resource_hashes: list[str], version: str) -> str:
    # 01 §4.4: sha256 over {instructions + ordered resource content_hashes + version}
    return sha256_hex({"instructions": instructions, "resources": resource_hashes, "version": version})


def _opt_str(x) -> str | None:
    return str(x) if x is not None else None


def compute_snapshot_hash(av) -> str:
    """The version's content identity — covers every DRIFT_TRIGGER (01L §4.2/§4.3).

    Binding lists are sorted by a stable identity key (NOT the per-row UUID, so an identical
    clone hashes equal). ``kb_index_version`` is excluded (KB content is live); ``io_contract``
    is over-covered. Numeric drift is absorbed by ``canonical_json`` (F16).
    """
    payload = {
        # model/provider are REQUIRED (ModelConfig guarantees them at version creation) → subscript;
        # api_version/params are OPTIONAL → .get. A missing required key on a corrupt row is a data
        # -integrity fault, surfaced as an internal error rather than masked (review #3: intentional).
        "model": av.model_config_["model"],
        "provider": av.model_config_["provider"],
        "api_version": av.model_config_.get("api_version"),
        "params": av.model_config_.get("params", {}),
        "prompt_hash": av.prompt_hash,
        "guardrail_config": av.guardrail_config,
        "io_contract": av.io_contract,
        # memory/context behavior are DRIFT_TRIGGERS (A4): a window/compaction/truncation edit
        # changes behavior, so it must bust the verdict cache + re-arm the publish gate.
        "memory_config": av.memory_config,
        "context_config": av.context_config,
        "tool_bindings": [
            {
                "kind": b.kind,
                "schema": b.schema_snapshot,
                "config_snapshot": b.config_snapshot,  # config IS behavior (I3): URL/egress edits count
                "risk_tier": b.risk_tier,
                "requires_approval": b.requires_approval,
                "subagent_config_id": _opt_str(b.subagent_config_id),
                "auth_mode": b.auth_mode,  # MCP on-behalf-of mode (H3); None for non-MCP
                "mcp_scopes": list(b.mcp_scopes or ()),
            }
            # Stable total sort key = kind + bound tool/mcp_tool/subagent identity (NOT the per-row
            # UUID), so a clone of an identical version hashes equal.
            for b in sorted(
                av.tool_bindings,
                key=lambda b: (b.kind, str(b.tool_id or b.mcp_tool_id or b.subagent_config_id)),
            )
        ],
        "skill_bindings": [
            {"skill_id": str(b.skill_id), "content_hash": b.skill_content_hash}
            for b in sorted(av.skill_bindings, key=lambda b: str(b.skill_id))
        ],
        "retrieval_bindings": [
            {
                "kb_id": str(b.kb_id),
                "embedding_model": b.kb.embedding_model,
                "config": b.retrieval_config,
                # injection is part of the retrieval_config trigger but lives in its own column (H2)
                "injection": b.injection,
                # kb_index_version deliberately EXCLUDED — KB content is live & independent (04 §9)
            }
            for b in sorted(av.retrieval_bindings, key=lambda b: str(b.kb_id))
        ],
        # `or {}` guards a stored JSON null (profile_snapshot may carry "features": null /
        # "fallback": null, not an absent key) — .get(k, {}) returns None in that case, and the
        # chained .get("features", …) on the fallback branch would raise AttributeError.
        "profile_features": av.profile_snapshot.get("features") or {},
        # A1: the fallback model's profile features are snapshotted too, so a fallback capability
        # change is a DRIFT_TRIGGER like the primary's. {} when no fallback is pinned.
        "fallback_profile_features": (av.profile_snapshot.get("fallback") or {}).get("features", {}),
    }
    return sha256_hex(payload)
