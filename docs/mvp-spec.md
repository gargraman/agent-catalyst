# MVP Spec — Viability Validation (the two unknowns)

> A standalone CLI experiment for de-risking the project's two highest-risk areas. The `services/`
> directory now contains a small foundation, but no end-to-end backend. H2 has a narrow live-call
> harness; H1 and the combined GO / NO-GO remain incomplete.
>
> This is a **thin CLI** (lives in `mvp/`), not the production `services/` backend or frontend.
> It has no web, database, auth, or production service layer. Its job is to produce a defensible GO / NO-GO on
> whether the two hard cores (`spec.md §B2`, `§B5`) actually work — treated as a real experiment, not a
> crappy v1.
>
> **Terminology:** this is the *viability experiment*, not the user-facing product MVP. The first product
> release is an internal pilot; backend coverage for the current frontend is phased across the roadmap.
>
> **Sequence confirmed (2026-10-31):** finish this standalone viability experiment and record GO/NO-GO
> before starting or extending production backend modules. The earlier implementation-plan sequence
> (build production M1–M3 first, then gate) is superseded. The existing partial `services/` foundation is
> recorded work, not evidence that the gate has passed. H2 has been live-exercised on the cheap
> hosted/local pair; the retained log is insufficient to claim a pass. A second paid hosted provider
> remains untested and is a later confidence check, not part of the currently stated two-provider bar.
> Confirm budget approval before additional paid calls. See `scope.md` constraints and decision log.
>
> Read first for intent: `scope.md` ("Biggest unknowns → MVP must answer" + "Success metrics"),
> `spec.md` §B2/§B5/§D/§E, `backend/02-provider-gateway.md`, `backend/05-eval-drift.md`.

---

## 1. Purpose & the two unknowns

Per `scope.md` ("Does AI even make sense here?"), ~90% of this platform is boring CRUD with no model in
it. The engineering risk lives in two places. H1 remains unproven; H2 has a narrow implementation and
live-call evidence, but its pass criteria are not recorded well enough to declare success. The MVP exists
to turn both into falsifiable pass/fail hypotheses — nothing else.

**H1 — the eval/drift harness (the novel, risky core; not yet implemented).**
> *Can the eval/drift harness reliably detect a meaningful quality/behavior drop when the underlying
> model or prompt changes, **without a human reading every transcript**?*

Falsifiable form: on a fixed eval suite scored against a frozen baseline, the harness flags a seeded
regression (weaker model / degraded prompt) as a FAIL, and does **not** flag an unchanged agent.
**If this fails, the platform is just another config UI** (`scope.md`).

**H2 — the provider abstraction (implemented as a narrow experiment; formal result pending).**
> *Does one provider-agnostic abstraction actually hold across ≥ 2 providers* — for system prompts,
> params, streaming, and cost/token accounting — *or does "model-agnostic" break the moment an agent
> touches a provider-specific feature?*

Falsifiable form: a single `NormalizedRequest` (`spec.md §B2`) drives ≥ 2 providers end to end
(including streaming); cost computed from **our own** capability profiles matches a hand calculation
within a stated tolerance; and every capability downgrade needed to make a request run on a given model
is **recorded** as a substitution, not silently dropped.

These are deliberately independent: H1 can pass while H2 fails and vice-versa. The current H2 code and
call log do not answer H1, and do not yet establish an H2 pass. The MVP must answer both.

---

## 2. Success metrics (the go/no-go)

Lifted verbatim-in-spirit from `scope.md` ("Success metrics" #2) and `spec.md §D` Phase-1 headline.

| Hyp | Metric | Pass bar |
|-----|--------|----------|
| **H1** | Seeded-regression **detection rate** | **≥ 80%** of injected regressions flagged FAIL |
| **H1** | **False-positive rate** on an *unchanged* agent | **< 10%** (ideally measured across repeated runs, not one) |
| **H1 (gate)** | **Judge↔human agreement** on the calibration holdout (§8) | Approved Cohen's κ ≥ 0.60 with human-human agreement reported; currently infeasible because two independent human raters are unavailable. **An uncalibrated judge invalidates H1 entirely.** |
| **H2** | Same `NormalizedRequest` drives **≥ 2 providers** incl. streaming | Both providers run from one request shape |
| **H2** | Our computed cost vs **hand-calc** | Within a **stated tolerance** (propose **±5%** per call; lock the number before the run so it's not reverse-fit) |
| **H2** | Capability **substitutions recorded** | Every downgrade/rewrite surfaces as a `Substitution`, none silent |

**GO** (proceed to full P1 build, `spec.md` Phase 1): H1 clears both detection **and** false-positive
bars **with a calibrated judge**, AND H2 clears all three of its bars.

**NO-GO** (pause expansion of the production backend beyond its current foundation): any of —
- judge cannot be calibrated to the agreement target (the drift signal is noise);
- detection < 80% or false-positive ≥ 10% even with a calibrated judge (the core doesn't work);
- cost cannot be reconciled to hand-calc within tolerance, or the abstraction leaks so badly that
  "one request, N providers" is a fiction.

A NO-GO is a **success of the process** — it's cheaper to learn here than after the end-to-end backend
and product surface are built.
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
- **No production `services/` implementation** — the narrow OpenAI/Ollama adapters exist only to exercise
  H2; they do not complete or automatically become the production gateway. Carry findings and contracts
  into P1, and decide explicitly whether any experiment code is suitable to port.
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

**Stage 1 — runtime models (H2's ≥ 2 providers).** The current H2 harness uses a cheap hosted model
and a local Ollama model. A second paid hosted provider remains future work; the local model does not
validate cross-vendor pricing or hosted-provider differences:

| Role | Current / planned model | Status |
|------|--------------------------|--------|
| Hosted runtime | OpenAI `gpt-5-mini` → `gpt-5.6-terra` | `gpt-5-mini` adapter/log are historical. User selected `gpt-5.6-terra` for the H2 rerun on 2026-10-31; its profile and preflight must be completed before a capped call. OpenAI schedules `gpt-5-mini` shutdown for Dec 11, 2026 and recommends `gpt-5.6-terra`. ([deprecation notice](https://developers.openai.com/api/docs/deprecations), [replacement model](https://developers.openai.com/api/docs/models/gpt-5.6-terra)) |
| Local runtime | Ollama `llama3.1:8b` | H2 adapter and live-call log present; one of four logged outputs is empty |
| Second hosted runtime | Different vendor from OpenAI | Not implemented or tested; a later confidence extension, not required by the current two-provider pass bar in §2 |

> **Honesty flag:** the cheap-hosted + local-Ollama pair is useful for testing a normalized interface,
> but it does not test a second hosted vendor's API behavior, reliability, pricing, or cost accounting.
> Preserve that as a separate H2 limitation.

> **Current H2 profile blocker:** `mvp/profiles/openai__gpt-5-mini.json` marks `gpt-5-mini` active with
> no sunset and prices input/output/cached input at $0.60/$2.40/$0.06 per million tokens. OpenAI now
> marks the model deprecated, schedules shutdown for Dec 11, 2026, and lists $0.25/$2.00/$0.025 for
> those rates; the profile's cache-write charge also needs provider-specific validation. Therefore the
> current profile cannot support an H2 cost-accuracy pass. The user selected `gpt-5.6-terra` for the
> rerun and approved a $10 combined hosted-spend cap. Correct the lifecycle, model ID, price rates, and
> cache semantics for the selected model, then preflight worst-case usage before any call.
> ([OpenAI model page](https://developers.openai.com/api/docs/models/gpt-5-mini),
> [deprecation notice](https://developers.openai.com/api/docs/deprecations))
> OpenAI's listed replacement, `gpt-5.6-terra`, is $2/M input and $12/M output, versus $0.25/$2 for
> `gpt-5-mini`; that is 8× the input rate and 6× the output rate. Confirm each H2 request fits within
> the approved $10 combined cap before sending it. ([GPT-5.6 Terra model and pricing](https://developers.openai.com/api/docs/models/gpt-5.6-terra))

**Stage 2 — judge model (H1's measuring instrument; selected, not implemented).** The approved candidate is
Anthropic Claude Haiku 5.5, API model ID `claude-haiku-5-5`, tagged locally as `judge-v1`. Anthropic
documents this as a fixed model ID with no date suffix, currently active, and suitable for extraction;
its listed price is $0.10/M input and $0.50/M output tokens for prompts up to 100K tokens. This is a
model selection is approved, but no H1 provider calls are authorized until the human-calibration
feasibility blocker in §8 is resolved.
([model and pricing](https://platform.claude.com/docs/en/models/haiku-5-5/overview),
[model ID and settings](https://platform.claude.com/docs/en/models/haiku-5-5/migration-guide))

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

> **The threshold VALUE must be tuned on a separate development set, then frozen before the H1
> measurement set is scored.** Do not tune against the 50 control / 50 regression holdout proposed in
> §8; that would overfit the gate to its own test results. The MVP records the development set,
> selected threshold, and frozen measurement-set identifiers.

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
- **Labeling effort — stated honestly.** This is real human work, not a free step. The proposal uses a
  separate development set, 60 human-labeled calibration pairs, and 100 held-out H1 documents. Writing
  extraction criteria and double-rating the calibration pairs is the bulk of the human cost. Budget
  and schedule this explicitly before treating the gate as a near-term deliverable.

---

## 7. The seeded-regression test (H1's headline)

The direct test of the `scope.md` #2 metric and `spec.md §D` Phase-1 headline. Two arms:

**Regression arm (should FAIL).** Hold the suite + baseline fixed and use pre-registered prompt lesions
for the selected extraction task: remove a required-facts instruction and remove the evidence-quote
requirement. Keep model/provider fixed so this test measures detection of prompt drift rather than
conflating it with provider/model changes. Apply the lesions to the held-out regression set; report
results per lesion as well as in aggregate. **Detection rate = fraction of seeded regressions flagged
FAIL; target ≥ 80%.**

**Control arm (should PASS).** Re-run the *unchanged* agent against the same baseline — ideally several
times, since output is non-deterministic. Any FAIL here is a false positive.
**False-positive rate = fraction of unchanged runs flagged FAIL; target < 10%.**

**Phase B — threshold tuning.** Use a separate development set to choose `Q_THRESHOLD`; freeze it before
scoring the held-out control/regression arms. The held-out arms measure the frozen system and cannot be
used to tune it. See the proposed sample sizes and count bars in §8.

---

## 8. Judge calibration (the make-or-break)

> An uncalibrated judge invalidates the entire H1 signal — if we don't know the judge agrees with
> humans, a green scorecard means nothing. This is the single most important step in the MVP (`spec.md
> §E` Risk #1, `05 §8`).

- **Protocol approved in principle on 2026-10-31; calibration feasibility is blocked, so this does not
  authorize H1 provider calls.** The user approved the judge, sample design, thresholds, repeatability
  check, and $10 combined hosted-spend cap. The user also reported that two independent human raters are
  unavailable. Do not substitute a weaker calibration design without a new decision.
  - **Judge:** Anthropic Claude Haiku 5.5, API ID `claude-haiku-5-5`, local `JUDGE_VERSION=judge-v1`.
    Freeze the judge prompt/rubric hash, schema, model ID, API settings, and provider profile in each
    result. Set adaptive thinking to `effort=medium`; omit `temperature`, `top_p`, and `top_k` because
    this model does not support those sampling controls. The fixed model ID is not a promise of
    deterministic outputs, so measure repeat variation. Bump `JUDGE_VERSION` and recalibrate whenever
    the model, prompt, rubric, schema, or settings change.
  - **Calibration holdout:** 60 pairwise examples, held apart from the H1 measurement set, balanced
    between examples where the candidate is materially worse and examples where it is not. Two human
    raters label each pair independently and blind to the judge's answer and experiment arm; adjudicate
    disagreements before comparison. Freeze the examples and labels before running the judge.
  - **Agreement report and gate:** report the two human raters' agreement, judge-vs-each-rater and
    judge-vs-adjudicated Cohen's κ, raw accuracy, confusion matrix, class counts, and 95% confidence
    intervals. Proposed calibration gate: human-human κ and judge-vs-adjudicated κ are each at least
    **0.60**. If human agreement is below target, the rubric/labels are not a usable reference; if the
    judge misses the target, H1 is **NO-GO on judge grounds**. Do not tune on this holdout. Recalibrate
    after every `JUDGE_VERSION` bump.
  - **Data split:** reserve 20 public documents for development and threshold tuning, 60 separate
    pairwise examples for calibration, and 100 held-out documents for measurement. No document may
    cross these splits. Freeze `Q_THRESHOLD` using only the 20-document development set.
  - **H1 measurement set:** use 50 independent public documents for unchanged controls and 50 separate
    documents for seeded prompt regressions, disjoint from calibration and development. Proposed
    raw-count bars are at least 40/50 regressions detected and at most 4/50 unchanged controls falsely
    flagged. Report exact
    numerators/denominators and Wilson 95% intervals. These are an internal feasibility gate, not a
    market-grade reliability claim; a result whose repeat runs change the GO/NO-GO is inconclusive.
  - **Repeatability:** rerun a frozen 10% sample three times with the same model ID, effort, prompt,
    inputs, and rubric. Report verdict flip rate and whether repeats change aggregate gate outcomes;
    do not assume `temperature=0` or bit-for-bit determinism.
  - **Spend cap:** hard cap **$10 USD total incremental hosted-model spend** for the remaining
    H1 work and the required H2 rerun, counting failed attempts and retries. Before each call, reserve
    a worst-case amount from input size, configured maximum output, and current provider rates; after
    the call reconcile returned token usage and billing. If usage is missing, count the attempt at its
    preflight maximum. Stop before a call could exceed the cap. The cap authorizes the selected H2 rerun
    only after its reviewed Spec Kit artifacts, model profile, and per-call preflight are complete; H1
    calls remain blocked by human-rater availability. Haiku 5.5's listed text rates are $0.10/M input and $0.50/M output for
    prompts up to 100K tokens, with higher rates above that size; runtime spend must be budgeted
    separately using the selected runtime profile. ([Anthropic pricing and lifecycle](https://platform.claude.com/docs/en/models/haiku-5-5/overview))

- **Status.** The task/domain, candidate schema, Haiku 5.5 judge, sample design, thresholds, repeatability
  check, and $10 cap were approved by the user on 2026-10-31. The exact corpus/URLs/hashes and reviewed
  Spec Kit artifacts remain incomplete. More importantly, two independent human raters are unavailable,
  so the approved calibration gate cannot currently be executed. The user chose to preserve the gate and
  keep H1 blocked until two independent raters are available. Do not run H1 calls or substitute a weaker
  calibration method.

---

## 9. The eval task + data

The task must be **representative** of the product (so results transfer) yet **non-sensitive** (so it's
safe to send to external hosted providers). The current H2 harness uses one synthetic mock-incident
triage prompt. This is suitable for provider-path smoke testing only; it is not a labeled H1 evaluation
set, calibration holdout, or representative quality benchmark.

- **Why not the seed world.** The product's seed world is **SOC / security** (incident triage, alert
  analysis — see `data.ts` / People-Ops split in `scope.md`). That content is **sensitive**: real or
  realistic security data should not be shipped to third-party model APIs during a throwaway experiment.
- **H1 task selection and candidate schema (approved 2026-10-31).** Use **structured extraction from
  public software release notes/changelogs**. The
  experiment will extract facts from public release notes, changelogs, RFCs, or comparable documents
  into a typed JSON output. The approved candidate schema is:
  `source_title: string`, `published_date: string|null`, `summary: string`, and
  `key_changes: [{claim: string, evidence_quote: string}]`. Exact quotes can be checked against the
  source deterministically; completeness and whether claims faithfully capture important changes
  remain for human/judge scoring. Pin each source by URL and content hash, and prefer documents
  published after the judge/runtime training cutoffs to reduce benchmark contamination. The exact
  corpus, required facts, and rubric must be frozen before implementation or model calls. Do not use
  real or realistic SOC/security data.

---

## 10. Risks

| Risk | Why it bites | Mitigation |
|------|--------------|------------|
| **Validating the validator** | A green scorecard from an uncalibrated judge is worse than no answer — false confidence. | §8 is a hard gate, not optional; κ target locked before measuring. |
| **Non-determinism → weak statistical power** | Too few cases / too few repeats → detection & false-positive rates are noise. | Proposed 50 held-out cases per H1 arm; repeat a frozen 10% sample; report rates as fractions with n and Wilson 95% intervals. This is still only an internal feasibility signal. |
| **Eval-loop cost / latency / flakiness** | `cases × triggers × (rubric + pairwise)` judge calls add up; judge calls can be slow/flaky. | Deterministic pre-filter (free, runs first); fixed judge ID/settings; enforce the proposed $10 experiment cap; cache verdicts by (inputs, judge_version). |
| **Abstraction leakage (H2)** | Tool-call formats, streaming event shapes, cost accounting, and provider-specific features diverge across vendors — "seamless" may be partly a lie (`scope.md` edge cases). | Make leaks **visible** as `Substitution` records, not silent; test cross-vendor, not same-vendor; reconcile cost to hand-calc. |
| **Scope creep → mini-platform** | The strong temptation to add a web UI, a DB, auth, or turn the narrow H2 adapters into a general product gateway. | §3 non-scope is binding; the deliverable is a go/no-go, not a product. Keep production gateway work in `services/`. |

---

## 11. Deliverables / definition of done

### Current implementation status (2026-10-31)

| Deliverable | Status |
|---|---|
| H2 CLI, normalized request, OpenAI/Ollama streaming adapters | Implemented. The current local log records 4 calls per provider; one Ollama call has empty output. |
| H2 cost/substitution assertions | The paid-provider cost check and an expected Ollama reasoning-substitution check are implemented; mocked tests include a double-counting regression. The current OpenAI profile's lifecycle and pricing conflict with OpenAI's published values, so the cost bar is not met. Exhaustive verification of every downgrade is not established, and the local JSONL log retains neither token counts nor substitutions/assertion output. |
| H1 baseline, LLM judge, human calibration, regression/control arms | Not implemented; CLI commands are stubs. |
| Scorecard and GO / NO-GO | Not produced. The combined viability gate remains open. |
| `scope.md` findings write-back | Pending the H1/H2 result; do not record the viability MVP as passed. |

The log format currently records calls but has no run ID or timestamp. Preserve the CLI report and exit
status for future runs; improve the evidence record before using live results to tune thresholds or make
product claims.

### Definition of done

Done when all of the following exist and the go/no-go is filled in:

1. **The `mvp/` CLI** — builds one `NormalizedRequest`, runs it across ≥ 2 providers (the current
   hosted OpenAI + local Ollama pair), streams, and computes cost from our own profiles. A second paid
   hosted vendor is a follow-on confidence check, not a prerequisite for the current GO/NO-GO bar.
2. **The per-call JSONL log** — one line per model call:
   `{model, prompt_version, input, output, latency_ms, cost_usd, success}`.
3. **The scorecard** — detection rate, false-positive rate (with n), judge↔human agreement (κ/accuracy),
   cost-vs-hand-calc deltas, and the list of recorded substitutions.
4. **A filled-in GO / NO-GO** against §2, with the empirically tuned `Q_THRESHOLD` (§7) recorded.
5. **Write-back to `scope.md`** — append to the **Decision log**, and fill the **Models** and
   **Evaluation** sections with what the MVP actually learned (judge model + version, runtime pair,
   trusted-baseline procedure, chosen task, thresholds). This is the step that lets `spec.md`'s opening
   assumption — *"assumes the viability MVP has passed"* — finally be true (or honestly flip to NO-GO).
