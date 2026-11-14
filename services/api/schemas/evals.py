"""Eval-run + advisory-publish API contracts (M4, feature 005).

`EvalRunIn` carries the suite inline (D5) and mirrors the M3 `EvalCase`/`BaselineAggregate`/`EvalPolicy`;
`EvalRunOut` maps `evals.EvalRunResult` (mirrors FE `EvalRun`). `PublishOut` is advisory — it never implies a
gate was enforced (owner decision #3 / D6).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Verdict = Literal["pass", "fail", "low_confidence"]


# ---- inline suite (mirrors M3 evals types) -------------------------------------------------------
class RubricDimIn(BaseModel):
    name: str
    weight: float = 1.0


class CaseIn(BaseModel):
    id: str
    input: str
    rubric: list[RubricDimIn] = Field(default_factory=list)
    required_fields: list[str] | None = None
    expected: str | dict | None = None


# TODO(backend): the baseline is CALLER-SUPPLIED on every eval-run — there is no stored, audited baseline and no
#   re-freeze endpoint (backend/05 §4: "explicit + audited so quality can't erode one silent step at a time"). A
#   caller can pass any baseline it likes. Persist a frozen baseline per agent/version + POST …/baseline (audit_logs
#   row); the console's reFreezeBaseline (frontend store.ts) is local-only until then.
class BaselineIn(BaseModel):
    quality: float
    cost_usd: float
    latency_ms: int
    per_case: dict[str, str] = Field(default_factory=dict)   # case_id -> baseline output text


class PolicyIn(BaseModel):
    min_cases: int = 10
    quality_threshold: float = 90.0
    efficiency_budget_pct: float = 10.0
    allow_low_confidence_override: bool = False


class EvalRunIn(BaseModel):
    version_id: str
    cases: list[CaseIn]
    baseline: BaselineIn
    policy: PolicyIn = Field(default_factory=PolicyIn)


# ---- verdict out (maps evals.EvalRunResult; mirrors FE EvalRun) ----------------------------------
class DimensionOut(BaseModel):
    name: str
    candidate: float
    baseline: float | None = None
    delta: float | None = None


class FailingCaseOut(BaseModel):
    case_id: str
    reason: str
    hard: bool


class EvalRunOut(BaseModel):
    verdict: Verdict
    reason: str
    quality: float
    quality_delta: float
    cost_delta: float
    latency_delta: float
    cases: int
    dimensions: list[DimensionOut] = Field(default_factory=list)
    failing_cases: list[FailingCaseOut] = Field(default_factory=list)
    pairwise_rate: float = 0.0


# ---- advisory publish (D6) -----------------------------------------------------------------------
class PublishIn(BaseModel):
    version_id: str


class PublishOut(BaseModel):
    agent_id: str
    version_id: str
    status: Literal["published"]
    advisory_verdict: Verdict | None = None
    advisory_note: str = (
        "Publish is advisory in this release: the eval verdict is reported but does NOT gate publishing "
        "until the judge is κ-calibrated (owner decision #3)."
    )
