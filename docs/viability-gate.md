# Viability gate — GO/NO-GO record

**Status: 🔴 GATE OPEN — no GO recorded.** Neither hypothesis has cleared its bar.
**Date:** 2026-11-03 · **Owner decision:** build the walking skeleton in parallel with the gate open,
publish-gate held advisory (see `scope.md` 2026-11-03 entry).

This is the reproducible GO/NO-GO artifact the governance clause requires (constitution §Governance;
`implementation-plan.md` Phase 0c). It records exactly what the `mvp/` viability experiment has and has
**not** proven. It is deliberately honest: a green engine-plumbing scorecard is **not** a passed
hypothesis (constitution P1/P9 — a mechanism running is not evidence the behaviour is correct).

The bars are defined in `mvp-spec.md §2` (metrics) and `§8` (judge calibration). Nothing here weakens
or reinterprets them.

---

## Decision

| Question | Answer |
|---|---|
| Is a **GO** recorded? | **No.** The gate is open; both hypotheses are blocked on owner-external inputs. |
| Is a **NO-GO** recorded? | **No.** No evidence falsifies either core; the cores are de-risked but unproven at the bar. |
| What is authorized? | **Parallel build of the production walking skeleton**, with the eval judge **advisory-only** (publish NOT gated on the uncalibrated judge — P4). Gate enforcement is deferred, not the evidence. |
| What is NOT authorized? | Any H1 provider call; any paid H2 rerun; representing the judge/publish-gate as calibrated; treating the skeleton's advisory verdict as a viability pass. |

The authorization exists because both blockers are **owner-external** (spend approval + sourcing two
independent raters) and could idle the build indefinitely, while the two defensible cores are already
exercised in `mvp/`. Building in parallel is cheaper than idling; keeping the gate open keeps it honest.

---

## H2 — provider abstraction + cost honesty

**Bar (`mvp-spec §2`):** one `NormalizedRequest` drives ≥ 2 providers incl. streaming; computed cost
(from **our own** profiles) within **±5%** of a hand calc; every capability downgrade recorded as a
`Substitution`.

| Component | State |
|---|---|
| One `NormalizedRequest` → 2 adapters (OpenAI + Ollama), streaming | ✅ wired + exercised offline (`mvp/experiments/h2_providers.py`, `mvp/tests/test_providers.py`) |
| Cost from our own profiles, independently reconciled vs hand-calc | ✅ offline reconciliation test passes; **but against a stale `gpt-5-mini` profile** |
| Capability-substitution reporting | ✅ asserts the expected Ollama reasoning downgrade |
| **±5% cost accuracy on a real, currently-supported model** | ❌ **BLOCKED** |

**Why H2 cannot clear yet (owner-gated):**
- `gpt-5-mini` is scheduled for shutdown (Dec 11, 2026); the profile's pricing is stale and disputed
  (profile says $0.60/$2.40/$0.06 per M; OpenAI lists $0.25/$2.00/$0.025). The approved replacement is
  `gpt-5.6-terra` — its profile is **not migrated** and its pricing is **not yet reviewed/versioned**
  (P7: cost must come from our own *reviewed* profiles — inventing rates is not allowed).
- The paid rerun (reviewed `gpt-5.6-terra` profile + per-call spend preflight under the **$10 combined
  cap**) is **not authorized to run here** and has not been run.

**To close H2:** owner reviews/ratifies a migrated `gpt-5.6-terra` pricing profile → run the capped paid
rerun with retained usage/cost evidence → confirm computed cost within ±5% of hand-calc.

---

## H1 — eval/drift detection

**Bar (`mvp-spec §2`):** seeded-regression **detection ≥ 80%**, **false-positive < 10%**, **and**
judge↔human **Cohen's κ ≥ 0.60** on the calibration holdout (`§8`). **An uncalibrated judge invalidates
H1 entirely.**

| Component | State |
|---|---|
| Engine mechanically flags each seeded regression axis (correctness floor / quality drop / efficiency budget) | ✅ `mvp/experiments/h1_eval.py`, `mvp/tests/test_h1.py` — detection 100%, offline, **mocked judge** |
| Engine passes an unchanged control (no false positive) | ✅ false-positive 0%, offline, mocked judge |
| Suite ≥ `min_cases` so verdicts aren't `low_confidence` | ✅ 12 cases ≥ 10 |
| **Detection/FP on REAL model outputs** | ❌ not measured (no real judge calls) |
| **Judge↔human κ ≥ 0.60** | ❌ **BLOCKED — infeasible** |

**Why H1 cannot clear (owner-gated):**
- κ ≥ 0.60 requires **two independent human raters** on the calibration holdout. They are **unavailable**.
  The owner chose to **preserve the gate and keep H1 blocked** rather than substitute a weaker method
  (`implementation-plan.md`; `scope.md` 2026-10-31).
- Without calibration the judge is uncalibrated, which **invalidates H1** by definition — so the H1
  engine-plumbing result is **pipeline evidence only**, explicitly **not** the hypothesis result.

**To close H1:** source two independent raters → run the κ calibration protocol (`§8`: 60 calibration
pairs, 50/50 held-out, repeatability, confidence intervals; do not tune on the holdout) → κ ≥ 0.60 →
then measure detection/FP on real outputs.

---

## What "advisory mode" means for the skeleton (constitution P4)

Until H1's κ-gate closes, the production eval engine runs through the **same `runtime.invoke`** and
produces a **visible Verdict**, but **publish is not blocked** by it. This is P4-compliant (an
uncalibrated judge MUST NOT gate publish). It is a **temporary, documented divergence** from the
frontend contract (`frontend/.../eval-explainer.tsx` expects `409 publish.eval_gate` on a non-PASS): the
console shows the verdict as advisory. The κ-calibrated hard publish-gate activates once raters are
sourced, closing this divergence.

## Reproduce

```bash
cd mvp && python3 -m pip install -e '.[dev]'
python3 -m pytest -q          # offline engine tests (H1 plumbing + H2 reconciliation)
python  -m run h1             # offline H1 plumbing scorecard (mocked judge — NOT the hypothesis)
# python -m run h2           # REQUIRES owner-approved key + reviewed gpt-5.6-terra profile + $10 cap — do NOT run unprompted
```

## Review these before flipping the gate to GO
- [ ] Migrated, reviewed `gpt-5.6-terra` pricing profile (`mvp/profiles/`) — owner-ratified.
- [ ] Capped paid H2 rerun evidence (usage/cost, ±5% confirmed, console report retained).
- [ ] Two independent raters sourced; κ calibration run; κ ≥ 0.60 recorded with CIs.
- [ ] Real-output H1 detection ≥ 80% / FP < 10% with the calibrated judge.
- [ ] Production judge model/pricing reconciled (Haiku 4.5 → 5.5 naming, deferred to M3).
