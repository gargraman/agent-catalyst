"""Value objects for the eval & drift engine (M3, feature 004) — pure, no web/DB (P6).

The verdict-math types that are a verbatim port from the MVP keep their canonical home in the ported
modules (`DetResult` in `deterministic.py`; `EvalPolicy`/`EfficiencyDelta`/`Verdict` in `drift.py`) and are
re-exported here for convenience. This module adds the NET-NEW types the M3 slice introduces: the authored
`EvalCase`, the judge's `JudgeVerdict`, the per-case `CaseScore`, the frozen `BaselineAggregate` (analyze
finding M1), and the full frontend-`EvalRun`-shaped `EvalRunResult` (FR-025). See data-model.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:  # annotations only — avoids a runtime import cycle with the ported modules
    from evals.deterministic import DetResult


# ---- authored case shapes (FE EvalCaseDef / Assertion / RubricDimension; 05 §2a) ------------------
@dataclass(frozen=True)
class Assertion:
    """One deterministic check. `hard=True` forces case FAIL regardless of the judge (FR-007)."""
    kind: Literal["schema", "required_fields", "regex", "exact", "tool_called", "no_refusal", "non_empty"]
    detail: str | None = None
    hard: bool = False


@dataclass(frozen=True)
class RubricDimension:
    """A judge scoring dimension (1–5), with its blend weight."""
    name: str
    weight: float = 1.0


@dataclass
class EvalCase:
    """An authored eval case. `required_fields`/`expected` feed the deterministic layer; `rubric` feeds the
    judge. The incident-triage contract is only the *default* required-fields set (FR-006)."""
    id: str
    input: str
    workspace_id: str
    vars: dict[str, str] | None = None
    assertions: tuple[Assertion, ...] = ()
    rubric: tuple[RubricDimension, ...] = ()
    expected: str | dict | None = None
    required_fields: tuple[str, ...] | None = None

    def det_spec(self) -> dict[str, Any]:
        """The lightweight mapping the deterministic layer reads (mirrors the MVP `case` dict). Only include
        keys that are set, so `run_deterministic` falls back to its defaults exactly as in the port."""
        spec: dict[str, Any] = {}
        if self.required_fields is not None:
            spec["required_fields"] = list(self.required_fields)
        if self.expected is not None:
            spec["expected"] = self.expected
        return spec

    def rubric_weights(self) -> dict[str, float]:
        return {d.name: d.weight for d in self.rubric}


# ---- judge output (05 §2) ------------------------------------------------------------------------
@dataclass(frozen=True)
class JudgeVerdict:
    """One judge outcome for one case. Advisory-only in this feature — no field gates publish (FR-016).
    Carries the versioned judge identity so the judge itself cannot silently drift (P4/FR-017)."""
    rubric_scores: dict[str, float]            # per-dimension 1–5
    pairwise_winner: Literal["baseline", "candidate", "tie"]
    rationale: str
    judge_version: str
    judge_model: str
    judge_prompt_hash: str
    low_confidence: bool = False               # True when the judge output was unparseable (fail-closed)


# ---- baseline (analyze M1 — pin the shape the runner/judge depend on) -----------------------------
@dataclass(frozen=True)
class BaselineAggregate:
    """The frozen, trusted point of comparison, supplied as an input in this slice (DB-backed
    `eval_baselines` + audited re-freeze are deferred to M4/M5). Carries the overall baseline quality + cost
    + latency aggregates AND the per-case baseline output text the judge needs for pairwise comparison."""
    quality: float                             # overall baseline quality 0–100
    cost_usd: Decimal | float                  # aggregate baseline cost (for the efficiency delta, P7)
    latency_ms: int                            # aggregate baseline latency (surfaced, not gated)
    per_case: dict[str, str]                   # case_id -> baseline output text (pairwise input)
    workspace_id: str                          # tenant (P5)


# ---- per-case + aggregate results ----------------------------------------------------------------
@dataclass
class CaseScore:
    """Per-case rolled-up result the aggregate is built from."""
    case_id: str
    det: "DetResult"
    judge: JudgeVerdict | None                 # None when hard-failed (pre-filter skipped the judge)
    quality: float                             # 0–100 for this case (0 on hard-fail)
    candidate_cost_usd: Decimal | None
    candidate_latency_ms: int | None
    failed: bool


@dataclass(frozen=True)
class DimensionScore:
    """Per-rubric-dimension candidate score (1–5). `baseline`/`delta` are None in this slice — the baseline
    is supplied as an aggregate and is not re-judged per dimension (honest: no fabricated baseline dims)."""
    name: str
    candidate: float
    baseline: float | None = None
    delta: float | None = None


@dataclass(frozen=True)
class FailingCase:
    case_id: str
    reason: str
    hard: bool


@dataclass(frozen=True)
class EvalRunResult:
    """The suite-level aggregate — the full frontend `EvalRun` shape (FR-025), so the M4 API serializes it
    drop-in. The judge's `pairwise_rate` is carried as an ADVISORY signal and does NOT gate (clarify 2026-11-05)."""
    verdict: str                               # Verdict literal from drift.py
    reason: str
    quality: float                             # candidate weighted quality 0–100
    quality_delta: float                       # candidate − baseline
    cost_delta: float                          # % (EfficiencyDelta.cost_pct)
    latency_delta: float                       # ms (surfaced, not gated)
    cases: int
    dimensions: tuple[DimensionScore, ...] = ()
    failing_cases: tuple[FailingCase, ...] = ()
    pairwise_rate: float = 0.0                 # advisory: fraction of decided cases candidate won
    workspace_id: str = ""
    judge_version: str = ""


class TenancyError(Exception):
    """Raised when eval inputs (cases / baseline / candidate) do not all share one workspace (P5/FR-023)."""
