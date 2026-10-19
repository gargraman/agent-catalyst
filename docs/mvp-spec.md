# MVP Spec — Viability Validation (the two unknowns)

> A throwaway CLI experiment that de-risks the project's two biggest unknowns **before** the
> `services/` backend is built. `spec.md` opens with *"Assumes the viability MVP has passed"* — that
> MVP never ran. **This doc defines it.**
>
> This is a **thin CLI** (lives in `mvp/`), **not** the `services/` backend and **not** the frontend.
> No web, no DB, no auth, no backend scaffolding. Its only job is to produce a defensible GO / NO-GO on
> whether the two hard cores (`spec.md §B2`, `§B5`) actually work — treated as a real experiment, not a
> crappy v1.
>
> **Sequence (locked with user 2026-10-18/19):** design-first, *then* MVP — the design docs are done;
> this bridges design → build. **Live provider spend is not yet approved.** The MVP therefore **starts
> on cheap/local models** (cheapest hosted model + a local open model via Ollama) and adds a real
> second hosted provider only once ~$100/mo spend is greenlit. See `scope.md` constraints (budget
> ≤ $100/mo) and decision log.
>
> Read first for intent: `scope.md` ("Biggest unknowns → MVP must answer" + "Success metrics"),
> `spec.md` §B2/§B5/§D/§E, `backend/02-provider-gateway.md`, `backend/05-eval-drift.md`.

---

## 1. Purpose & the two unknowns

Per `scope.md` ("Does AI even make sense here?"), ~90% of this platform is boring CRUD with no model in
it. The engineering risk lives in exactly two places, and both are *unproven*. The MVP exists to turn
each from an open question into a falsifiable pass/fail hypothesis — nothing else.

**H1 — the eval/drift harness (the novel, risky core).**
> *Can the eval/drift harness reliably detect a meaningful quality/behavior drop when the underlying
> model or prompt changes, **without a human reading every transcript**?*

Falsifiable form: on a fixed eval suite scored against a frozen baseline, the harness flags a seeded
regression (weaker model / degraded prompt) as a FAIL, and does **not** flag an unchanged agent.
**If this fails, the platform is just another config UI** (`scope.md`).

**H2 — the provider abstraction (unavoidable, but is it leaky?).**
> *Does one provider-agnostic abstraction actually hold across ≥ 2 providers* — for system prompts,
> params, streaming, and cost/token accounting — *or does "model-agnostic" break the moment an agent
> touches a provider-specific feature?*

Falsifiable form: a single `NormalizedRequest` (`spec.md §B2`) drives ≥ 2 providers end to end
(including streaming); cost computed from **our own** capability profiles matches a hand calculation
within a stated tolerance; and every capability downgrade needed to make a request run on a given model
is **recorded** as a substitution, not silently dropped.

These are deliberately independent: H1 can pass while H2 fails and vice-versa. The MVP must answer both.

---

## 2. Success metrics (the go/no-go)

Lifted verbatim-in-spirit from `scope.md` ("Success metrics" #2) and `spec.md §D` Phase-1 headline.

| Hyp | Metric | Pass bar |
|-----|--------|----------|
| **H1** | Seeded-regression **detection rate** | **≥ 80%** of injected regressions flagged FAIL |
| **H1** | **False-positive rate** on an *unchanged* agent | **< 10%** (ideally measured across repeated runs, not one) |
| **H1 (gate)** | **Judge↔human agreement** on the calibration holdout (§8) | Documented target met (e.g. Cohen's κ ≥ 0.6 / accuracy threshold); **an uncalibrated judge invalidates H1 entirely** |
| **H2** | Same `NormalizedRequest` drives **≥ 2 providers** incl. streaming | Both providers run from one request shape |
| **H2** | Our computed cost vs **hand-calc** | Within a **stated tolerance** (propose **±5%** per call; lock the number before the run so it's not reverse-fit) |
| **H2** | Capability **substitutions recorded** | Every downgrade/rewrite surfaces as a `Substitution`, none silent |

**GO** (proceed to full P1 build, `spec.md` Phase 1): H1 clears both detection **and** false-positive
bars **with a calibrated judge**, AND H2 clears all three of its bars.

**NO-GO** (rethink before building `services/`): any of —
- judge cannot be calibrated to the agreement target (the drift signal is noise);
- detection < 80% or false-positive ≥ 10% even with a calibrated judge (the core doesn't work);
- cost cannot be reconciled to hand-calc within tolerance, or the abstraction leaks so badly that
  "one request, N providers" is a fiction.

A NO-GO is a **success of the process** — it's cheaper to learn it here than after the backend exists.
Either outcome is written back to `scope.md` (§11).

---

## 3. Scope & non-scope

**In scope.** A `mvp/` CLI + scripts that:
- builds one `NormalizedRequest` and runs it through ≥ 2 provider adapters (streaming included);
- computes cost from our own per-`(provider, model)` capability profiles;
- runs the two-layer eval (deterministic + LLM-judge) against a frozen baseline;
- writes a **per-call JSONL log** — one line per model call:
  `{model, prompt_version, input, output, latency_ms, cost_usd, success}` (matches `scope.md`
  Monitoring line + `spec.md §B2` usage IR);
- emits a **scorecard** (detection rate, false-positive rate, judge agreement, cost deltas, substitutions).

**Explicitly NOT in scope** (resist building a mini-platform — this is the #1 failure mode):
- **No web / UI** — CLI only.
- **No DB** — JSONL for logs; SQLite **at most** if a query surface is genuinely needed. No Postgres,
  no pgvector.
- **No auth / tenancy / RBAC**, no workspaces, no guardrail pipeline, no publish gate UI.
- **No `services/` scaffolding** — do not start the real backend tree. The MVP is throwaway; its code
  is not expected to survive into P1 (its *findings* are).
- **No RAG, no MCP, no tools-as-config, no streaming UI** beyond what H2 needs to prove streaming
  normalizes. Guardrails, versioning, cost *enforcement* are all out.

The shapes that **must** be honored (so learnings transfer): `NormalizedRequest` / `NormalizedUsage` /
`Substitution` (`02`) and the verdict math / `JudgeVerdict` (`05`). Keep those recognizable; throw away
everything else.

---

## 4. Models

Two stages, kept **separate** (`scope.md` Models — "keep stages SEPARATE so you can compare and swap
each independently"). Runtime models are what H2 abstracts over; the judge model is what H1 trusts.
**They must never be the same model**, and never share a version knob.

**Stage 1 — runtime models (H2's ≥ 2 providers).** Because spend is not yet approved, start on the
cheap/local pair, then add a real second hosted provider once ~$100/mo is greenlit:

| Role | Candidate | Why |
|------|-----------|-----|
| Cheapest hosted | A low-cost hosted model (e.g. a Haiku-class / GPT-mini-class model) | Real network/provider path, trivial $; proves the hosted adapter |
| Local open | An open model via **Ollama** (e.g. Llama-3.x-8B / Qwen-class) | Zero marginal cost, offline; proves the abstraction spans a wildly different provider |
| (Phase 2, post-greenlight) | A real **second hosted provider** (different vendor from the first) | The honest H2 test is *cross-vendor*, where tool-call format / caching / usage accounting actually diverge |

> **Honesty flag:** two hosted models from the *same* vendor is a weak H2 test — the leaks (tool-call
> format, caching tokens, reasoning tokens) hide. The cheap-hosted + local-Ollama pair is a *strong*
> first test precisely because the two providers are maximally different; the paid second vendor
> hardens it once budget allows.

**Stage 2 — judge model (H1's measuring instrument).** A **pinned, versioned** judge
(`JUDGE_MODEL` + `JUDGE_VERSION`, `05 §2`): e.g. a fixed Haiku-class model tagged `judge-v1`.

> **The judge is subject to its own P0-3 model-deprecation problem.** If the judge model is silently
> upgraded, the "quality" number moves for reasons unrelated to the agent — the one thing a
> drift-detector must never do is drift itself (`05 §2`). So the judge model id **and** prompt hash are
> part of every verdict's identity; changing either is a `judge_version` bump that forces a re-run and
> **re-calibration** (§8), never a silent swap.

---

## 5. The eval/drift harness

Implements a thin slice of `spec.md §B5` / `backend/05`. Two layers, deterministic first (it's free
and pre-filters the expensive judge):

**Layer 1 — deterministic checks (zero model calls).** Per case: structured-output / schema validity,
required-fields present & non-empty, regex / exact-match assertions, **tool-call correctness** (expected
tool + args), and **refusal / empty-output detection** (a spiking refusal rate is itself drift). A
`hard_fail` forces the case to FAIL regardless of the judge — the correctness floor.

**Layer 2 — LLM-judge (gated, versioned).** Two judgments per case (`05 §2`):
- **Rubric 1–5** on each dimension (accuracy, completeness, faithfulness, safety…);
- **Pairwise baseline-vs-candidate** (position order randomized to cancel bias) — the primary judgment,
  since outputs are non-deterministic and a frozen golden answer would rot.

**Verdict vs a FROZEN baseline** (`05 §1,§3`):
```
PASS ⟺ quality(candidate) ≥ quality(baseline) − Q_THRESHOLD   (quality)
   AND efficiency within EFF_BUDGET of baseline                (cost/latency)
   AND all deterministic hard-assertions green                 (correctness floor)
   AND suite has ≥ MIN_CASES else → low_confidence             (statistical honesty)
```
**Drift = a quality drop beyond `Q_THRESHOLD`.**

> **The threshold VALUE is to be tuned empirically in Phase B (§7 below), not guessed now.** Pick it
> from the observed separation between the unchanged-agent runs (should pass) and the seeded-regression
> runs (should fail). In the real product these become a per-workspace `EvalPolicy` (`05 §1`); in the
> MVP they are just a constant we *derive from data* and record.

---

## 6. The trusted baseline + labeled set

The whole signal is relative to a baseline that is **genuinely good** — a bad baseline makes every
delta meaningless.

- **Establishing the baseline.** Run the *intended-good* configuration (the stronger/correct model +
  the good prompt, prompt_version `v1`) over the eval suite, score it, and **freeze** the result set as
  the baseline (its `results_hash` is the trusted anchor, `05 §4`). Re-freezing is explicit and
  recorded — you never silently adopt a new baseline, or quality erodes one "small" step at a time.
- **What a label looks like.** Per `05 §2a`, authors do **not** hand-write golden outputs (non-determinism
  rots them). A labeled case = **criteria**, not a frozen answer:
  - deterministic `assertions[]` (objective: schema, required fields, tool called, no refusal);
  - a judge `rubric[]` (the dimensions scored 1–5);
  - an **optional** `expected` reference answer only where one genuinely exists; otherwise the score is
    inferred by pairwise comparison to the baseline.
- **Labeling effort — stated honestly.** This is real human work, not a free step. Writing ~20–30
  representative cases with good criteria, plus the human-labeled calibration holdout (§8), is the bulk
  of the MVP's human cost. Under-investing here is the quiet way the whole experiment produces a
  confident-but-wrong answer. Budget for it explicitly.

---

## 7. The seeded-regression test (H1's headline)

The direct test of the `scope.md` #2 metric and `spec.md §D` Phase-1 headline. Two arms:

**Regression arm (should FAIL).** Hold the suite + baseline fixed; inject known regressions one at a
time and confirm the harness flags each:
- **Model downgrade** — swap the runtime model to a weaker/cheaper one (e.g. strong → the cheap-hosted
  or local Ollama model).
- **Prompt degradation** — damage the system prompt (drop a key instruction, remove a format rule,
  truncate), prompt_version `v1 → v1-degraded`.
Seed a **variety** of regressions (several models × several prompt lesions) so detection rate is a real
fraction, not 1-of-1. **Detection rate = fraction of seeded regressions flagged FAIL; target ≥ 80%.**

**Control arm (should PASS).** Re-run the *unchanged* agent against the same baseline — ideally several
times, since output is non-deterministic. Any FAIL here is a false positive.
**False-positive rate = fraction of unchanged runs flagged FAIL; target < 10%.**

**Phase B — threshold tuning.** Use the gap between the control arm's quality scores (cluster high) and
the regression arm's (cluster low) to **set `Q_THRESHOLD`** so it separates them. This is why §5 refuses
to guess the threshold up front: it's an output of this test, then frozen and recorded.

---

## 8. Judge calibration (the make-or-break)

> An uncalibrated judge invalidates the entire H1 signal — if we don't know the judge agrees with
> humans, a green scorecard means nothing. This is the single most important step in the MVP (`spec.md
> §E` Risk #1, `05 §8`).

- **Calibration set.** A small **human-labeled holdout** of (case, output) pairs — a human decides
  good / bad (or picks the better of a pair) independently of the judge.
- **Metric.** Measure judge-vs-human agreement: **Cohen's κ** and/or raw accuracy on the holdout.
  Document the target up front (propose **κ ≥ 0.6**, "substantial" agreement; lock it before measuring).
- **Gate.** If the judge misses the target, H1 is **NO-GO on judge grounds** — fix the judge (better
  rubric, better judge model, clearer criteria) and re-calibrate **before** trusting any detection
  number. Re-calibrate on every `judge_version` bump (§4).

---

## 9. The eval task + data

The task must be **representative** of the product (so results transfer) yet **non-sensitive** (so it's
safe to send to external hosted providers).

- **Why not the seed world.** The product's seed world is **SOC / security** (incident triage, alert
  analysis — see `data.ts` / People-Ops split in `scope.md`). That content is **sensitive**: real or
  realistic security data should not be shipped to third-party model APIs during a throwaway experiment.
- **Chosen task (realistic yet safe).** Pick a structured, assertable text task with the *same shape* as
  the real agents — a system prompt, typed inputs, a checkable output — but drawn from public / synthetic
  data. Candidates (recommended first):
  1. **Structured extraction from public text** — e.g. summarize a public changelog / RFC / product doc
     into a fixed JSON schema (fields present, correct types). Exercises deterministic schema checks +
     judge completeness, mirrors the extraction shape of triage without the sensitive content.
  2. **Synthetic "mock-incident" triage** — hand-authored fake incidents (no real data) classified /
     summarized to a rubric. Closest to the product; requires authoring the synthetic set.
  3. **Public-FAQ Q&A to a rubric** — faithfulness + refusal behavior on a public knowledge source.

  Option 1 is the recommended default: strong deterministic surface, trivially non-sensitive, and it
  still forces the judge to score a quality dimension (completeness/faithfulness) that a unit test can't.

---

## 10. Risks

| Risk | Why it bites | Mitigation |
|------|--------------|------------|
| **Validating the validator** | A green scorecard from an uncalibrated judge is worse than no answer — false confidence. | §8 is a hard gate, not optional; κ target locked before measuring. |
| **Non-determinism → weak statistical power** | Too few cases / too few repeats → detection & false-positive rates are noise. | ≥ `MIN_CASES` (default 10, aim 20–30); repeat the control arm; report rates as fractions with n. |
| **Eval-loop cost / latency / flakiness** | `cases × triggers × (rubric + pairwise)` judge calls add up; judge calls can be slow/flaky. | Deterministic pre-filter (free, runs first); cheap pinned judge; cache verdicts by (inputs, judge_version); start on cheap/local models. |
| **Abstraction leakage (H2)** | Tool-call formats, streaming event shapes, cost accounting, and provider-specific features diverge across vendors — "seamless" may be partly a lie (`scope.md` edge cases). | Make leaks **visible** as `Substitution` records, not silent; test cross-vendor, not same-vendor; reconcile cost to hand-calc. |
| **Scope creep → mini-platform** | The strong temptation to add a web UI, a DB, auth, the real adapters. | §3 non-scope is binding; the deliverable is a go/no-go, not a product. Throwaway by design. |

---

## 11. Deliverables / definition of done

Done when all of the following exist and the go/no-go is filled in:

1. **The `mvp/` CLI** — builds one `NormalizedRequest`, runs it across ≥ 2 providers (cheap-hosted +
   local Ollama; a paid 2nd vendor once greenlit), streams, and computes cost from our own profiles.
2. **The per-call JSONL log** — one line per model call:
   `{model, prompt_version, input, output, latency_ms, cost_usd, success}`.
3. **The scorecard** — detection rate, false-positive rate (with n), judge↔human agreement (κ/accuracy),
   cost-vs-hand-calc deltas, and the list of recorded substitutions.
4. **A filled-in GO / NO-GO** against §2, with the empirically tuned `Q_THRESHOLD` (§7) recorded.
5. **Write-back to `scope.md`** — append to the **Decision log**, and fill the **Models** and
   **Evaluation** sections with what the MVP actually learned (judge model + version, runtime pair,
   trusted-baseline procedure, chosen task, thresholds). This is the step that lets `spec.md`'s opening
   assumption — *"assumes the viability MVP has passed"* — finally be true (or honestly flip to NO-GO).
