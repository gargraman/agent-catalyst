"""Eval & drift engine (M3, feature 004) — a pure scoring library (no web/DB, P6).

Public surface is finalized in the package exports below; see data-model.md / contracts for the contract.
"""
from __future__ import annotations

from evals.breaker import CircuitBreaker
from evals.deterministic import DetResult, run_deterministic
from evals.drift import (
    DRIFT_TRIGGERS,
    EfficiencyDelta,
    EvalPolicy,
    Verdict,
    efficiency_delta,
    score_quality,
    verdict,
    verdict_with_reason,
)
from evals.judge import JUDGE_MODEL, JUDGE_VERSION, judge_case
from evals.runner import run_eval
from evals.types import (
    Assertion,
    BaselineAggregate,
    CaseScore,
    DimensionScore,
    EvalCase,
    EvalRunResult,
    FailingCase,
    JudgeVerdict,
    RubricDimension,
    TenancyError,
)

__all__ = [
    "run_eval", "run_deterministic", "score_quality", "efficiency_delta", "verdict",
    "verdict_with_reason", "judge_case", "CircuitBreaker", "DRIFT_TRIGGERS",
    "DetResult", "EfficiencyDelta", "EvalPolicy", "Verdict",
    "JUDGE_MODEL", "JUDGE_VERSION",
    "Assertion", "BaselineAggregate", "CaseScore", "DimensionScore", "EvalCase",
    "EvalRunResult", "FailingCase", "JudgeVerdict", "RubricDimension", "TenancyError",
]
