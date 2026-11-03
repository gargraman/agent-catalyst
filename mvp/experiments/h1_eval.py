"""H1 (eval/drift) PLUMBING harness — OFFLINE, mocked judge (no paid calls).

Proves the eval engine MECHANICALLY flags a seeded regression and passes an unchanged agent: a
baseline vs candidate runs through the real deterministic checks + a *mocked* rubric judge →
`score_quality` → `verdict()`, exercising all three verdict axes (correctness floor, quality drop,
efficiency budget) plus a control arm.

IMPORTANT — this is engine-plumbing evidence ONLY, not the H1 hypothesis result. The GO bars
(≥80% detection, <10% false-positive, judge↔human Cohen's κ ≥ 0.60) require REAL judge calls on
real model outputs AND two human raters — both owner-gated (docs/mvp-spec.md §2, §8). A green
scorecard here means "the pipeline wiring detects a seeded drop", not "the judge works".
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

sys.path.insert(0, __file__.rsplit("/", 2)[0])  # make flat mvp modules importable (run via -m run h1)

from evals.deterministic import run_deterministic
from evals.drift import EvalPolicy, Verdict, efficiency_delta, score_quality, verdict

# Rubric dimensions + weights for the synthetic SOC-triage task (05 §judge).
_WEIGHTS = {"accuracy": 2.0, "completeness": 1.0, "actionability": 1.0}
_PERFECT = {"accuracy": 5.0, "completeness": 5.0, "actionability": 5.0}  # judge gives top marks
_WEAK = {"accuracy": 3.0, "completeness": 3.0, "actionability": 3.0}     # subtly-worse model

# Fixed 12-case suite (≥ EvalPolicy.min_cases so the verdict is never low_confidence by default).
_SEVERITIES = ["critical", "high", "medium", "low", "info", "high",
               "medium", "critical", "low", "high", "medium", "info"]
SUITE = [
    {"id": f"case-{i:02d}", "input": f"synthetic SOC alert #{i}", "expected": sev,
     "rubric_weights": _WEIGHTS}
    for i, sev in enumerate(_SEVERITIES)
]

_COST_PER_CASE = 0.001


def _good_output(case: dict) -> str:
    """A valid triage JSON matching the deterministic contract (severity/summary/next_step)."""
    return (
        '{"severity": "%s", "summary": "triaged %s", "next_step": "isolate host"}'
        % (case["expected"], case["id"])
    )


@dataclass
class Arm:
    """One agent run over the whole suite: its per-case output, mocked rubric scores, and per-case cost."""
    outputs: dict[str, str]
    scores: dict[str, dict[str, float]]
    costs: dict[str, float]


@dataclass
class ArmAgg:
    det_all_green: bool
    quality: float
    cost: float


def _baseline_arm() -> Arm:
    return Arm(
        outputs={c["id"]: _good_output(c) for c in SUITE},
        scores={c["id"]: dict(_PERFECT) for c in SUITE},
        costs={c["id"]: _COST_PER_CASE for c in SUITE},
    )


def _control_arm() -> Arm:
    # Unchanged agent — identical to baseline. Must NOT be flagged (true negative).
    return _baseline_arm()


def _seed_correctness() -> Arm:
    # One case regresses to invalid JSON (hard deterministic fail) → correctness floor trips.
    arm = _baseline_arm()
    arm.outputs["case-03"] = "I'm sorry, I cannot help with that."  # refusal → hard fail
    return arm


def _seed_quality() -> Arm:
    # Outputs still valid (deterministic green), but the judge scores them lower → quality drop.
    arm = _baseline_arm()
    arm.scores = {c["id"]: dict(_WEAK) for c in SUITE}
    return arm


def _seed_efficiency() -> Arm:
    # Same quality + correctness, but 2x cost → efficiency budget blown.
    arm = _baseline_arm()
    arm.costs = {c["id"]: _COST_PER_CASE * 2 for c in SUITE}
    return arm


def _aggregate(arm: Arm) -> ArmAgg:
    det_all_green = True
    qualities: list[float] = []
    for case in SUITE:
        cid = case["id"]
        det = run_deterministic(arm.outputs[cid], case)
        if not det.passed:
            det_all_green = False
        # Hard-failed cases skip the judge (05) and count as zero quality; others use the mocked rubric.
        q = 0.0 if det.hard_fail else score_quality(arm.scores[cid], case["rubric_weights"])
        qualities.append(q)
    quality = sum(qualities) / len(qualities) if qualities else 0.0
    return ArmAgg(det_all_green=det_all_green, quality=quality, cost=sum(arm.costs.values()))


def _verdict_for(candidate: Arm, baseline_agg: ArmAgg, policy: EvalPolicy) -> Verdict:
    cand = _aggregate(candidate)
    return verdict(
        n_cases=len(SUITE),
        det_all_green=cand.det_all_green,
        quality_b=baseline_agg.quality,
        quality_c=cand.quality,
        eff=efficiency_delta(cand.cost, baseline_agg.cost),
        policy=policy,
    )


def default_policy() -> EvalPolicy:
    # quality_threshold in 0–100 quality points; efficiency_budget_pct in % cost delta.
    return EvalPolicy(min_cases=10, quality_threshold=10.0, efficiency_budget_pct=20.0)


_SEEDS = {
    "correctness": _seed_correctness,
    "quality": _seed_quality,
    "efficiency": _seed_efficiency,
}


def run_h1(policy: EvalPolicy | None = None) -> dict:
    """Run the control + seeded-regression arms and return a plumbing scorecard."""
    policy = policy or default_policy()
    baseline_agg = _aggregate(_baseline_arm())

    control_verdict = _verdict_for(_control_arm(), baseline_agg, policy)
    seed_verdicts = {name: _verdict_for(fn(), baseline_agg, policy) for name, fn in _SEEDS.items()}

    detected = sum(1 for v in seed_verdicts.values() if v == "fail")
    false_positive = 1 if control_verdict != "pass" else 0
    return {
        "n_cases": len(SUITE),
        "min_cases": policy.min_cases,
        "control_verdict": control_verdict,
        "seed_verdicts": seed_verdicts,
        "detection_rate": detected / len(seed_verdicts),
        "false_positive_rate": false_positive / 1,
        "note": "engine-plumbing only; GO bars need real judge calls + κ≥0.60 raters (owner-gated)",
    }


def assert_h1(result: dict) -> None:
    """Mechanical invariants: the control passes, every seeded regression is flagged."""
    assert result["control_verdict"] == "pass", f"false positive: control → {result['control_verdict']}"
    for name, v in result["seed_verdicts"].items():
        assert v == "fail", f"missed regression seed[{name}] → {v}"
    assert result["n_cases"] >= result["min_cases"], "suite smaller than min_cases → would be low_confidence"


def _print_report(r: dict) -> None:
    print("H1 PLUMBING SCORECARD (offline, mocked judge — NOT the hypothesis result)")
    print(f"  suite cases:          {r['n_cases']} (min_cases {r['min_cases']})")
    print(f"  control (unchanged):  verdict={r['control_verdict']}  (expect pass)")
    for name, v in r["seed_verdicts"].items():
        print(f"  seed[{name:<11}]:  verdict={v}  (expect fail)")
    print(f"  detection_rate:       {r['detection_rate']:.0%}  (seeds flagged)")
    print(f"  false_positive_rate:  {r['false_positive_rate']:.0%}")
    print(f"  NOTE: {r['note']}")


def main() -> int:
    r = run_h1()
    _print_report(r)
    assert_h1(r)
    print("H1 plumbing OK — pipeline mechanically detects seeded regressions and passes the control.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
