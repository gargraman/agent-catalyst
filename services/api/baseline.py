"""Feature 026 US4 — server-held baseline resolution, case-set diff and reason codes (research.md D7/D8).

Pure: no session, no FastAPI. Callers load the rows (suite runs, frozen pointer, baseline results) and pass them in, so
the same function decides what the UI shows (`EvalSuiteOut.baseline`) and what the next run compares against.

- Resolution (D7): the suite's frozen run, else the latest COMPLETED run of the agent's active version on the suite
  (excluding the run being created), else none. Ties on `finished_at` break by id descending (as the publish advisory).
- Case identity (D8): `(case_id, content_hash)`. An edited case is new + dropped; only common cases are compared.
- Reason codes: `hard_fail` first (definitive, needs no baseline/judge/sample — reversed 2026-11-18, review #2), then
  026's `no_baseline > judge_unavailable > below_min_cases > baseline_stale`, then the engine's own order
  (`evals.drift.verdict`: quality_drift > cost_drift > ok), which stays the source of the pass/fail decision. `reason` is rendered only from fixed templates and our own numbers — never provider text.
"""
from __future__ import annotations

import hashlib
import json
import statistics
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

from evals.drift import EvalPolicy, efficiency_delta, verdict as engine_verdict

Origin = Literal["frozen", "automatic", "none"]
ORIGINS: frozenset[str] = frozenset({"frozen", "automatic", "none"})

REASON_CODES: tuple[str, ...] = (
    "ok", "quality_drift", "cost_drift", "hard_fail", "below_min_cases", "no_baseline", "baseline_stale",
    "judge_unavailable",
)

REASON_TEMPLATES: dict[str, str] = {
    "no_baseline": "No baseline run to compare against yet. This run is recorded and can become the baseline.",
    "judge_unavailable": "The judge could not score the compared cases, so quality cannot be assessed.",
    "below_min_cases": "The suite has {total} case(s), below the {min_cases}-case floor for a confident verdict.",
    "baseline_stale": ("Only {compared} of {total} case(s) match the baseline ({new} new, {dropped} dropped), "
                       "below the {min_cases}-case floor for a confident verdict."),
    "hard_fail": "A deterministic hard assertion failed (empty, refusal or malformed output).",
    "quality_drift": ("Quality dropped {drop:.1f} points vs the baseline ({quality_c:.1f} < {quality_b:.1f}), "
                      "beyond the {threshold:.1f}-point threshold."),
    "cost_drift": "Cost {cost_pct:+.1f}% vs the baseline, over the {budget:.1f}% budget.",
    "ok": "Quality held and cost stayed within budget versus the baseline.",
}


# ---- resolution ------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ResolvedBaseline:
    origin: Origin
    run: Any = None                       # the eval_runs row (or row-like), None iff origin == "none"


_NONE = ResolvedBaseline("none", None)


def _newest_key(run) -> tuple:
    fin = getattr(run, "finished_at", None)
    return (fin is not None, fin, str(run.id))        # NULL finished_at sorts last; tie → id DESC


def resolve_baseline(frozen, active_version_id, completed_runs: Iterable, *, exclude_run_id=None) -> ResolvedBaseline:
    """`frozen` = the suite's frozen run (or None); `completed_runs` = the suite's runs (any status — filtered here)."""
    if frozen is not None:
        return ResolvedBaseline("frozen", frozen)
    if active_version_id is None:
        return _NONE
    active, excluded = str(active_version_id), (str(exclude_run_id) if exclude_run_id is not None else None)
    candidates = [r for r in completed_runs
                  if r.status == "completed" and str(r.version_id) == active and str(r.id) != excluded]
    if not candidates:
        return _NONE
    return ResolvedBaseline("automatic", max(candidates, key=_newest_key))


# ---- case diff ---------------------------------------------------------------------------------------------------

def case_content_hash(input: dict, rubric: list, required_fields: Iterable = (), expected: Any = None) -> str:
    """D8 case identity: sha256 of the canonical JSON of (input, rubric, required_fields, expected). Server-computed
    only (a client can never choose it), so an edited case always reads as new + dropped against an older baseline."""
    doc = {"input": input, "rubric": rubric, "required_fields": list(required_fields), "expected": expected}
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class CaseDiff:
    compared: int
    new: int
    dropped: int
    total: int
    common: frozenset                     # {(case_id, content_hash)} as given by the candidate cases
    _keys: frozenset = field(default=frozenset(), repr=False, compare=False)   # str-normalized `common`

    def in_comparison(self, case_id, content_hash) -> bool:
        return (str(case_id), content_hash) in self._keys


def case_diff(candidate_cases: Iterable, baseline_results: Iterable) -> CaseDiff:
    """Candidate cases carry `.id/.content_hash`; baseline results carry `.case_id/.case_hash`."""
    cands = list(candidate_cases)
    base = {(str(r.case_id), r.case_hash) for r in baseline_results}
    cand_keys = {(str(c.id), c.content_hash) for c in cands}
    common = frozenset((c.id, c.content_hash) for c in cands if (str(c.id), c.content_hash) in base)
    keys = cand_keys & base
    return CaseDiff(compared=len(keys), new=len(cands) - len(keys), dropped=len(base - cand_keys), total=len(cands),
                    common=common, _keys=frozenset(keys))


# ---- baseline aggregate over the common set (D7) -----------------------------------------------------------------

@dataclass(frozen=True)
class BaselineAgg:
    quality: float | None                 # mean of non-null per-case quality
    cost_usd: float                       # sum of non-null per-case cost
    latency_ms: float | None              # mean of non-null per-case latency
    per_case: dict[str, str]              # case_id → stored candidate output (feeds the pairwise judge)


def baseline_aggregate(results: Iterable, diff: CaseDiff) -> BaselineAgg:
    common = [r for r in results if diff.in_comparison(r.case_id, r.case_hash)]
    q = [float(r.quality) for r in common if r.quality is not None]
    c = [float(r.cost_usd) for r in common if r.cost_usd is not None]
    lat = [float(r.latency_ms) for r in common if r.latency_ms is not None]
    return BaselineAgg(quality=statistics.fmean(q) if q else None, cost_usd=float(sum(c)),
                       latency_ms=statistics.fmean(lat) if lat else None,
                       per_case={str(r.case_id): r.candidate_output for r in common})


def compared_quality(candidate_quality: dict, results: Iterable, diff: CaseDiff) -> tuple[float | None, float | None]:
    """Quality means over the SAME cases on both sides: common cases (D8) scored in the baseline run AND in this run.
    `candidate_quality` maps case_id → this run's quality (None = not scored: hard-failed or judge failed closed).
    Averaging different subsets would let a judge hiccup on the hardest cases fake a gain (or hide a regression).
    Returns (baseline, candidate); (None, None) when no case is scored on both sides."""
    base = {str(r.case_id): float(r.quality) for r in results
            if r.quality is not None and diff.in_comparison(r.case_id, r.case_hash)}
    cand = {str(k): float(v) for k, v in candidate_quality.items() if v is not None}
    both = base.keys() & cand.keys()
    if not both:
        return None, None
    return statistics.fmean(base[k] for k in both), statistics.fmean(cand[k] for k in both)


# ---- verdict + reason code ----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Decision:
    verdict: Literal["pass", "fail", "low_confidence"]
    reason_code: str
    reason: str
    reason_args: dict = field(default_factory=dict)


def _decision(verdict: str, code: str, **args) -> Decision:
    return Decision(verdict, code, REASON_TEMPLATES[code].format(**args), args)   # type: ignore[arg-type]


def decide(*, origin, diff, det_all_green: bool, judge_available: bool, quality_b, quality_c, cost_b, cost_c,
           policy: EvalPolicy) -> Decision:
    if origin not in ORIGINS:
        raise ValueError(f"unknown baseline origin {origin!r}")
    counts = dict(compared=diff.compared, total=diff.total, new=diff.new, dropped=diff.dropped,
                  min_cases=policy.min_cases)
    # A deterministic hard failure is definitive: it needs no baseline, no judge and no sample size (hard-failed cases
    # are never judged, so checking it after judge_unavailable masked the real cause). Reversed 2026-11-18, review #2.
    if not det_all_green:
        return _decision("fail", "hard_fail")
    if origin == "none":
        return _decision("low_confidence", "no_baseline")
    if not judge_available or quality_b is None or quality_c is None:
        return _decision("low_confidence", "judge_unavailable")
    if diff.total < policy.min_cases:
        return _decision("low_confidence", "below_min_cases", **counts)
    if diff.compared < policy.min_cases:
        return _decision("low_confidence", "baseline_stale", **counts)

    eff = efficiency_delta(float(cost_c or 0.0), float(cost_b or 0.0))
    qb, qc = float(quality_b), float(quality_c)
    v = engine_verdict(diff.compared, det_all_green, qb, qc, eff, policy)   # the engine owns pass/fail ordering
    if v == "pass":
        return _decision("pass", "ok")
    if qb - qc > policy.quality_threshold:
        return _decision("fail", "quality_drift", drop=qb - qc, quality_b=qb, quality_c=qc,
                         threshold=policy.quality_threshold)
    return _decision("fail", "cost_drift", cost_pct=eff.cost_pct, budget=policy.efficiency_budget_pct)
