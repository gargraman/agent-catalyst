# Eval & Drift — gap analysis & build roadmap

> Where the eval + drift module stands (designed vs built), how it integrates, every gap, the correctness
> issues, and a dependency-ordered roadmap. Companion to `backend/05-eval-drift.md` (the design) and
> `backend-integration-map.md` (the mock→backend contract). Read `scope.md` → `spec.md` → `backend/05`
> for intent first. Findings verified against the frontend source on 2026-10-18.

Eval + drift is **defensible core #2** (`scope.md`): the one non-CRUD reason the platform exists — "when
the model changes, nobody notices the agent silently got worse." Today it is a **`Math.random` frontend
mock**; the backend is fully *designed* but unbuilt. This doc is the map and the build order, so the team
can decide what to build when without re-deriving the design.

## 1. The feature (designed)

A version **drifts** when, relative to a frozen trusted **baseline** on a fixed **suite**, behavior
degrades. The verdict (`backend/05 §1`, `spec B5`):

```
PASS ⟺ quality(cand) ≥ quality(base) − Q_THRESHOLD        (quality axis, weighted rubric blend 0–100)
   AND efficiency(cand) within EFF_BUDGET of baseline       (cost% / latency / tokens / tool-calls)
   AND all deterministic hard-assertions green              (correctness floor)
   AND n_cases ≥ MIN_CASES (default 10, else low_confidence)(statistical honesty)
```

Two scoring layers:
- **(1) Deterministic** (`services/evals/deterministic.py`) — schema / required-fields / regex /
  tool-call / refusal / citation. Zero model calls; the correctness floor *and* the judge pre-filter
  (a hard-fail never reaches the judge).
- **(2) LLM-judge** (`services/evals/judge.py`) — **fixed + versioned** (`claude-haiku-4-5`,
  `judge-v1`), rubric 1–5 + pairwise baseline-vs-candidate, cached & batched. The judge is the
  platform's #1 COGS risk, so it is gated hard.

The verdict maps 1:1 to the FE `EvalRun` type (`frontend/src/lib/data.ts:661-670`).

## 2. Implementation flow (designed, end-to-end)

`POST /versions/:id/eval-runs` → `202 {job_id}` → Redis queue → eval worker →
**`runtime.invoke(run_source="eval", allow_cache=False)` per case** → deterministic checks (hard-fail
short-circuits to FAIL) → verdict-cache hit? else judge via Batch (rubric + pairwise) → `verdict()`
math → write `eval_runs` + `eval_results` + set `agent_versions.publishable` → progress streamed to
`jobs`; UI polls `GET /jobs/:id` and patches the row (`resolveEvalRun`).

Publish/re-point call `assert_publishable` (`backend/05 §5`) → `409 publish.eval_gate` /
`publish.low_confidence` otherwise. Config edits in `DRIFT_TRIGGERS` (model / provider / api_version /
params / prompt_hash / guardrail_config / tool_bindings / embedding_model / kb_binding /
retrieval_config / capability_substitution) flip `publishable=false`.

## 3. Integration surface

- **The invariant (`backend/00 §1`, `03 §9`):** the playground, eval workers, and published invoke all
  call the *same* `runtime.invoke(snapshot, inputs, ctx)`, differing only in `RunContext` flags. "You
  evaluated the code that ships," or the verdict is a lie. Eval cannot exist before `runtime.invoke` +
  the gateway.
- **Data model (`backend/01 §4.5`):** `eval_suites`, `eval_cases` (input/assertions/rubric/expected),
  `eval_baselines` (version+suite+`results_hash`, audited), `eval_runs`, `eval_results` (per-case
  det+judge detail). `agent_versions` carries `snapshot_hash` + `publishable`.
- **API (`backend/08`):** `GET /evals`·`/agents/:id/evals`, `POST /agents/:id/evals` (async),
  `GET /jobs/:id`, `POST /agents/:id/eval-suites`·`/cases`, `POST /versions/:vid/baseline`,
  `POST /versions/:vid/publish` (409-gated), `POST /agents/:id/repoint`. Pydantic `EvalRunOut` mirrors
  the FE camelCase 1:1.
- **FE `@backend` contract already placed:** `store.ts:429/453/569/581`,
  `evaluations/page.tsx:41-43`, `agents/[id]/page.tsx:73/79/81`, map `backend-integration-map.md:96-105`.
- **ai-eval approach** maps 1:1: Layer-1 deterministic tests + Layer-2 versioned-prompt eval set vs a
  blessed baseline, gated in CI — the same shape as `backend/05 §2` + `eval_baselines` +
  `backend-integration-plan.md` Component 13.

## 4. Gaps (bucketed, evidence-cited)

**A. Designed, not built (backend absent):** the entire engine (`deterministic.py`/`judge.py`/
`drift.py`); the async `jobs`+queue+worker (the mock applies the verdict synchronously in `addEvalRun`,
`store.ts:432-452`); the server publish-gate + `publishable` flag + `DRIFT_TRIGGERS` invalidation;
baseline freeze/re-freeze (`POST /versions/:vid/baseline` — FE "Re-freeze baseline" is a toast); verdict
cache / Batch / pre-filter / incremental eval; judge calibration + seeded-regression meta-eval.

**B. Mock diverges from design:** verdict = `quality ≥ threshold ? pass : fail` +
`cases<10→low_confidence` (`evaluations/page.tsx:86-87`) — **ignores the efficiency axis and
hard-assertions** even though it fabricates `costDelta`/`latencyDelta`; lifecycle transition at
queue-time, not job-completion (`store.ts:442-443`); `low_confidence` threshold hardcoded, no
`audited_override`; `generateStarterEval` is a canned passing 92.5 / 12-case run
(`eval-explainer.tsx:55-74`).

**C. Missing everywhere (needed for a real feature):** eval **case / suite / rubric authoring UI** — the
biggest gap; "Eval suite" is a 3-item static dropdown (`evaluations/page.tsx:27`), with no way to create
cases, assertions, rubrics, or expected answers; dataset / test-set management (import/label/grow); CI
wiring for the two-layer gate; a production-failure → eval-case triage + drift-alerting surface.

## 5. Correctness issues to resolve (independent of build order)

1. ~~**Judge-selector contradicts the design**~~ and ~~**verdict-cache / `snapshot_hash` bypass**~~ — raised as
   review F2/C2 and now **RESOLVED** (fixed+versioned judge enforced; `snapshot_hash` folds in
   `retrieval_config` + `kb_index_version` per the parked-KB caveat). See `review-fixes.md` C2 + `backend/05 §5–6`.
2. **`drift` is dead (partially addressed).** Agent `drift` (stable/watch/regressed, `data.ts:90`) was only
   seeded/reset; the FE mock now writes it (see the 2026-10-18 status update below), but the **backend** still
   needs the real source wired — the eval worker's drift-transition write + owner notification (`backend/05 §5`,
   `16`) and/or production proxies (`backend/10 §7`). Confirm when the backend is built.
3. **Phasing tension.** `spec` tags the eval-suite/case/eval-run/baseline routes `[P2]`, but the eval/drift
   harness + publish gate are `[P1]`. Reconcile before sequencing — you can't run a suite you can't author.

## 6. Review findings (verified against code)

A critical re-read surfaced items an initial pass under-stated or missed — each verified in the FE
source, not assumed:

- **Trigger→invalidation is absent even as a mock.** `updateAgent` (`store.ts:244`) and
  `updateAgentConfig` (`store.ts:254`) edit model/prompt/params/guardrails/retrieval — *all*
  `DRIFT_TRIGGERS` — without touching `status`/`publishable`/`drift`. A published/draft agent can have
  its behavior-defining config changed and the UI still presents it as publishable: the headline
  guarantee silently not holding.
- **Eval results don't propagate to the denormalized read-models.** `addEvalRun` (`store.ts:432`) writes
  only the run row + a pre-publish status flip; it never updates `agent.evalPass`, `agent.drift`, or
  `Version.evalVerdict`. Only `generateStarterEval`/`rollbackToBaseline` write `evalPass`/`drift`
  (`eval-explainer.tsx:40-41,73`); `evalVerdict` is written **nowhere**. The Eval-pass tile, Drift
  badge, and version verdict go stale vs the latest run.
- **KB→bound-agent ripple missing** (links to issue #3): `reingest`/`addDataSource`
  (`store.ts:388/401`) don't flag bound agents, so a re-ingest (bumps `kb_index_version`) neither
  re-evaluates nor busts the verdict cache.
- **Versioning substrate is cosmetic.** Eval is anchored per `version_id` and baseline per version, but
  "Save as new version" / "Re-freeze baseline" / "Compare" are toasts (`agents/[id]/page.tsx` Versions
  tab) and `Version` is a seed array — there is no immutable snapshot to anchor an eval to. Real eval is
  blocked on real versioning.
- **No authoring RBAC and no version-pin entity.** There is no `eval.author` permission for the proposed
  authoring UI (only `eval.run`/`agent.publish` exist), and no `published_endpoints`/`pinned_version_id`
  entity — `agent.endpoint` is a bare string, so eval-gated `repoint` has no real target.
- **Nuance:** "drift computed from trigger+re-eval" is only half the story — for *published* agents, drift
  comes from production quality proxies (`backend/10 §7`), not triggers. Model both sources.

## 7. Dependent-entity impact (verified)

| Entity | Eval relationship | Current FE state | Effect / gap |
|---|---|---|---|
| Versions / snapshot | eval + baseline anchored per `version_id`; `snapshot_hash` is identity | "Save as new version" is a toast; `Version` is a seed; no `snapshot_hash` | eval has no real per-version anchor; baseline "Frozen at v6" is a string |
| Agent config (prompt/model/params/guardrails/retrieval) | all are `DRIFT_TRIGGERS` → flip `publishable=false` | `updateAgentConfig` ignores eval/publishable/drift | trigger edits leave stale publishable — guarantee unenforced |
| Read-models `evalPass`/`drift`/`Version.evalVerdict` | denormalized from eval results | `addEvalRun` never writes them; `evalVerdict` written nowhere | tiles/badges stale vs latest run |
| KB / RAG (`embedding_model`/`kb_binding`/`retrieval_config`/`kb_index_version`) | triggers; reingest bumps index version | `reingest`/`addDataSource` don't touch bound agents | silent stale PASS + cache-bypass (issue #3) |
| Runs / spans | eval emits per-case runs (`source="Eval"`) + `spans.kind="judge"` | `addRun` mock; eval creates no runs | eval cost/latency unobservable |
| Cost / limits | judge spend → cost → workspace `spend_limits`/BudgetGuard | no judge-cost; Limits stored not enforced | the #1 COGS risk is invisible |
| Channels | route to *published* (pinned) versions | `deleteAgent` cascades to channels | a new failing eval must NOT auto-break live channels (published version is immutable by design) |
| Access / RBAC | `eval.run` (builder+), `agent.publish` (publisher+) | no `eval.author` permission | authoring UI needs an RBAC decision |
| Metrics / dashboard | `EvalStats` feeds evaluations + dashboard charts | seeded per workspace, not computed | charts static until a metrics API computes from `eval_runs` |
| jobs | eval shares the async `jobs` substrate (ingestion/mcp) | synchronous mock | P1 prerequisite |

## 8. Prioritized roadmap (dependency-ordered)

- **P0 — Mock fidelity + the fixable issues (no backend; high demo/contract value):** make the mock
  verdict honor efficiency + a hard-assertion flag; add a real case/suite/rubric **authoring** mock
  (types + store + UI) so suites stop being a static dropdown; remove the judge selector (show the fixed
  versioned judge); propagate eval results to `evalPass`/`evalVerdict`/`drift`; add trigger→invalidation
  (config/guardrail/retrieval edit marks "needs re-eval") and KB→bound-agent ripple; reconcile the
  phasing tension (#4) in `spec`.
- **P1 — Foundational prereqs (owned by other modules; eval is blocked on them):** `runtime.invoke` +
  gateway (`backend/02`/`03`), real immutable **versioning** + `snapshot_hash`, the `jobs`+Redis
  queue+worker substrate (`backend/10 §9`), and the eval DDL migrations (`backend/01 §4.5`).
- **P2 — Eval engine core:** `deterministic.py`, `judge.py` (fixed/versioned), `drift.py` (verdict +
  `publishable`), the eval worker, `assert_publishable` + 409s, baseline freeze/re-freeze (audited).
- **P3 — Cost + trust (what makes it defensible):** verdict cache + Batch + pre-filter (`backend/05 §6`,
  `07 §5`); fix `snapshot_hash` (issue #3); judge **calibration set** (Cohen's κ vs human labels) and the
  **seeded-regression meta-eval** — the scope success metric (**≥80% detection, <10% false positives**).
- **P4 — Feedback loop:** production failure → proposed `eval_cases` (`backend/10 §7`); eval-pass-rate SLO
  alerting (drop >5pp day-over-day); an in-app eval/drift dashboard.

## 9. Verification (how each phase proves itself)

- **P0:** `cd frontend && npm run build && npm run lint` green; author a suite/case, run an eval, confirm
  a cost-regression alone can fail a run, the judge is shown fixed, and editing a trigger marks the agent
  "needs re-eval".
- **P2:** publish with no/failed/low-confidence eval → `409` (the locked spec test); the eval worker
  produces `eval_runs`+`eval_results` from `runtime.invoke`.
- **P3 (headline):** the seeded-regression harness flags **≥80%** of injected regressions at **<10%**
  false positives on an unchanged agent (`scope` success metric 2); judge κ is tracked and re-run on a
  `judge_version` bump; a retrieval change correctly busts the verdict cache.

> Keep the FE hook/type **shapes** (`EvalRun`, `useEvalRuns`, `resolveEvalRun`) stable when wiring the
> backend — the mocks were built as drop-in replacements (`backend-integration-map.md`).

---

> **Status update (2026-10-18) — FE faithful-fix landed (Steps 1–6).** The mock no longer diverges from
> the design on the points below. Verdict now honors the **efficiency axis + hard-assertions + min-cases**
> (`computeVerdict`, `store.ts`); trigger edits and KB re-ingest set **`needsReEval`** (the FE proxy for
> `version.publishable=false`) and ripple to bound agents; `addEvalRun` writes `evalPass`/`drift`/clears
> `needsReEval` on pass; the **publish gate** blocks `eval_failing → needsReEval → low_confidence` (with an
> admin override); the **judge is fixed/versioned** (selector removed); admins get a **workspace eval
> policy** card + capability matrix rows; and **eval authoring** (suites/cases/assertions/rubric, typed from
> the io_contract) now exists under the agent **Evals** tab. Gaps §4/§5 that these cover are closed *in the
> mock*; the backend build (below) remains the real work. §10–§11 record the inputs/outputs and integration
> stance the fix assumes.

## 10. Inputs & outputs — set vs. infer (resolved stance)

How a case's inputs and expected outputs are determined — the question that drove the authoring design
(`backend/05 §2a` is the canonical version; summarized here).

| Dimension | Set by author | Inferred by platform |
|---|---|---|
| **Case input message** | ✓ free-text scenario | — |
| **Input variables** | ✓ values, **typed from the agent `io_contract`** (`AgentConfig.inputVariables`) | defaults pre-filled from the contract; import from prod traffic / dataset (§11) |
| **Deterministic assertions** | ✓ `[{kind, detail?, hard}]`; `detail` required for regex/required_fields/tool_called/exact | — |
| **Judge rubric** | ✓ `[{name, weight}]` dimensions | judge scores each 1–5 at run time |
| **Expected output** | optional reference answer | **inferred**: scored **pairwise vs. the frozen baseline**, not vs. a golden |

Key decisions:
- **No hand-written golden outputs required.** The agent is non-deterministic, so the primary judgment is
  *candidate vs. baseline* under a fixed judge; `expected` is an optional aid, not a gate. This is why
  authoring sets *criteria* (assertions + rubric), not answers.
- **Inputs are typed from the contract**, so an authored case can't drift from the published request schema
  — the case editor renders one field per declared variable (boolean→select, number→numeric).
- **Starter inference, then ownership.** `generateStarterEval` synthesizes a real, editable 12-case suite
  (above `MIN_CASES`) once; the builder then owns and expands it. Run case-count **is** the suite size, so
  a thin suite honestly reads `low_confidence` — "add cases to earn a confident verdict" is a real loop.
- **FE evidence:** `EvalSuite`/`EvalCaseDef`/`Assertion`/`RubricDimension` (`data.ts`), the authoring UI
  (`agents/[id]/page.tsx` → `SuiteAuthoring`/`CaseEditorDialog`), suite-driven runs (`RunEvalDialog`,
  `evaluations/page.tsx`).

## 11. External eval systems — integration stance (LangSmith / LangFlow / etc.)

Can/should we integrate with LangSmith, LangFlow, Braintrust, Promptfoo, etc.? **Stance: import datasets,
do not outsource the verdict.**

- **Why not outsource the judge/verdict.** The platform's whole defensibility (`scope.md` core #2) is a
  **fixed, versioned, calibrated** judge whose cost is gated and whose agreement with humans is measured
  (`backend/05 §2, §8`). A third-party eval runner that upgrades its judge model underneath us reintroduces
  exactly the "the detector itself drifted" failure we exist to prevent, and moves the #1 COGS center
  off-platform where we can't gate it. The verdict, baseline freeze, and publish gate stay in `services/evals`.
- **What we *do* integrate: datasets in, traces out.**
  - **Dataset import** → `eval_cases`: map an external dataset's rows to `EvalCaseDef` (input + vars +
    assertions + rubric). A thin importer per source (LangSmith dataset, CSV/JSONL, Promptfoo test file).
    Candidate API: `POST /api/v1/agents/:id/eval-suites/import?source=…` (net-new; not yet in `08`, flagged
    here as the one additional route §11 implies). Imported inputs are still *set* once, then owned (§10).
  - **Trace/metrics export** → LangSmith/Langfuse for *observability* of eval runs (we already use Langfuse
    for run traces, `10`). Export is one-way telemetry; it never feeds back into the verdict.
  - **Production-failure capture** (`backend/05 §8`, `10 §drift`) is the higher-value "dataset in": real
    thumbs-down / corrections / incidents become new cases, so the suite tracks reality. Prefer this over a
    static third-party dataset.
- **P-tier:** dataset import is **P2** (authoring works without it); trace export piggybacks on the existing
  Langfuse integration; the fixed-judge verdict is **P1** and stays in-house.

> Keep the FE hook/type **shapes** (`EvalRun`, `EvalSuite`, `EvalCaseDef`, `EvalPolicy`, `useEvalRuns`,
> `useSuitesForAgent`, `resolveEvalRun`) stable when wiring the backend — the mocks were built as drop-in
> replacements (`backend-integration-map.md`).
