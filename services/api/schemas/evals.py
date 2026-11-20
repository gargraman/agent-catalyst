"""Eval-run + advisory-publish API contracts (M4, feature 005).

026 US4: `EvalRunIn` names a server-held suite (the inline suite/baseline/policy of feature 005 is gone);
`EvalRunOut` / `EvalRunDetailOut` project a persisted `eval_runs` row. `PublishOut` is advisory — it never implies a
gate was enforced (owner decision #3 / D6).
"""
from __future__ import annotations

import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Verdict = Literal["pass", "fail", "low_confidence"]


# ---- 026 US4: eval runs read suite + baseline + policy from the server (contracts/api.md §E) ----------------------
class EvalRunIn(BaseModel):
    """Owner O3: a client `baseline` / `policy` / `cases` is accepted and dropped unread (extra="ignore") — the server
    owns all three, so a flattering body can never change a verdict (SC-004)."""
    model_config = ConfigDict(extra="ignore")
    version_id: str
    suite_id: str


class DimensionOut(BaseModel):
    name: str
    candidate: float
    baseline: float | None = None
    delta: float | None = None


class FailingCaseOut(BaseModel):
    case_id: str
    reason: str
    hard: bool


class RunBaselineOut(BaseModel):
    origin: Literal["automatic", "frozen"]
    eval_run_id: str
    version_label: str | None = None


class EvalResultOut(BaseModel):
    """Per-case evidence. `candidate_output` is deliberately NOT returned in v1 (judge evidence only)."""
    case_id: str
    passed_deterministic: bool
    quality: float | None = None
    in_comparison: bool
    failure_reason: str | None = None
    cost_usd: float | None = None
    latency_ms: int | None = None


class EvalRunOut(BaseModel):
    id: str
    agent_id: str
    suite_id: str
    suite_name: str | None = None
    version_id: str
    version_label: str | None = None
    status: Literal["completed", "failed"]
    verdict: Verdict | None = None
    reason_code: str
    reason: str
    quality: float | None = None
    quality_delta: float | None = None
    cost_usd: float | None = None
    cost_delta_pct: float | None = None
    latency_ms: float | None = None
    latency_delta_ms: float | None = None
    compared_cases: int
    new_cases: int
    dropped_cases: int
    total_cases: int
    baseline: RunBaselineOut | None = None
    created_by: str
    finished_at: datetime.datetime | None = None


class EvalRunDetailOut(EvalRunOut):
    dimensions: list[DimensionOut] = Field(default_factory=list)
    failing_cases: list[FailingCaseOut] = Field(default_factory=list)
    results: list[EvalResultOut] = Field(default_factory=list)
    policy: dict = Field(default_factory=dict)
    judge_version: str | None = None
    pairwise_rate: float | None = None


# ---- advisory publish (D6) -----------------------------------------------------------------------
class PublishIn(BaseModel):
    version_id: str


class Advisory(BaseModel):
    """026 D5: the newest COMPLETED eval run of the published version (any suite); none → low_confidence/no_eval_run."""
    verdict: Verdict
    reason_code: str
    eval_run_id: str | None = None
    suite_id: str | None = None


class PublishOut(BaseModel):
    agent_id: str
    version_id: str
    status: Literal["published"]
    advisory: Advisory
    advisory_verdict: Verdict | None = None   # back-compat mirror of advisory.verdict (pre-026 shape)
    advisory_note: str = (
        "Publish is advisory in this release: the eval verdict is reported but does NOT gate publishing "
        "until the judge is κ-calibrated (owner decision #3)."
    )


# ---- 026 US4: server-held suites, cases and baseline (contracts/api.md §C/§D) -----------------------------------
class RubricDimensionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    weight: float = Field(default=1.0, gt=0)


class EvalCaseIn(BaseModel):
    """A case authored on the server. `content_hash` is never accepted from the client (extra="forbid")."""
    model_config = ConfigDict(extra="forbid")
    input: dict
    rubric: list[RubricDimensionIn] = Field(min_length=1, max_length=8)
    required_fields: list[str] = Field(default_factory=list)
    expected: Any = None


class EvalCaseOut(BaseModel):
    id: str
    suite_id: str
    input: dict
    rubric: list[RubricDimensionIn]
    required_fields: list[str]
    expected: Any = None
    content_hash: str
    updated_at: datetime.datetime


class EvalSuiteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)


class BaselineOut(BaseModel):
    """Resolved by `api.baseline.resolve_baseline` — the same rule the next eval run compares against (D7)."""
    origin: Literal["frozen", "automatic", "none"]
    eval_run_id: str | None = None
    version_id: str | None = None
    frozen_by: str | None = None
    frozen_at: datetime.datetime | None = None


class EvalSuiteOut(BaseModel):
    id: str
    agent_id: str
    name: str
    case_count: int
    updated_at: datetime.datetime
    baseline: BaselineOut | None = None


class EvalSuiteDetailOut(EvalSuiteOut):
    cases: list[EvalCaseOut] = Field(default_factory=list)


class BaselineFreezeIn(BaseModel):
    eval_run_id: str
