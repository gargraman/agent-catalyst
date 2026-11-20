# API Contract Deltas — 026 Core Loop Live

All routes are under the existing auth/tenancy (`X-Workspace-Id`, principal). Errors use the 08 envelope
`{"error":{code,message,detail?}}`. A denial on a per-resource check returns **404** (indistinguishable from absent).
**Bold** = new or changed. Everything else is unchanged and only wired.

## A. Agents (`api/routers/agents.py`)

| Route | Perm | Change |
|---|---|---|
| `GET /api/v1/agents`, `GET /api/v1/agents/{id}` | view | `AgentOut` **gains** `description, provider, model, draft (resolved Draft), draft_dirty: bool, restore_status: str\|null`. `VersionOut` **gains** `created_by: str\|null, is_active: bool`. `ETag` header (existing) |
| `PATCH /api/v1/agents/{id}` | agent.edit | `AgentPatch` **gains** `description?: str(≤2000)`, `draft?: Draft` (full replace). `If-Match` required (existing). Archive: `status:"archived"` stores `status_before_archive`. Restore: `status:<restore_status>`. Any other transition out of `archived` → 400. Repoint/archive/restore write audit rows. Response carries the new `ETag` |
| `POST /api/v1/agents/{id}/versions` | agent.edit | **Built from the resolved draft** (D2). Writes tool/skill/kb bindings in the same txn. Sets `created_by`. A missing bound resource → 404 `not_found`, `detail:{binding:"knowledge_base"\|"tool"\|"skill", id}` |
| `POST /api/v1/agents/{id}/publish` | agent.publish | Response **gains** `advisory: {verdict, reason_code, eval_run_id\|null, suite_id\|null}` (D5). Audit `agent.publish`. Never blocked by the verdict |

`Draft` schema: see [data-model.md](../data-model.md#value-object--draft-stored-in-agentsdraft).

**Errors added**: 409 **`agent.archived`** (console invoke + eval-run on an archived agent).

## B. Invoke (`api/routers/invoke.py`)

| Route | Change |
|---|---|
| `POST /api/v1/agents/{id}/invoke` (console) | `InvokeIn` **gains** `target: "active"\|"draft"\|"version" = "active"`. `version_id` is required iff `target="version"`, else 400. `draft` → transient snapshot (D3), run recorded with `version_id=null`, `run_start.version="draft"`. Archived → 409 `agent.archived` |
| `POST /v1/agents/{id}/invoke` (published) | No body change (no `target`). Archived → **404 `not_found`** |

SSE frames: unchanged (see research G20). The FE treats a stream that ends without `done` as **interrupted**.

## C. Eval suites & cases (new; `api/routers/eval_suites.py`)

| Route | Perm | Body → Response |
|---|---|---|
| `GET /api/v1/agents/{id}/eval-suites` | view + ensure_use | → `[EvalSuiteOut{id, agent_id, name, case_count, updated_at, baseline: BaselineOut\|null}]` |
| `POST /api/v1/agents/{id}/eval-suites` | eval.author + ensure_manage | `{name}` → 201 `EvalSuiteOut`. Duplicate name → 409 `conflict` |
| `GET /api/v1/eval-suites/{sid}` | view + ensure_use | → `EvalSuiteOut` + `cases: [EvalCaseOut]` |
| `PATCH /api/v1/eval-suites/{sid}` | eval.author + ensure_manage | `{name}` → `EvalSuiteOut` |
| `DELETE /api/v1/eval-suites/{sid}` | eval.author + ensure_manage | 204. Has runs → 409 **`eval.suite_has_runs`** |
| `POST /api/v1/eval-suites/{sid}/cases` | eval.author + ensure_manage | `EvalCaseIn` → 201 `EvalCaseOut` |
| `PUT /api/v1/eval-suites/{sid}/cases/{cid}` | eval.author + ensure_manage | `EvalCaseIn` → `EvalCaseOut` (`content_hash` recomputed) |
| `DELETE /api/v1/eval-suites/{sid}/cases/{cid}` | eval.author + ensure_manage | 204 |

- `EvalCaseIn = {input: dict, rubric: [{name, weight>0}] (1–8), required_fields: [str] = [], expected?: any}`, `extra="forbid"`.
- `EvalCaseOut = EvalCaseIn + {id, suite_id, content_hash, updated_at}`.
- `BaselineOut = {origin: "frozen"|"automatic"|"none", eval_run_id|null, version_id|null, frozen_by|null, frozen_at|null}`
  is resolved with the same function the run uses, so what the UI shows is what the next run will compare against.

## D. Baseline freeze (new)

| Route | Perm | Behaviour |
|---|---|---|
| `PUT /api/v1/eval-suites/{sid}/baseline` | **eval.govern** (admin) + ensure_manage | `{eval_run_id}` → `BaselineOut`. The run must be `completed`, of this suite and this workspace; otherwise 400 `input.invalid`. Audit `baseline.freeze` with `{previous_run_id, new_run_id}` |
| `DELETE /api/v1/eval-suites/{sid}/baseline` | **eval.govern** (admin) + ensure_manage | 204 (idempotent). Audit `baseline.clear` with `{previous_run_id}` (only when one existed) |

A builder or publisher gets 403 `access.denied` from `require_permission` (before any lookup, per the existing deps order).

## E. Eval runs (changed + new)

| Route | Perm | Change |
|---|---|---|
| `POST /api/v1/agents/{id}/eval-runs` | eval.run → rate_limit(eval) → spend cap (existing order) | **Body replaced**: `EvalRunIn = {version_id, suite_id}`, `extra="ignore"`. A body with `baseline`/`policy`/`cases` is accepted, and those fields are dropped unread (owner O3). Suite empty → 400. Suite > 25 cases → 400. Version not of this agent → 404. Archived → 409 `agent.archived`. Response 201 `EvalRunDetailOut` |
| `GET /api/v1/agents/{id}/eval-runs` | view + ensure_use | keyset (`limit, cursor`), newest first → `Page[EvalRunOut]` |
| `GET /api/v1/eval-runs` | view | workspace-wide, filtered to agents the caller can use → `Page[EvalRunOut]` |
| `GET /api/v1/eval-runs/{rid}` | view + ensure_use(agent) | → `EvalRunDetailOut` |

- `EvalRunOut` contains:
  - identity: `{id, agent_id, suite_id, suite_name, version_id, version_label, status, verdict, reason_code, reason}`
  - scores: `quality, quality_delta, cost_usd, cost_delta_pct, latency_ms, latency_delta_ms`
  - case counts: `compared_cases, new_cases, dropped_cases, total_cases`
  - baseline provenance: `baseline: {origin, eval_run_id, version_label} | null`
  - audit: `created_by, finished_at`
- `EvalRunDetailOut = EvalRunOut + {dimensions, failing_cases[{case_id, reason, hard}], results[{case_id, passed_deterministic, quality, in_comparison, failure_reason, cost_usd, latency_ms}], policy, judge_version, pairwise_rate}`.
  `candidate_output` is **not** returned in v1. It is evidence for the judge only, which keeps the payload small (Nice: per-case drill-down).

**Behavioural contract (tested)**:

| # | Given | Then |
|---|---|---|
| E1 | no frozen baseline, a completed run of the active version on this suite exists | `baseline.origin="automatic"`, compared to that run |
| E2 | frozen baseline R, active version moved | compared to R, `origin="frozen"` |
| E3 | no baseline at all | `verdict="low_confidence"`, `reason_code="no_baseline"`. The run is persisted, and the judge still produces rubric scores |
| E4 | baseline run lacks some cases or has edited ones | only `(case_id, content_hash)` matches are compared. `compared_cases < min_cases` → `low_confidence` / `baseline_stale` |
| E5 | spend cap exceeded | 402 before any row. **0** `eval_runs` rows |
| E6 | provider deadline mid-run | `eval_runs.status="failed"`, `reason_code="timeout"`, no `eval_results`. Per-case `runs` rows kept |
| E7 | client body includes a flattering `baseline` (and `policy`, `cases`) | 201. The verdict, deltas and persisted row are identical to the same run without those fields (SC-004) |
| E8 | other workspace | list/get/freeze → 404 / empty; isolation under RLS + composite FKs |

## F. Tokens, runs — unchanged (wired only)

`POST/GET /api/v1/agents/{id}/tokens`, `DELETE /api/v1/tokens/{id}`, `GET /api/v1/runs[/{id}]` as built (research
G18). No contract change.

## G. OpenAPI

`frontend/openapi.json` is re-emitted and `src/lib/api/schema.ts` regenerated (`npm run gen:api`). The
`openapi-emit` CI smoke must pass.
