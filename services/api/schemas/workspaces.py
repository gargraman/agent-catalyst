"""Pydantic contracts for workspace administration (feature 006, US4) — mirror data.ts WorkspaceSettings/Limits/EvalPolicy.

Settings/limits/eval-policy within the active workspace. Workspace-root create/delete is a platform-admin plane and
is out of scope (deferred). RBAC enforcement of roles is deferred to M5 (FR-012).
"""
from __future__ import annotations

from pydantic import BaseModel


class Limits(BaseModel):
    monthlySpendUsd: float = 0.0
    maxConcurrent: int = 0
    requestTimeoutS: int = 30


class EvalPolicy(BaseModel):
    minCases: int = 3
    qualityThreshold: float = 0.0
    efficiencyBudgetPct: float = 10.0
    allowLowConfidenceOverride: bool = False


class WorkspaceSettingsOut(BaseModel):
    workspace_id: str
    default_provider: str
    default_model: str
    limits: dict
    eval_policy: dict


class WorkspaceSettingsPatch(BaseModel):
    default_provider: str | None = None
    default_model: str | None = None
    limits: dict | None = None
    eval_policy: dict | None = None


class WorkspaceSpendOut(BaseModel):
    """Feature 009 (M6): month-to-date spend vs the active monthly cap (same figures the gate enforces)."""
    monthToDateUsd: float
    capUsd: float
    remainingUsd: float


class WorkspaceOut(BaseModel):
    id: str
    name: str
    group_prefix: str | None


class WorkspacePatch(BaseModel):
    name: str | None = None
    group_prefix: str | None = None
