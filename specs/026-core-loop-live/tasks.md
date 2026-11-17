---
description: "Task list — feature 026 core loop live (M12 slice 1 + eval persistence, Must tier)"
---

# Tasks: Core Loop Live — Configure → Version → Playground → Publish → cURL, with Persisted Evals

**Input**: `specs/026-core-loop-live/` — plan.md, spec.md (US1–US4, FR-001–023), research.md (G1–G25, D1–D22),
data-model.md (migration `0016_core_loop`), contracts/api.md (A–G, E1–E8), contracts/frontend-seams.md (S1–S20),
quickstart.md (§1 tests, §3 live checks L1–L24, §4 real smoke)

**Tests**: MANDATORY for the backend. Constitution §9 requires strict Red→Green→Refactor: every behaviour-changing
backend task has a Red test task before it. **Red evidence (failing for the expected reason, not import or syntax
errors) is shown to the owner and confirmed before the matching Green task starts.** Never weaken, skip, or xfail a
test to get Green.

**§9 FE exception — APPROVED 2026-11-16** (owner instruction at `/speckit-tasks`: "take recommended options where
decisions are not clear"; recorded in plan.md Constitution Check + research.md Part 3). There is no FE test harness.
FE tasks have no Red task; their evidence is `npm run lint` + `npm run build` green **plus** the named quickstart live
check(s) recorded in the Evidence section. `parseSseChunk` stays a pure function so a harness can test it later.

**Scope**: Must tier only. Explicitly **not** in 026 (leave MOCK, labelled "not available yet" in live mode, never a
fake success): duplicate, delete, unpublish, eval-policy edit, per-case drill-down, suite archive, every non-core
family (knowledge/tools/skills/channels/guardrails PUT).

**Paths**: backend = `services/` (run from `services/` with `./.venv/bin/python -m pytest …`); integration tests need
`make up` (Postgres :5433, Redis :6380). Shell alembic needs `AC_ENV=dev AC_STORAGE_PROVIDER=local`. The local `api`
container has no source mount: rebuild with `docker compose -f deploy/compose.yaml up -d --build api` before live checks.
FE = `frontend/` and always `source ~/.nvm/nvm.sh; nvm use 24` first (`;`, not `&&`). **No paid provider calls
anywhere** except the single owner-approved smoke T076 (fake transport D22 everywhere else).

**Standing 025 rules**: bindings may only be inserted in their version's own transaction. Test seeders use
`seed_version(..., tool_ids=, skill_ids=, kb_ids=)`. Cross-table refs use composite `(workspace_id, id)` FKs (RI
bypasses RLS). DB refusals map to typed 4xx through `api/db_errors.py`.

## Format: `[ID] [P?] [Story] Description`
- **[P]** = different files, no dependency on an incomplete task. **[USn]** = user story from spec.md.
- Every Red task names a `Focused:` command. Every Green task names a `Regression:` command.

---

## Phase 1: Setup

- [X] T001 Record the pre-026 baseline in this file's "Evidence" section: `./.venv/bin/python -m pytest -q --collect-only | tail -1` (expect ≈1359), `-m "not integration"` (expect 940 passed / 1 skipped / 1 held-out fail), `-m integration` (expect 413 passed / 4 skipped), `make test-integration` (LocalStack 4/4), and `AC_ENV=dev AC_STORAGE_PROVIDER=local ./.venv/bin/alembic current` (must be `0015`). Also record `grep -rn "@backend" frontend/src | wc -l` (expect 257) and the current "4 / 257" line in `docs/backend-integration-status.md`. No code changes.
- [X] T002 [P] Locate (do not change) the seams later tasks edit, and note file:line for each in Evidence: where the api binds `transport.stream_chunks`/`transport.embed` for the gateway (`services/api/resilience_wiring.py` or `services/api/routers/invoke.py`); `AgentPatch`/`AgentOut`/`VersionOut` in `services/api/schemas/agents.py`; `InvokeIn` in `services/api/schemas/invoke.py`; `EvalRunIn` + publish in `services/api/schemas/evals.py` and `services/api/routers/evals.py`; `create_version` in `services/api/routers/agents.py`; `workspace_settings.eval_policy` read site. Use codegraph (`codegraph_explore`).

---

## Phase 2: Foundational (blocks all stories)

**Purpose**: schema + ORM for every story, the audit writer, the dev-only fake transport (so no live check is a paid call), shared FE client plumbing.

### Red tests (Foundational)
- [X] T003 Red (integration): create `services/tests/test_migration_0016.py`, covering:
  - **Round-trip**: `upgrade head` → `downgrade 0015` → `upgrade head` is clean. After downgrade, no `eval_*`/`audit_logs` tables and no `agents.description/draft/status_before_archive` or `agent_versions.created_by` remain.
  - **Columns**:
    - `agents.description` is "`text NOT NULL DEFAULT ''`".
    - `agents.draft` is "`jsonb NULL`".
    - `agents.status_before_archive` is "`agent_status NULL`".
    - `agent_versions.created_by` is "`text NULL`".
  - **CHECKs**:
    - `eval_runs.status` "`CHECK IN ('completed','failed')`".
    - `verdict` "`NULL CHECK IN ('pass','fail','low_confidence')`".
    - `baseline_origin` "`NULL CHECK IN ('automatic','frozen')`".
  - **Uniques**: `UNIQUE (agent_id, name)` on `eval_suites`, `UNIQUE (eval_run_id, case_id)` on `eval_results`, and `eval_baselines.suite_id` is the PK.
  - **Cascades**:
    - eval_cases → suites is **ON DELETE CASCADE**.
    - eval_results → eval_runs is **ON DELETE CASCADE**.
    - eval_baselines → suites is CASCADE.
    - eval_runs → suites is RESTRICT.
    - eval_runs.baseline_run_id is RESTRICT.
    - eval_baselines.eval_run_id is RESTRICT.
  - **FK guards**: `eval_runs.version_id` FK `(version_id, agent_id) → agent_versions(id, agent_id)` refuses another agent's version. Composite `(workspace_id, …)` FKs refuse cross-workspace refs.
  - **Trigger** `eval_baselines_run_check` refuses a `failed` run and a run of another suite.
  - **RLS**: `ENABLE`+`FORCE` and isolation on all 6 new tables, as `ac_app` under another `app.workspace_id`.
  - **`audit_logs` grants**: `UPDATE` and `DELETE` as `ac_app` → SQLSTATE 42501.
  - **Indexes** exist:
    - `(agent_id, finished_at DESC)`
    - partial `(suite_id, version_id, finished_at DESC) WHERE status='completed'`
    - `(workspace_id, finished_at DESC)`
    - `audit_logs (workspace_id, created_at DESC)`

  Focused: `pytest -q -m integration tests/test_migration_0016.py`
- [X] T004 [P] Red: in `services/tests/test_orm_models.py` assert the ORM mirrors 0016: `Agent.description/draft/status_before_archive`, `AgentVersion.created_by`, models `EvalSuite`, `EvalCase`, `EvalRun`, `EvalResult`, `EvalBaseline`, `AuditLog` with the column names/nullability of data-model.md and composite FK targets. Focused: `pytest -q tests/test_orm_models.py -k "eval or audit or draft or created_by"`
- [X] T005 [P] Red (integration): create `services/tests/test_audit.py`. `api.audit.record(session, principal, action, target_type, target_id, meta)` inserts one row with `actor = principal subject` in the caller's transaction. A rolled-back transaction leaves 0 rows. `action` outside "`baseline.freeze, baseline.clear, agent.publish, agent.repoint, agent.archive, agent.restore`" → `ValueError`. `meta` holds "ids only". A `meta` value that is not a UUID/str-id/None → `ValueError` (no content in audit rows). The row is invisible from another workspace. Focused: `pytest -q -m integration tests/test_audit.py`
- [X] T006 [P] Red: create `services/tests/test_fake_transport.py`, covering:
  - **Streaming**: `runtime.providers.fake_transport` streams deterministic chunks for a given prompt (same input → same chunk sequence + same usage), followed by a usage chunk priced by our capability profiles.
  - **Markers in the last user message**:
    - `[[fail:transient]]` raises a retryable `TransportError` before the first chunk.
    - `[[fail:timeout]]` sleeps past the deadline via the injected clock / raises a timeout.
    - `[[fail:midstream]]` yields ≥ 1 text chunk, then raises.
    - `[[fail:provider_unavailable]]` raises a non-retryable 5xx-class error.
  - **Embeddings**: deterministic vectors of the requested dimension.
  - **Purity**: no FastAPI/redis/httpx imports (also covered by `test_runtime_purity.py`).
  - **Settings**: `AC_PROVIDER_TRANSPORT=fake` with `AC_ENV=prod` → settings construction raises. Default is `litellm`. An unknown value → raises.

  Focused: `pytest -q tests/test_fake_transport.py`

### Green (Foundational)
- [X] T007 Green: create `services/migrations/versions/0016_core_loop.py` (down_revision `0015`) per data-model.md, so that T003 passes. Every new table follows the 0008 tenant pattern: `workspace_id uuid NOT NULL REFERENCES workspaces(id)`, `ix_<t>_workspace`, `ENABLE`+`FORCE ROW LEVEL SECURITY`, policy `<t>_ws_isolation`, and grants to `ac_app`. `audit_logs` gets "`SELECT, INSERT` only (no UPDATE/DELETE)". `eval_cases.rubric` "`[{name: str, weight: float>0}]`, 1–8 dimensions" is enforced by a CHECK on `jsonb_array_length` between 1 and 8 (weight > 0 is enforced in the API). Downgrade drops the tables, then the columns. Regression: `pytest -q -m integration tests/test_migration_0016.py tests/test_migration_0015.py`
- [X] T008 Green: mirror 0016 in `services/api/models.py` so that T004 passes. Regression: `pytest -q tests/test_orm_models.py`
- [X] T009 Green: create `services/api/audit.py` (`record()` + the action allow-list), so that T005 passes. Regression: `pytest -q -m integration tests/test_audit.py`
- [X] T010 Green: create `services/runtime/providers/fake_transport.py` (pure, scripted markers, same `stream_chunks`/`embed` signatures as `transport.py`). Add `provider_transport: Literal["litellm","fake"] = "litellm"` (env `AC_PROVIDER_TRANSPORT`) to `services/api/settings.py`, with a validator that refuses `fake` when `AC_ENV=prod`. Select the transport at the binding point found in T002 (api side only, since the runtime stays pure). Add `AC_PROVIDER_TRANSPORT: ${AC_PROVIDER_TRANSPORT:-litellm}` to the `api` env in `deploy/compose.yaml`. Regression: `pytest -q tests/test_fake_transport.py tests/test_runtime_purity.py tests/test_transport.py tests/test_gateway.py`
- [X] T011 Test scaffolding (no production behaviour): extend `services/tests/_api_helpers.py` with:
  - `set_draft(agent_id, **fields)`
  - `seed_suite(agent_id, name, cases=[...])` (computes `content_hash`)
  - `seed_eval_run(agent_id, suite_id, version_id, status=, verdict=, reason_code=, finished_at=, results=[...])`
  - `freeze(suite_id, run_id, by=)`

  Each inserts through the ORM in one transaction. Smoke test: `pytest -q -m integration tests/test_migration_0016.py`.
- [X] T012 [P] FE plumbing (behaviour-neutral in mock), in `frontend/src/lib/api/client.ts`:
  - Extract one shared `parseError(res) → ApiError{status, code, message, detail, retryAfter}` (reads the 08 envelope `{"error":{code,message,detail?}}` and the `Retry-After` header).
  - Add `apiFetchWithMeta` returning `{data, etag}` (reads the `ETag` header).
  - Keep `apiFetch` signature-compatible.
  - Add no new typed calls yet (they land per story).

  Evidence: `npm run lint && npm run build` green.
- [X] T013 [P] FE: create `frontend/src/lib/api/refusals.ts`, a pure `refusalMessage(err: ApiError) → {title, body, retryable}` covering the D16 table:
  - 402 budget, with `cap_usd`/`month_to_date_usd` in the text.
  - 429, with the Retry-After seconds.
  - 403 access.
  - 404 not found / no access.
  - 502 `provider.unavailable`.
  - 503 `config_error`.
  - stream `error{timeout}`.
  - 409 `conflict` (stale ETag).
  - 409 `agent.archived`.
  - 409 `eval.suite_has_runs`.
  - The 025 integrity codes from `api/db_errors.py` (frozen binding, cross-workspace binding, missing binding with `detail.binding` = `knowledge_base|tool|skill`).

  Unknown codes fall back to the server `message`, never a generic "something went wrong" that hides it. Evidence: lint + build.

**Checkpoint**: Red evidence for T003–T006 shown to the owner → Green T007–T010 → `alembic current` = `0016`; full `-m "not integration"` + `-m integration` still at baseline + new tests.

---

## Phase 3: User Story 1 — Configure an agent and freeze a version that persists (P1) 🎯 MVP

**Goal**: description + draft persist server-side under the xmin ETag; versions are built from the draft with bindings kept (G3 fix) and a creator; re-point, archive and restore are audited.
**Independent test**: edit + save + reload persists; two-tab save → 409; create v2 → listed with label/creator/id; re-point to v1 → the playground runs v1; archive refuses, restore answers (quickstart L1–L7).

### Red tests (US1 backend)
- [X] T014 [P] [US1] Red: create `services/tests/test_drafts.py` (infra-free) for `api.drafts`:
  - `normalize()` sorts and dedupes `kb_ids/tool_ids/skill_ids` and drops default-valued `model_params`.
  - `resolve(agent, active_version)` returns the stored draft, or the active version projected onto the Draft shape when `draft IS NULL`.
  - `dirty()` is `normalized(draft) != normalized(active)`, and `false` when `draft IS NULL`.
  - Reordering ids or adding a default-valued param is not dirty.
  - `transient_version(agent, draft)` builds an unsaved `AgentVersion`-shaped object that `to_snapshot()` accepts.

  Focused: `pytest -q tests/test_drafts.py`
- [X] T015 [US1] Red (integration): create `services/tests/test_api_agents_draft.py`, covering:
  - **GET** `/api/v1/agents/{id}` returns `description, provider, model, draft, draft_dirty, restore_status` (draft resolved from the active version when NULL, `draft_dirty=false`) plus the `ETag` header. `VersionOut` has `created_by` and `is_active`.
  - **PATCH** `description` (≤ 2000 chars persists; 2001 → 422) and `draft` (full replace) with `If-Match`. Each response has a new `ETag`. A stale `If-Match` → 409 `conflict`. A missing `If-Match` → the existing refusal.
  - **Dirty tracking**: changing `system_prompt` → `draft_dirty=true`; reverting to identical content → `false`.
  - **Draft validation at save**: unknown provider / model not in the profiles / `io_contract` without `input_schema` / invalid `model_params` → 422. A draft listing a non-existent `kb_id` is **accepted** (bindings are not checked at save).
  - **Permissions**: a viewer PATCH → 403. Another workspace → 404.

  Focused: `pytest -q -m integration tests/test_api_agents_draft.py`
- [X] T016 [US1] Red (integration): in `services/tests/test_api_agents_draft.py` add lifecycle + audit cases:
  - **Archive**: PATCH `status:"archived"` from `published` stores `status_before_archive='published'`, and `restore_status` reads `"published"`.
  - **Restore**: PATCH `status:"published"` restores it and clears `status_before_archive`. The same works from `draft` and `eval_failing`.
  - **Other transitions out of `archived`** (e.g. `status:"draft"` when `restore_status` is `published`) → 400.
  - **Audit**:
    - Re-point PATCH `active_version_id` writes an `agent.repoint` row with `{from_version_id, to_version_id}`.
    - Archive writes an `agent.archive` row.
    - Restore writes an `agent.restore` row.
    - A failed PATCH writes no audit row.

  Focused: `pytest -q -m integration tests/test_api_agents_draft.py -k "archive or restore or audit or repoint"`
- [X] T017 [P] [US1] Red (integration): create `services/tests/test_api_versions_from_draft.py`, covering:
  - **Version from draft**: `POST /api/v1/agents/{id}/versions` builds the version from the resolved draft (prompt/model/params/io_contract equal the draft).
  - **Bindings kept**: tool **and** skill **and** KB bindings from the draft are present on the new version (G3 regression: a draft with `tool_ids=[t]`, `skill_ids=[s]` yields a version with both bindings).
  - **Creator**: `created_by` = the caller's subject.
  - **Missing binding**: a draft with a deleted KB → 404 `not_found` with `detail:{binding:"knowledge_base", id}`. The same shape applies for `tool` and `skill`. No version row is left behind.
  - **After create**: the list is newest first. `draft_dirty` is false once the version is made active.
  - **Permissions**: a viewer → 403.

  Focused: `pytest -q -m integration tests/test_api_versions_from_draft.py`

### Green (US1 backend)
- [X] T018 [US1] Green: create `services/api/drafts.py` (Draft pydantic model `{provider, model, model_params, system_prompt, io_contract, kb_ids, tool_ids, skill_ids}` reusing the AgentCreate validators; `normalize`, `resolve`, `dirty`, `transient_version`), so that T014 passes. Regression: `pytest -q tests/test_drafts.py tests/test_to_snapshot.py`
- [X] T019 [US1] Green: in `services/api/schemas/agents.py` + `services/api/routers/agents.py`, add the `AgentOut` enrichment, `VersionOut.created_by/is_active`, and `AgentPatch.description?: str(max 2000)` + `draft?: Draft`, all under the existing xmin `If-Match`, so that T015 passes. Regression: `pytest -q -m integration tests/test_api_agents_draft.py tests/test_api_agents*.py`
- [X] T020 [US1] Green: in `services/api/routers/agents.py`, archive stores `status_before_archive`, restore validates against it and clears it, and any other `archived →` transition → 400. `api.audit.record` runs in the same transaction for repoint/archive/restore. This makes T016 pass. Regression: `pytest -q -m integration tests/test_api_agents_draft.py tests/test_audit.py`
- [X] T021 [US1] Green: rewrite `create_version` in `services/api/routers/agents.py` to build from `drafts.resolve()`. It existence-checks every bound KB/tool/skill (→ 404 with `detail.binding`), inserts the version **and** all bindings in the same transaction (the 025 insert-window rule), and sets `created_by`. This makes T017 pass. Regression: `pytest -q -m integration tests/test_api_versions_from_draft.py tests/test_binding_integrity.py tests/test_api_integrity_errors.py tests/test_api_tools.py tests/test_api_skills.py tests/test_api_knowledge.py`

**Checkpoint (backend)**: Red evidence T014–T017 → Green T018–T021 → `-m integration` at baseline + new.

### FE (US1) — §9 exception: evidence = lint + build + live checks
- [X] T022 [US1] Contract regen: `services/.venv/bin/python scripts/emit_openapi.py` → `cd frontend && npm run gen:api`. Commit nothing. Confirm the `git diff frontend/openapi.json` shows only the api.md §A deltas. Evidence: lint + build.
- [X] T023 [US1] S1:
  - `frontend/src/lib/data.ts` gains `Agent.draftDirty`, `Agent.restoreStatus`, `Version.id`, and `Version.createdBy`.
  - `agentOutToAgent` in `frontend/src/lib/api/adapters.ts` maps `description/provider/model/draft/draft_dirty/restore_status` plus the `versions` embed (`created_by`, `is_active`).
  - `loadAgent` in `frontend/src/lib/store.ts` stores the ETag in a store-level `etags` map (D13).
  - Flip the `data.ts:108` marker to `@backend wired(026)`.

  Evidence: lint + build.
- [X] T024 [US1] S2 + S3:
  - `updateAgent` (store.ts:668) and `updateAgentConfig` (store.ts:695) get live branches: PATCH with `If-Match` from `etags` (description/name, and full `draft`), store the new ETag, and delete their `warnLocalOnly` calls.
  - A 409 `conflict` opens a "changed by someone else" dialog in `frontend/src/app/(app)/agents/[id]/page.tsx`. Reload re-fetches and re-applies the unsaved local values.
  - Show a "draft changed since active version" badge from `draftDirty`.
  - Flip both markers.

  Evidence: lint + build + L1, L2.
- [X] T025 [US1] S4: add a `createVersion(agentId, label)` live call in `store.ts` that replaces the toast-only `SaveVersionDialog` path (page :482, :1459). A 404 with `detail.binding` renders the `refusals.ts` message ("a bound knowledge base no longer exists"). Flip the page header (version create) marker. Evidence: lint + build + L3, L5.
- [X] T026 [US1] S5 + S6: `getVersions` reads the live `agent.versions` embed (page Versions :877-910, and the playground page :37 versions source). `rollbackVersion` live = PATCH `active_version_id` by **id** (not label) with `If-Match`. Flip the markers. Evidence: lint + build + L4.
- [X] T027 [US1] S7: `archiveAgent`/`restoreAgent` (store.ts:721) live = PATCH `status:"archived"` / `status:<restoreStatus>`. Archived agents render a read-only banner. In live mode, `duplicateAgent` (store.ts:809), `deleteAgent` (store.ts:781) and `unpublishAgent` show "not available yet" (disabled + tooltip), never a fake success. Evidence: lint + build + L7 (console half).
- [X] T028 [US1] FR-020 gating: every US1 control in `agents/[id]/page.tsx` gates on `useAuth().can` (server `backendRole`). The dev role override can hide controls but never unlocks a server action. A server 403 renders the `refusals.ts` 403 message, and each live load shows the `LiveStatus` loading/ready/error strip with no silent mock fallback (FR-019). Evidence: lint + build + L6.
- [X] T029 [US1] Record L1–L7 (L7 console half; the published half is re-checked in US3) in Evidence, with the `AC_PROVIDER_TRANSPORT=fake make up` stack and `NEXT_PUBLIC_API_MODE=live npm run dev`.

**Checkpoint (US1 = MVP)**: backend green + lint/build green + L1–L7 recorded → demo-able increment.

---

## Phase 4: User Story 2 — Try the agent in the playground and see a real streamed answer (P1)

**Goal**: an incremental stream with citations and real cost/latency; 7 distinct refusals; an interrupted turn keeps its partial answer; draft vs version targeting labelled.
**Independent test**: a playground send streams token by token, turn cost equals the Runs row, and each refusal is distinct (L8–L12).

### Red tests (US2 backend)
- [x] T030 [US2] Red (integration): create `services/tests/test_invoke_draft_target.py`, covering:
  - **Draft target**: console `POST /api/v1/agents/{id}/invoke` with `target:"draft"` runs the draft's prompt through `runtime.invoke_result`. The run row has `version_id IS NULL`, and `run_start.version == "draft"`.
  - **Version target**: `target:"version"` without `version_id` → 400. With another agent's `version_id` → 404. With `version_id` it runs that version, and `run_start.version` is its label.
  - **Default**: `target` defaults to `"active"`.
  - **Published plane**: `POST /v1/agents/{id}/invoke` with a `target` field → it is ignored/refused per the schema (never runs the draft).
  - **Missing binding**: a draft with a deleted KB at run time → 404 with `detail.binding`.

  Focused: `pytest -q -m integration tests/test_invoke_draft_target.py`
- [x] T031 [P] [US2] Red: in `services/tests/test_invoke_parity.py`, add the draft parity check: a draft run and a saved version with identical content produce identical output, `cost_usd` and RunRecord fields (except `version_id`) under the fake transport (P6, one runtime path). Focused: `pytest -q tests/test_invoke_parity.py -k draft`
- [x] T032 [P] [US2] Red (integration): create `services/tests/test_api_archived_invoke.py`. With an archived agent, the console invoke → 409 `agent.archived` and the published invoke (valid token) → 404 `not_found`. After a restore, both answer. Focused: `pytest -q -m integration tests/test_api_archived_invoke.py`

### Green (US2 backend)
- [x] T033 [US2] Green: in `services/api/schemas/invoke.py`, add `InvokeIn.target: Literal["active","draft","version"] = "active"` (`version_id` required iff `target="version"`, console schema only). In `services/api/routers/invoke.py` `_run_invoke`, the draft path builds `drafts.transient_version()` → `to_snapshot()` → the same `invoke_result`, and records the run with `version_id=None`. This makes T030 + T031 pass. Regression: `pytest -q -m integration tests/test_invoke_draft_target.py tests/test_api_invoke*.py && pytest -q tests/test_invoke_parity.py tests/test_runtime_purity.py`
- [x] T034 [US2] Green: add `AgentArchived` (409 `agent.archived`) in `services/api/errors.py`. In `services/api/routers/invoke.py`, an archived agent raises `AgentArchived` on the console plane and `not_found` on the published plane, checked before any provider work or spend reservation. This makes T032 pass. Regression: `pytest -q -m integration tests/test_api_archived_invoke.py tests/test_invoke_draft_target.py`

### FE (US2) — §9 exception
- [x] T035 [US2] Contract regen (as T022) for the §B delta. Then create `frontend/src/lib/api/stream.ts`:
  - `streamInvoke({agentId, workspace, body, signal, onFrame})` using fetch + `ReadableStream`, with dev-auth headers via the `client.ts` helper and no 15 s timeout.
  - A pure `parseSseChunk(buffer) → {frames, rest}` that handles split chunks, multi-line `data:` and comments.
  - A non-2xx response → `parseError` → reject with `ApiError`.
  - A stream that ends without `done` → resolves `{interrupted:true}`.
  - `AbortController` cancel.

  Evidence: lint + build.
- [x] T036 [US2] S9: in `frontend/src/components/playground.tsx`, add a turn state machine `streaming → complete | incomplete (interrupted/error, partial kept, Retry) | refused (refusals.ts message, no partial)` driven by `streamInvoke` for live agents. Mock agents keep today's behaviour (FR-021). Flip the `playground/page.tsx:3-7` invoke marker. Evidence: lint + build + L8, L10 (all 7 refusals, via `[[fail:*]]` markers / `seed_limits` / key removal / breaker per quickstart L10), L11.
- [x] T037 [US2] S10: in `frontend/src/app/(app)/playground/page.tsx` :30-53, add a draft / active / specific-version selector → `target` (+ `version_id`). Each turn shows a label of which ran ("draft" or the version label). Flip the versions marker. Evidence: lint + build + L12.
- [x] T038 [US2] S11: render the `citations` frames (document names) and the `usage` frame (cost/latency). Remove the hardcoded `1.8s / 9,120 / $0.034` (playground.tsx :88) and the `store.ts:1197-1206` addRun NOTE path for live. Flip the marker. Evidence: lint + build + L8 (turn cost/latency = Runs page row), L9.
- [x] T039 [US2] Record L8–L12 in Evidence.

**Checkpoint (US2)**: backend green + L8–L12 recorded (L10 must show 7/7 distinct messages).

---

## Phase 5: User Story 3 — Publish the agent and call it from outside (P1)

**Goal**: publish shows a server advisory (never blocking) and is audited; tokens list/mint/revoke live; the copyable cURL works with the token never interpolated.
**Independent test**: publish → advisory shown; mint → cURL works; revoke → stream cut ≤ ~2 s, next call 401 (L13–L16).

### Red tests (US3 backend)
- [ ] T040 [US3] Red (integration): create `services/tests/test_api_publish_advisory.py`, covering:
  - **Advisory when a run exists**: `POST /api/v1/agents/{id}/publish` returns `advisory:{verdict, reason_code, eval_run_id, suite_id}` from the **latest completed** eval run of the version being published, seeded with `seed_eval_run`. A newer `failed` run is ignored, and a completed run of a different version is ignored.
  - **No run**: no completed run → `{verdict:"low_confidence", reason_code:"no_eval_run", eval_run_id:null, suite_id:null}`.
  - **Never blocks**: a `fail` advisory still publishes (status `published`).
  - **Audit**: an `agent.publish` row is written in the same transaction.
  - **Permissions**: a builder → 403.

  Focused: `pytest -q -m integration tests/test_api_publish_advisory.py`

### Green (US3 backend)
- [ ] T041 [US3] Green: in `services/api/routers/evals.py` (publish) + `services/api/schemas/evals.py`, add the advisory query over `eval_runs` (`status='completed'`, this version, newest `finished_at`) and `audit.record("agent.publish", …)`. This makes T040 pass. Regression: `pytest -q -m integration tests/test_api_publish_advisory.py tests/test_api_evals_publish.py`

### FE (US3) — §9 exception
- [ ] T042 [US3] Contract regen (as T022). S8: `publishAgent` (store.ts:721/724) live = POST publish and render `advisory` (verdict chip + reason + link to the run when present). For live agents, drop the client-side gate checks (the server is advisory-only). Flip the marker. Evidence: lint + build + L13.
- [ ] T043 [US3] S12–S14 in `store.ts` (:1147, :1152) + `adapters.ts` `TokenOut`:
  - `loadTokens(agentId)` / `useTokens` live: masked prefix, expiry, last used.
  - `mintToken` shows the raw secret **once** in `CreateTokenDialog` (:1570) and never stores it in the store or localStorage.
  - `revokeToken` = DELETE `/api/v1/tokens/{id}`.
  - Flip the markers.

  Evidence: lint + build + L14, L15.
- [ ] T044 [US3] S15 (D17): the Publish tab cURL snippet (page :1294-1299) uses `${NEXT_PUBLIC_PUBLIC_API_BASE || window.location.origin}/v1/agents/{id}/invoke`, `-H "Authorization: Bearer $AC_TOKEN"` (the token is **never** interpolated, even right after mint), and a body built from the active version's `io_contract` example. Flip the page :88 marker. Evidence: lint + build + L14.
- [ ] T045 [US3] FR-020: publish + token controls gate on `useAuth().can("agent.publish")` (server role), so a builder sees them unavailable. Record L13–L16 + the L7 published half (archived → cURL 404) in Evidence.

**Checkpoint (US3)**: core loop rows 1–3 live end-to-end on the fake transport.

---

## Phase 6: User Story 4 — Evaluate a version against a trusted baseline and keep the evidence (P2) — backend

**Goal**: suites/cases stored server-side; eval runs persisted with a server-resolved baseline (frozen > automatic > none); the client body cannot influence the verdict; admin-only audited freeze/clear.
**Independent test**: a suite of 3 cases runs on v2 vs v1, survives reload, and a flattering client `baseline` changes nothing (E1–E8).
**Parallelism**: may start right after Phase 2 (different files), except T055/T056, which edit `routers/evals.py` after T041.

### Red tests (US4 backend)
- [ ] T046 [P] [US4] Red: create `services/tests/test_baseline_resolution.py` (infra-free, over plain row objects) for `api.baseline`, covering:
  - **Resolution order**: `resolve_baseline(frozen, active_version_id, completed_runs)` returns the frozen run (`origin="frozen"`), else the latest completed run of `(suite, active_version_id)` (`origin="automatic"`), else `origin="none"`.
  - **Self-comparison**: when the candidate is the active version and no other run exists, the result is `none` (it never compares a run to itself).
  - **Case diff**: `case_diff(candidate_cases, baseline_results)` matches on `(case_id, content_hash)` and returns `compared/new/dropped/total`. An edited case counts as new + dropped.
  - **Reason codes**:
    - `compared < min_cases` → `low_confidence` / `baseline_stale`.
    - No baseline → `low_confidence` / `no_baseline`.
    - Judge unavailable → `low_confidence` / `judge_unavailable`.
    - Pass/fail map to `ok | quality_drift | cost_drift | hard_fail | below_min_cases`.
    - All codes come from "`ok, quality_drift, cost_drift, hard_fail, below_min_cases, no_baseline, baseline_stale, judge_unavailable`".
  - **Reason text**: `reason` comes from fixed templates (no provider text).

  Focused: `pytest -q tests/test_baseline_resolution.py`
- [ ] T047 [P] [US4] Red (integration): create `services/tests/test_api_eval_suites.py` for the contracts/api.md §C routes, covering:
  - **Suites**: create/list/get/rename/delete. `name` is "1–80 chars" (0 or 81 → 422), and a duplicate name for the same agent → 409 `conflict`.
  - **Delete with runs**: deleting a suite that has runs → 409 `eval.suite_has_runs`. A suite without runs deletes and cascades its cases.
  - **Case input** (`EvalCaseIn`, `extra="forbid"`):
    - `rubric` with 0 or 9 dimensions → 422.
    - Weight ≤ 0 → 422.
    - An unknown field → 422.
  - **Case writes**: `content_hash` is server-computed (a client-sent value is refused by `forbid`). It changes when input/rubric/required_fields/expected change, and is stable for an identical PUT.
  - **List view**: `EvalSuiteOut.case_count` and `baseline` (resolved by the same `api.baseline` function).
  - **Permissions**: a builder can author (`eval.author`), a viewer → 403. Another workspace → 404.

  Focused: `pytest -q -m integration tests/test_api_eval_suites.py`
- [ ] T048 [US4] Red (integration): create `services/tests/test_api_eval_runs_persisted.py` (fake transport + stub judge per `_evals_helpers.py`), covering:
  - **E1 (automatic baseline)**: v1 active with a completed run on the suite; a run on v2 → `baseline.origin="automatic"` compared to v1's run.
  - **E3 (no baseline)**: no baseline → `verdict="low_confidence"`, `reason_code="no_baseline"`. The run + results are persisted with rubric scores.
  - **E5 (spend cap)**: spend cap exceeded → 402 and **0** `eval_runs` rows.
  - **E6 (deadline)**: a deadline mid-run (`[[fail:timeout]]` case) → `status="failed"`, `reason_code="timeout"`, `verdict NULL`, no `eval_results`. Per-case `runs` rows are kept.
  - **E7 (client body ignored)**: a body with a flattering `baseline` + `policy` + `cases` → 201, and the verdict/deltas/persisted row are equal (excluding id/timestamps) to the same run without them.
  - **Request validation**:
    - Missing `suite_id` → 422.
    - An empty suite → 400.
    - 26 cases → 400.
    - Another agent's `version_id` → 404.
    - An archived agent → 409 `agent.archived`.
  - **Policy evidence**: the policy comes from `workspace_settings.eval_policy` and is stored in `eval_runs.policy`. `judge_version` and `created_by` are set.
  - **History**:
    - `GET /api/v1/agents/{id}/eval-runs` is newest first with keyset `limit/cursor`.
    - `GET /api/v1/eval-runs` is workspace-wide, filtered to usable agents.
    - `GET /api/v1/eval-runs/{rid}` returns `EvalRunDetailOut` without `candidate_output`.
  - **E8 (isolation)**: another workspace gets 404 / empty.

  Focused: `pytest -q -m integration tests/test_api_eval_runs_persisted.py`
- [ ] T049 [P] [US4] Red (integration): create `services/tests/test_api_baseline_freeze.py`, covering:
  - **Freeze**: an admin `PUT /api/v1/eval-suites/{sid}/baseline {eval_run_id}` → `BaselineOut{origin:"frozen", frozen_by, frozen_at}`. A re-freeze replaces it and audits `baseline.freeze` with `{previous_run_id, new_run_id}`.
  - **Clear**: `DELETE` → 204 and is idempotent. It audits `baseline.clear` with `{previous_run_id}` only when one existed.
  - **E2 (frozen wins)**: after a re-point, the next run compares to the frozen run (`origin="frozen"`).
  - **Permissions**: publisher → 403 `access.denied`; builder → 403. Both are refused before any lookup.
  - **Invalid run**: a `failed` run / another suite's run / another workspace's run → 400 `input.invalid`. The trigger `eval_baselines_run_check` backs this up when the route check is bypassed.

  Focused: `pytest -q -m integration tests/test_api_baseline_freeze.py`
- [ ] T050 [US4] Red: migrate existing callers of the old eval-run body (`cases/baseline/policy` inline) to `{version_id, suite_id}` + seeded suites. Find them with `grep -rln "eval-runs\|EvalRunIn" services/tests` plus `grep -rln '"baseline"' services/tests` (at /speckit-tasks time the route grep hit `test_api_evals_publish.py`, `test_api_contracts.py`, `test_api_spend_cap.py`, `test_api_access_policy.py`, `test_api_correlation.py`, `test_api_call_log.py`, `test_api_provider_key_resolve.py`, `test_api_retrieval_invoke.py`; re-run the grep, since only files that POST an inline body need changing). Keep every assertion's intent. Record which now fail and why (expected: the route still reads the old body). Focused: `pytest -q -m integration <those files>`
- [ ] T051 [P] [US4] Red: in `services/tests/test_api_archived_invoke.py`, add a case: an eval-run on an archived agent → 409 `agent.archived`. Focused: `pytest -q -m integration tests/test_api_archived_invoke.py -k eval`

### Green (US4 backend)
- [ ] T052 [US4] Green: create `services/api/baseline.py` (`resolve_baseline`, `case_diff`, reason-code + fixed reason templates; pure, with no session or FastAPI imports in its core functions), so that T046 passes. Regression: `pytest -q tests/test_baseline_resolution.py tests/test_evals_verdict.py`
- [ ] T053 [US4] Green: create `services/api/routers/eval_suites.py` + suite/case schemas in `services/api/schemas/evals.py` (`EvalCaseIn` `extra="forbid"`; `content_hash` = sha256 of the canonical JSON — sorted keys, no whitespace — of `(input, rubric, required_fields, expected)`). Add `EvalSuiteHasRuns` (409 `eval.suite_has_runs`) in `services/api/errors.py`, and register the router in `services/api/main.py`. Deps order: `require_permission` → `ensure_use`/`ensure_manage`. This makes T047 pass. Regression: `pytest -q -m integration tests/test_api_eval_suites.py && pytest -q tests/test_rbac*.py`
- [ ] T054 [US4] Green: create `services/api/eval_store.py`, `persist_completed(session, run, results)` / `persist_failed(session, run)`, which insert the `eval_runs` row (+ `eval_results`) in **one** transaction, never update after insert, and set `in_comparison` from `case_diff`. Covered by T048. Regression: run it with T055.
- [ ] T055 [US4] Green: in `services/api/routers/evals.py` + `schemas/evals.py`, `POST /api/v1/agents/{id}/eval-runs` becomes:
  1. Parse `EvalRunIn={version_id, suite_id}` with `extra="ignore"`. Unknown fields are dropped unread, and there is no log of their values.
  2. Run the existing deps order `eval.run → rate_limit(eval) → spend cap` (402 before any row).
  3. Archived → 409.
  4. Load the suite's cases (0 → 400, > 25 → 400).
  5. Resolve the baseline with `api.baseline` + the baseline run's `eval_results`.
  6. Read the policy from `workspace_settings.eval_policy`.
  7. Run the cases through the existing `evals/runner.py` → `invoke_result` (one runtime path).
  8. Persist via `eval_store`: completed, or failed on deadline/error.
  9. Return `EvalRunDetailOut`.

  This makes T048 (E1, E3, E5–E8) + T050 + T051 pass. Regression: `pytest -q -m integration tests/test_api_eval_runs_persisted.py tests/test_api_evals_publish.py tests/test_api_drift.py tests/test_api_archived_invoke.py && pytest -q tests/test_evals_*.py tests/test_invoke_parity.py`
- [ ] T056 [US4] Green: history routes in `services/api/routers/evals.py` (`GET /api/v1/agents/{id}/eval-runs`, `GET /api/v1/eval-runs`, `GET /api/v1/eval-runs/{rid}`), keyset on `(finished_at DESC, id)` via `api/pagination.py`, `ensure_use` filtering. This completes T048. Regression: `pytest -q -m integration tests/test_api_eval_runs_persisted.py`
- [ ] T057 [US4] Green: baseline `PUT`/`DELETE` in `services/api/routers/eval_suites.py`, gated by `require_permission("eval.govern")` (admin only, owner O2) + `ensure_manage`. Freeze = upsert `eval_baselines`; clear = delete. Each writes `audit.record` in the same transaction. A trigger refusal maps through `api/db_errors.py` → 400 `input.invalid`. This makes T049 pass. Regression: `pytest -q -m integration tests/test_api_baseline_freeze.py tests/test_audit.py tests/test_migration_0016.py`

**Checkpoint (US4 backend)**: Red evidence T046–T051 → Green T052–T057 → full `-m integration` green; T040 re-run (advisory now reads real runs).

---

## Phase 7: User Story 4 — FE (P2) — §9 exception

- [ ] T058 [US4] Contract regen (as T022) for §C–§E. In `frontend/src/lib/data.ts` + `adapters.ts`, add the EvalSuite/EvalCase/EvalRun/Baseline mappings: `EvalRun.baseline {origin, evalRunId, versionLabel}`, suite `baseline`, case `contentHash`. Evidence: lint + build.
- [ ] T059 [US4] S16: `loadSuites(agentId)`, `addSuite`, `renameSuite`, `deleteSuite` (store.ts :569, :578, :582) live in SuiteAuthoring (page :1006-1117). A 409 `eval.suite_has_runs` renders its refusal message. Flip the markers. Evidence: lint + build + L17.
- [ ] T060 [US4] S17: `upsertCase` / `deleteCase` (store.ts :587, :599) live in `CaseEditorDialog` (:1166). In live mode only the engine-supported assertions are enabled: rubric dimensions (1–8, weight > 0) + `required_fields`. The other `AssertionKind`s (`schema_valid`, `regex`, `exact`, `tool_called`, `no_refusal`, `has_citation`) are disabled with "not available yet". Flip the markers. Evidence: lint + build + L17, L21.
- [ ] T061 [US4] S18: `runEval` (store.ts:1105) and `evaluations/page.tsx:37-40` live = POST `{version_id, suite_id}` only. Live mode has **no** baseline picker in `RunEvalDialog` (:1404). The result shows the verdict, reason, `compared X of Y cases`, deltas and failing cases. Flip the markers. Evidence: lint + build + L18, L19, L21.
- [ ] T062 [US4] S19: `loadEvalRuns(agentId)` (Evals tab :951) and workspace `loadEvalRuns()` (evaluations page, store.ts:1140) list from the history routes, newest first, with the baseline provenance line (`baseline: automatic · run …` / `frozen by … · run …` / none). Flip the markers. Evidence: lint + build + L18–L20, L23.
- [ ] T063 [US4] S20: `freezeBaseline(suiteId, runId)` / `clearBaseline(suiteId)` replace the toast `reFreezeBaseline` (store.ts:556) in the Evals Baseline card (:959). The GateButton stays `eval.govern` (admin), so a publisher or builder sees it disabled. A server 403 is surfaced. Flip the marker. Evidence: lint + build + L20.
- [ ] T064 [US4] Record L17–L23 in Evidence. For L22, use `curl` with a flattering `baseline` field and compare against the run without it.

---

## Phase 8: Polish & DoD

- [ ] T065 Final contract regen: `services/.venv/bin/python scripts/emit_openapi.py`, `cd frontend && npm run gen:api && npm run lint && npm run build`. `git diff frontend/openapi.json` must equal the contracts/api.md deltas only (CI `openapi-emit` smoke).
- [ ] T066 [P] Full backend regression, recorded in Evidence:
  - `-m "not integration"` ≥ baseline + new, with only the held-out gate allowed to fail.
  - `-m integration` ≥ baseline + new, with 0 failed.
  - `tests/test_runtime_purity.py` green.
  - `make test-integration` LocalStack 4/4.
  - `--collect-only` count.
- [ ] T067 [P] CI-safety check: every new test that touches Postgres carries `@pytest.mark.integration` (`grep -L "pytestmark\|mark.integration"` over the new `test_api_*`/`test_migration_0016`/`test_audit` files returns nothing). `test_drafts.py`, `test_baseline_resolution.py` and `test_fake_transport.py` pass with no DB.
- [ ] T068 L24: unset `NEXT_PUBLIC_API_MODE`, click through every screen touched by L1–L23, and confirm the mock behaviour is unchanged (FR-021). Record it in Evidence.
- [ ] T069 Pin the exact seam list (SC-002): `grep -rn "@backend wired(026)" frontend/src`. List each flipped marker (file:line → S#) in Evidence. A seam counts only with build green **and** its live check recorded. Compute the new count to replace "4 / 257".
- [ ] T070 [P] Security pass on the diff:
  - No token/secret is logged or stored client-side (`grep` for `localStorage`/`console.log` near the mint code).
  - The cURL snippet never interpolates a token.
  - Eval case content is never logged (data classification: customer content).
  - `audit_logs.meta` holds ids only.
  - `AC_PROVIDER_TRANSPORT=fake` is refused under `AC_ENV=prod` (T006).
  - gitleaks-clean (`secret-scan` job equivalent, if available locally).
- [ ] T071 Reconcile `docs/backend-integration-status.md`: the counter on lines 5/12/105 goes from "4 / 257" to the T069 count, the agents/versions/playground/publish/tokens/evals family rows go MOCK→REAL, and add 2 new rows (eval suites/cases, baseline freeze).
- [ ] T072 [P] Reconcile the design docs to as-built:
  - `docs/backend/05-eval-drift.md`: baseline per suite, frozen > automatic > none, (case_id, content_hash) diff, 25-case cap, client fields ignored.
  - `docs/backend/08-api-surface.md` (or the API section that owns these routes): §A–§E deltas.
  - `docs/backend/01-data-model.md`: a 0016 as-built note.
- [ ] T073 [P] Update `docs/implementation-plan.md`: the M12 slice-1 + eval-persistence row → done, with evidence links. Close the audit gap-5 "eval persistence" item. Keep F7 reservation / durable idempotency / provider fallback listed as open.
- [ ] T074 [P] Update `docs/scope.md` (decision-log row for 026: server-held baseline, admin-only freeze, client fields ignored, dev-only fake transport) and `CLAUDE.md` "Current state" (026 status, live seam count, `AC_PROVIDER_TRANSPORT`, 16 migrations).
- [ ] T075 Update memory `feature-026-core-loop-live.md` (+ `MEMORY.md` pointer): status, the committed/uncommitted state, and anything still open.
- [ ] T076 Real-provider smoke — **owner-gated, once** (quickstart §4, owner O5). The owner captures the provider key in a dedicated dev workspace; that workspace's cap is `$0.05`; restart the api with `AC_PROVIDER_TRANSPORT=litellm`. The steps:
  1. **Preflight**: (max input tokens + `max_tokens`=256) × the `claude-haiku-5-5` profile price must be ≤ $0.05, otherwise **stop** with no call.
  2. **Calls**: one playground stream (synthetic prompt) + one cURL call with a minted token. No eval.
  3. **Evidence**: record both run ids, the `usage` frames, and the sum of `cost_usd` (must be ≤ $0.05 and ≤ the preflight).
  4. **Cleanup**: revoke the token; the owner may delete the key.

  If the owner declines or no key is available, mark this task **SKIPPED (owner)** with the reason. Never fake it.
- [ ] T077 Final Evidence summary: what was verified (tests, build, Lx, smoke) and what was **not** (quickstart §5: real provider beyond the 2-call smoke, FE automated tests, judge calibration, multi-replica). Nothing is committed unless the owner asks.

---

## Dependencies & Execution Order

```text
Phase 1 Setup (T001–T002)
        │
Phase 2 Foundational (T003–T013)  ── migration 0016 · ORM · audit · fake transport · FE client/refusals
        │
        ├──────────────────────────────┬───────────────────────────────┐
        ▼                              ▼                               ▼
US1 backend (T014–T021)         US4 backend (T046–T054, T057)   (FE T012/T013 done)
        │                              │
US1 FE (T022–T029)  🎯 MVP              │
        │                              │
US2 (T030–T039)  needs drafts (T018)   │
        │                              │
US3 (T040–T045)  T041 edits evals.py ──┴──▶ T055/T056 (evals.py, after T041)
        │                                          │
        └──────────────▶ US4 FE (T058–T064) ◀──────┘
                                   │
                         Polish (T065–T077)
```

- **Story dependencies**:
  - US1 is first (MVP).
  - US2 needs `api/drafts.py` (T018) for `target=draft`.
  - US3's backend is independent of US1/US2, but its FE lives on the same agent page, so it is sequenced after US1 FE.
  - US4's backend depends only on Phase 2, except T055/T056, which edit `routers/evals.py` after T041.
  - US4 FE depends on US4 backend + T058 regen.
- **Within a story**: Red → owner sees Red evidence → Green → FE (regen first) → live checks recorded.
- **Same-file serialization**:
  - `routers/agents.py`: T019 → T020 → T021.
  - `routers/evals.py`: T041 → T055 → T056.
  - `routers/eval_suites.py`: T053 → T057.
  - `test_api_agents_draft.py`: T015 → T016.
  - `test_api_archived_invoke.py`: T032 → T051.
  - FE `store.ts` and `agents/[id]/page.tsx`: all FE tasks run in order.

## Parallel Opportunities

- **Phase 2**: T004, T005, T006 [P] alongside T003. T012 and T013 [P] (FE files) can run any time in Phase 2.
- **US1 Red**: T014 and T017 [P] with T015.
- **US2 Red**: T031 and T032 [P] with T030.
- **US4 Red**: T046, T047, T049, T051 [P]. The whole US4 backend (T046–T054, T057) can run in parallel with Phases 3–5 by a second worker.
- **Polish**: T066, T067, T070, T072, T073, T074 [P].

```text
# Example: after Phase 2, two workers
worker A: T014 → T015 → T016 → T017 → T018 → T019 → T020 → T021 → T022 … (US1 → US2 → US3)
worker B: T046 ∥ T047 ∥ T049 → T048 → T050 → T052 → T053 → T054 → T057 → (wait T041) → T055 → T056
```

## Implementation Strategy

1. **MVP = Phase 1 + Phase 2 + US1 (T001–T029)**. A live agent can be edited, saved (ETag-safe), versioned with bindings intact, re-pointed, archived and restored, and all of it is audited. Stop and demo here.
2. **+US2**: the playground streams on the fake transport, with 7 distinct refusals and draft runs. Core-loop rows 1–2 are live.
3. **+US3**: publish advisory + tokens + cURL. Core loop rows 1–4 are live (the release-plan v1 blocker for M12 slice 1).
4. **+US4**: persisted evals with a server-held baseline. This closes audit gap 5 and makes the publish advisory meaningful.
5. **Polish**: regen, regression, the seam count, the docs DoD, and the owner-gated real smoke.

Each increment is shippable on its own; US4 can be dropped to a follow-up without breaking US1–US3 (the advisory then reports `no_eval_run`).

## Evidence (filled during implementation)

| Task | Command / check | Result |
|---|---|---|
| T001 | baseline (2026-11-16, pre-026) | `--collect-only` **1359**; `-m "not integration"` **940 passed / 1 skipped / 1 failed** (held-out `test_heldout_gate_sc004`, known); `-m integration` **413 passed / 4 skipped**; `make test-integration` LocalStack **4/4**; `alembic current` **0015 (head)**; `@backend` markers **257**; status doc "4 / 257" at lines 12 + 105 |
| T002 | seams | **Transport binding**: no injection point exists — adapters call the module attribute `transport.stream_chunks` (`runtime/providers/_common.py:230`) and the gateway calls `transport.embed` (`runtime/providers/gateway.py:264`); tests monkeypatch the module. ⇒ T010 binds the fake by assigning those attributes once at app startup (`api/main.py` `create_app`/`_lifespan`, :26/:43), api side only. `AgentCreate/AgentPatch/VersionCreate/VersionOut/AgentOut` `api/schemas/agents.py:19/29/48/56/72`; `_version_out`/`_agent_out` `api/routers/agents.py:52/59`; `patch_agent` :205 (xmin `If-Match`, stale → `ConflictStale` **409 `conflict.stale`**, not `conflict`); `create_version` :338 (clones latest; `tool_bindings`/`skill_bindings` set to `[]` at :384 = G3); `_etag` :93. `InvokeIn` `api/schemas/invoke.py`; `_run_invoke` `api/routers/invoke.py:72`, console :163, published :178. `eval_policy` read sites: `api/models.py:430` (`WorkspaceSettings.eval_policy`), `api/routers/workspaces.py:60/144` (no eval-run read yet). Agents have **no** `(workspace_id, id)` unique → 0016 must add one as the composite FK target |
| T003 | Red `pytest -q -m integration tests/test_migration_0016.py` | **16 failed / 0 passed** — every test fails on the missing revision/tables (`'0015' == '0016'`, `relation "eval_suites" does not exist`, RLS `None`), i.e. the expected reason |
| T004 | Red `pytest -q tests/test_orm_models.py -k "eval or audit or draft or created_by or 026"` | **8 failed** — `api.models.EvalSuite/EvalCase/EvalRun/EvalResult/EvalBaseline/AuditLog is missing`, `agents` lacks `description/draft/status_before_archive`, `KeyError 'eval_suites'` |
| T005 | Red `pytest -q -m integration tests/test_audit.py` | **20 failed** — `api/audit.py` is a signature-only stub (so the Red is not an import error): refusal cases fail with `NotImplementedError` instead of `ValueError`; write/read cases fail on `relation "audit_logs" does not exist` (needs T007) |
| T006 | Red `pytest -q tests/test_fake_transport.py` | **15 failed / 1 passed** — `runtime/providers/fake_transport.py` signature-only stub → `NotImplementedError`; settings cases fail on missing `provider_transport` / no prod refusal. The 1 pass is the purity guard (trivially true for a stub) |
| T007 | `pytest -q -m integration tests/test_migration_0016.py tests/test_migration_0015.py` | **20 passed**. Surprise fixed: migration 0003 `ALTER DEFAULT PRIVILEGES` grants ac_app full CRUD on every new table, so a bare `GRANT SELECT, INSERT` restricted nothing — 0016 now explicitly `REVOKE UPDATE, DELETE, TRUNCATE` on `eval_runs`/`eval_results`/`audit_logs` (append-only proven by test). **Pre-existing finding (out of 026 scope, owner to triage):** same cause leaves ac_app with UPDATE/DELETE on `api_tokens` and `provider_keys`, contradicting the 0007/0008 "no DELETE grant" design |
| T008 | `pytest -q tests/test_orm_models.py` | **11 passed** (Agent/AgentVersion columns + `uq_agents_ws_id`; EvalSuite/EvalCase/EvalRun/EvalResult/EvalBaseline/AuditLog with constraint names matching 0016) |
| T009 | `pytest -q -m integration tests/test_audit.py` | **20 passed** (closed action allow-list; meta = UUID / id-string / None only; inserts in caller's txn; RLS) |
| T010 | `pytest -q tests/test_fake_transport.py tests/test_runtime_purity.py tests/test_transport.py tests/test_gateway.py` | **70 passed**. Bound in `api/main.py::_bind_provider_transport` (assigns `transport.stream_chunks/embed` at `create_app`); prod refusal validator in settings; compose `AC_PROVIDER_TRANSPORT` added. Test fix (my T006 defect, not impl): cost assertion compared raw `compute_cost` but the gateway quantizes to 6 dp → assert vs `quantize_usd(compute_cost(...))` |
| T011 | `pytest -q -m integration tests/test_migration_0016.py` | **17 passed** incl. new `test_026_seeders_smoke` (set_draft merge, seed_suite+content_hash, seed_eval_run ok/failed, freeze + trigger refusal of a failed run). Helpers take `workspace_id` first (house convention). `case_content_hash` duplicated in helper — TODO swap to the production hash in US2 |
| Phase 2 checkpoint | full suites | `-m "not integration"` **956 passed / 1 skipped / 1 failed** (held-out `test_heldout_gate_sc004`, known at baseline); `-m integration` **458 passed / 4 skipped**; `AC_ENV=dev alembic current` **0016 (head)**. Red gates T003–T006 self-confirmed under owner's batch waiver — **owner review pending** |
| T014 | Red `pytest -q tests/test_drafts.py` | **21 failed** vs signature-only stub (`NotImplementedError` / no validation) — behavioural, not import. Self-confirmed under batch waiver — **owner review pending** |
| T015+T016 | Red `pytest -q -m integration tests/test_api_agents_draft.py` | **17 failed / 5 passed**. Failures = missing enrichment (`KeyError: restore_status`/`draft`), no 422 on bad drafts, no lifecycle/audit. The 5 passing are deliberate guards of existing behaviour (stale → **409 `conflict.stale`** per owner decision, missing If-Match → 400, viewer 403 / foreign 404, failed PATCH and plain edit write no audit). **Owner review pending** |
| T017 | Red `pytest -q -m integration tests/test_api_versions_from_draft.py` | **8 failed / 2 passed**. Incl. the G3 bug reproduced: `[] == [tool_id]` (tool binding silently dropped). Guards passing: NULL-draft clone hash, viewer 403. **Owner review pending** |
| T018 | `pytest -q tests/test_drafts.py tests/test_to_snapshot.py` | **26 passed**. `model_params` allow-list {temperature, top_p, max_tokens≤profile max_output_tokens} — params flow into transport kwargs, so unknown keys (`api_key`, …) are refused. "Default-valued" = unset (None); no invented provider defaults |
| T019–T021 | `pytest -q -m integration` (agents_draft, versions_from_draft, api_agents, api_bindings, binding_integrity, integrity_errors, tools, skills, knowledge, audit) | **136 passed**. **Two surprises, both resolved — owner to note:** (1) **P5 leak caught by the existing 024 H2 test**: resolved `draft.kb_ids` exposed a restricted KB id to a builder. Fix: draft ids the caller may not USE are omitted + counted in `draft.restricted_bindings`; a draft PATCH re-merges hidden ids so a caller can't strip a binding they can't see (new test). (2) **G2 semantics change**: a NULL draft now clones the **active** version (D2), not the latest; `test_g2_omitted_bindings_are_copied_with_the_same_hash` updated to re-point first — G2's "never drop knowledge" guarantee is unchanged. Also: versions now listed newest-first; tool bindings snapshot `{name, description}` + `http_config`, `requires_approval = risk_tier != read`; bindings gate on USE (`ensure_use` = bind) with a denied id ≡ absent (404 `detail.binding`) |
| US1 backend checkpoint | full suites | `-m "not integration"` **977 passed / 1 skipped / 1 failed** (held-out, known); `-m integration` **491 passed / 4 skipped** |
| T022 | contract regen | `emit_openapi.py` + `gen:api`: openapi diff = new `Draft`, `DraftOut`; `AgentOut` +description/draft/draft_dirty/model/provider/restore_status; `AgentPatch` +description/draft; `VersionOut` +created_by/is_active; POST versions only gained a docstring description (no shape change). No paths added/removed. `npm run lint` clean, `npm run build` OK (Node 24) |
| T023–T028 | `npx tsc --noEmit && npm run lint && npm run build` (Node 24) | all clean. Seams wired in `client.ts` (`getAgentWithEtag`, `patchAgent` w/ `If-Match`, `createVersion`, 422 `detail[]`→`input.invalid`), `adapters.ts` (draft↔config, `draftWithEdit`, `versionOutToVersion`), `store.ts` (`livePatch` + `editConflicts`/`resolveEditConflict` re-apply, `saveLiveDraft`, `createVersion`, `rollbackVersion` by id, archive/restore, "not available yet" for delete/duplicate/unpublish/tags/bindings/memory/spend), `agents/[id]/page.tsx` (ConflictDialog, draft-dirty badge, archived banner, LiveBindingsNote, Versions creator/id), `playground/page.tsx` (`versionsOf`). FE has no unit tests (approved exception) — behaviour proven by T029 |
| T029 setup | live stack | `AC_PROVIDER_TRANSPORT=fake docker compose … up -d --build api` (log: provider calls SCRIPTED, no paid calls) + `NEXT_PUBLIC_API_MODE=live next dev`; driven by Playwright (playwright-core 1.65.1, job-tmp, not a repo dep) against agent `d14859eb-…` in "Trellix XDR" (mock user = publisher). Assertions on the real network responses + DOM after hard reload |
| T029 L1 | edit description + prompt, save, hard reload | **PASS** — 2× PATCH 200; both values persisted after reload; "Draft changed since v1" badge shown |
| T029 L2 | two tabs, save A then B | **PASS** — A PATCH 200, B PATCH **409** → "Changed by someone else" dialog; "Reload and re-apply my edit" → PATCH 200; after reload the server holds B's value |
| T029 L3 | create labelled version, reload | **PASS** — POST versions 201; new label listed above v1 with creator, date, short id. **Found a gap:** the v1 that `POST /agents` creates showed "unknown creator" (create path never stamped `created_by`). Fixed TDD: Red `test_create_agent_stamps_creator_on_the_initial_v1` (`None == 'bob'`) → `created_by=principal.subject` in `routers/agents.py` create → Green; re-verified live after API rebuild (`v1 created_by = <subject>`) |
| T029 L4 | re-point (console half) | **PASS** — "Make active v2-…" sent `PATCH {"active_version_id": <uuid>}` (by id) → 200; header "v2-… active" after reload. **Deferred:** the playground-run half (`run_start.version`, Runs page id) needs US2 (T030+) |
| T029 L5 | bound KB deleted → create version | **PASS** — real KB created via API, bound into the draft via API (console can't bind: knowledge family is MOCK), then row hard-deleted in SQL (**no KB DELETE endpoint exists** — only archive). Console "Save version" → POST 404 `detail.binding=knowledge_base` → toast "A bound knowledge base no longer exists: …"; dialog stays open (no false success) |
| T029 L6 | role gating | **PASS** — (a) member row set to `viewer` (restored to `publisher` after): chip "Viewer", 0 enabled Save/Save-as-version (tooltip "Requires Builder role"); dev override → Builder → PATCH **403** `access.denied` (required `agent.edit`, role viewer) → toast "Save — Not permitted: You don't have permission…". (b) server publisher + override → Viewer: enabled Save 1 → 0. **Note:** controls are *disabled with a reason* (GateButton design), not hidden — quickstart wording says "hidden" |
| T029 L7 | archive/restore (console half) | **PASS** — previewed as Admin (console gates archive on `agent.delete`=admin; server allows builder+ — gate mismatch, see report) → PATCH `status:archived` 200 → read-only banner, no enabled Save; Restore → PATCH 200 → "Draft · v-active" after reload. **Deferred:** playground + published cURL refusal re-checked in US3 |
| Review fix #1/#2 | `pytest tests/test_api_026_review_fixes.py` (post-MVP `/code-review`, 2026-11-16) | Red: clone dropped `api_version` (model_config mismatch) + retired-profile base → `KeyError` (500). Green: `drafts.clone_version` copies the base's stored config verbatim (no `project()`/`profile_doc()`); `create_version` NULL-draft branch uses it. 2 tests PASS |
| Review fix #3 | same file | Red: draft-bound tool `schema_snapshot` = `{name, description}` (no `input_schema` → Anthropic rejects). Green: `_tool_def` snapshots `{name, description, input_schema}` — `http_config.input_schema` when an object schema, else `{"type":"object","properties":{}}`. 2 tests PASS |
| Review fix #6 | same file | Red: archived agent accepted draft/name/description/policy/re-point PATCH (200) and POST versions (201). Green: new `AgentArchived` (409 `agent.archived`); `patch_agent` allows only a bare status transition out of `archived` (restore still validated → 400 for a wrong target); `create_version` refuses. 8 tests PASS (incl. restore-then-edit) |
| Review fix #7 | Node strip-types harness on the real `adapters.ts` (job-tmp) | Red on HEAD: integer→number, top-level `additionalProperties`/`$schema` dropped, `enum: []` emitted (3/6 FAIL). Green: 6/6 PASS |
| Review fix #9/#10 | `tsc --noEmit` + `npm run lint` + `npm run build` (Node 24) | clean. #9: `saveLiveDraft` diffs against the RAW server draft; model card passes temperature/max_tokens only when the field was touched (untouched-default vs deliberately-chosen-default now distinguishable). #10: per-agent issue-sequence — `applyAgentOut` drops a response older than one already applied (stale GET can't roll back the ETag). FE no-unit-test exception: no automated proof, not re-checked live |
| Review fixes — suites | `pytest -m "not integration"` / `-m integration` | 977 passed, 1 failed (`test_heldout_gate_sc004` — known: owner-authored corpus pending), 1 skipped / **504 passed**, 4 skipped |
| US1 FE checkpoint | full suites after the L3 fix | `-m "not integration"` **977 passed / 1 skipped / 1 failed** (held-out corpus not authored — owner-gated, pre-existing); `-m integration` **492 passed / 4 skipped** (+1 new); `tsc` + `eslint` clean |
| T030 | Red `pytest -q -m integration tests/test_invoke_draft_target.py` | **8 failed / 2 passed**. Failures are behavioural: draft target ran the ACTIVE prompt; `run_start.version` was the version UUID, not `"draft"`/label; a missing draft KB returned 200, not 404; `target:"version"` with no id and `version_id` with a non-version target returned 200, not 400. The 2 guards that pass are existing behaviour: another agent's `version_id` → 404, and the published plane ignores `target`. Self-confirmed under the batch waiver (extended to US2 by assumption) — **owner review pending** |
| T031 | `pytest -q tests/test_invoke_parity.py -k draft` | **Passes on arrival. It is a guard, not a Red**: `drafts.transient_version` already existed (T018), so library-level draft↔version parity (text, cost, RunRecord except id/label/run-local fields, equal `snapshot_hash`) holds. The router-level Red for the draft path is T030. A first run failed on my own test defect: unpinned `correlation_id` |
| T032 | Red `pytest -q -m integration tests/test_api_archived_invoke.py` | **4 failed / 1 passed**. An archived agent answered 200 on the console (default target and every target) and on the published plane (G10 reproduced). The restore guard passes (existing). Asserts zero gateway calls and no Run row on refusal. **Owner review pending** |
| T033+T034 | `pytest -q -m integration tests/test_invoke_draft_target.py tests/test_api_archived_invoke.py` → full suites | **15 passed**; `-m "not integration"` **978 passed / 1 skipped / 1 failed** (`test_heldout_gate_sc004`: owner corpus not authored, pre-existing, confirmed on a stash); `-m integration` **519 passed / 4 skipped**; `emit_openapi.py` clean.<br>`schemas/invoke.py`: `PublishedInvokeIn` (no `target`) + console `InvokeIn(target)`; the published route takes `PublishedInvokeIn`.<br>`routers/invoke.py` `_resolve_snapshot`: draft → `drafts.transient_version` with bindings via `draft_bindings` (renamed public in `routers/agents.py`, one existence+USE check shared with save-version). A NULL draft runs the active snapshot relabelled `"draft"`. The run is persisted with `version_id` NULL.<br>The archived gate sits after the USE gate, before snapshot/key/provider: console 409 `agent.archived`, published 404.<br>**Contract delta, owner to note:** `run_start.version` is now the **label** (was the UUID; quickstart L4 expects a label), plus an additive `run_start.version_id` (null for a draft). No test pinned the old UUID. A draft run's call-log line carries the transient uuid as `version_id`; the persisted run row is NULL |
| T038 (done frame) | Red→Green `pytest -q -m integration tests/test_invoke_draft_target.py -k done_frame` | **Red**: `KeyError 'latency_ms'`. The `done` frame carried only `run_id/status`, and the `usage` frame has cost but no latency, so a turn could not show the run's own latency. **Green**: `done` adds `latency_ms` + `cost_usd` from the persisted RunRecord (`DoneFrame` schema updated); equals the Runs row. Additive contract delta. **Owner review pending** (batch waiver assumed) |
| T035 | `emit_openapi.py` + `npm run gen:api`; `tsc --noEmit` | Regenerated. New `frontend/src/lib/api/stream.ts`: `parseSseChunk` (CRLF, multi-line `data:`, comments, split chunks, bad-JSON skip) + `streamInvoke` (pre-stream non-2xx → typed `ApiError` via `parseError`; no `done` → `interrupted`; AbortError rethrown). Frame types hand-mirrored (the frame models aren't on a route, so OpenAPI omits them). A Node 24 throwaway check of the parser passed 3/3. `store.invokeAgentStream` resolves the principal via `callerFor` (auth in one place) |
| T036–T038 | `tsc --noEmit`, `npm run lint`, `NEXT_PUBLIC_API_MODE=live npm run build` (Node 24) | All clean/green. Playground live turn state machine (`data-turn-state`): streaming → complete / incomplete (partial kept + Retry in place) / refused (refusals.ts copy, no partial). Stop button + abort on unmount. Run selector: Draft / each version, "(active)" marked, maps to `target` (+`version_id`). Turn shows "ran: <label>", citation chips (document names), server latency, tokens, cost. Mock agents unchanged (FR-021). `refusals.ts` gains `provider_unavailable`, `provider_error`, `invalid_input`, stale-version 404 |
| T039 setup | live stack, `AC_PROVIDER_TRANSPORT=fake`, workspace "Trellix XDR", agent "US2 Playground Check" | v1 active ("ACTIVE prompt v1."), draft = different prompt + KB "US2 Refund KB" (`refund-policy.txt`, ingested ready), v2-kb saved from the draft (not active). UI was driven in headless Chromium over raw CDP (Playwright could not be installed; the npm registry hung). Wire checks via httpx against :8000 |
| T039 L8 | UI + wire | **PASS**. UI: turn `streaming` → `complete`, "ran: v1 · 0.00s · 39 tokens · $0.0001". Wire: 10 `text_delta` frames; `done.latency_ms`/`cost_usd` equal the persisted row (0 ms / 0.000056; DB-checked). Fake transport doesn't pace deltas, so "incremental" is proven at the frame level + by the proxy fix below, not visually. **Gap:** the stream's `run_id` is the runtime hex id; `runs.id` is a separate uuid (`run_sink` never stores `rec.run_id`), so a turn can't deep-link to its Runs row. Pre-existing (005/006), not fixed here |
| T039 L9 | UI + wire on v2-kb, query = the chunk text (fake embeddings hit only on exact text) | **PASS**. `citations` frame: `knowledge_used: true`, `document_name: "refund-policy.txt"`, score 1.0, status `used`. UI shows the `refund-policy.txt` chip + "ran: v2-kb" |
| T039 L10 | 7 refusals, wire (all 7) + UI (rate, timeout, provider_error) | **PASS, 7 distinct**: cap → 402 `budget.exceeded` (cap 1e-7 < MTD); burst → 429 `rate.limited` + `Retry-After: 59` (120 OK then 15× 429); bogus active provider key → 503 `config_error` (row removed after); `[[fail:transient]]` → `provider_error` ×2 then breaker open → `provider_unavailable` on a normal message; `[[fail:timeout]]` → in-stream `timeout`; restricted (admin-only) agent as a publisher → 404 `not_found`; unknown `version_id` → 404 with `detail.version_id`. UI rendered "Too many requests — retry in 14 s" (refused), "Stopped: took too long" (incomplete, 30.00s). **Finding:** the console route is gated `rate_limit("invoke")` (120/60s), so the `playground` limit (60/60s) in settings is dead config. Pre-existing, not changed |
| T039 L10 fix (proxy) | Red→Green `pytest -q -m integration tests/test_invoke_sse_headers.py`; live re-check via :3000 | **Found live**: through the Next `/api/v1` rewrite the SSE came back `Content-Encoding: gzip` and **no frame arrived until the body ended** (a 0.1 s `run_start` never reached the client), and the proxy's 30 s idle timeout cut the stream at 30.0 s → the UI showed "Connection lost" instead of the timeout. **Red**: header test failed (no `Cache-Control`). **Green**: both invoke planes send `Cache-Control: no-cache, no-transform` + `X-Accel-Buffering: no` (Next's bundled `compression` honours `no-transform`); `next.config.ts` sets `experimental.proxyTimeout: 120_000` (> the 30 s server deadline; `0` means 30 s). Re-check via :3000: no gzip, `run_start` 0.2 s, `error`+`done` 30.2 s; UI shows "Stopped: took too long". **Owner review pending** |
| T039 L11 | UI + wire `[[fail:midstream]]` | **PASS**. Wire: partial text, then `error provider_error`, then `done status=provider_error`. UI: partial "[fake] Re: go for" kept, "(Partial answer — incomplete.)", Retry. Retry re-ran in place (turn count stayed 1; 2 `provider_error` rows in the DB). Retry re-sends the same message, so a deterministic marker fails again; an unmarked retry → `success` (wire) |
| T039 L12 | UI + wire + DB | **PASS**. Draft: `run_start.version="draft"`, `version_id` null, DB row `version_id` NULL, UI "ran: draft". Active: "v1" + v1 id. Version: "v2-kb" + v2 id. Switching the Run target starts a fresh transcript (the page re-keys the playground); noted, not a defect |
| US2 suites | `-m "not integration"`; `-m integration`; FE `tsc`/lint/live build | **978 passed / 1 skipped / 1 failed** (pre-existing `test_heldout_gate_sc004`); **521 passed / 4 skipped**; FE clean + build green |
| US2 decisions (owner, 2026-11-16: "take the recommended steps") | — | **Accepted**: #4 `run_start.version` = label + additive `version_id`; #5 additive `done.latency_ms`/`cost_usd` + separate `PublishedInvokeIn` body. **Red waiver → batch-per-story**: Claude self-confirms Reds during a story and records them here; the owner reviews them once per story (US1+US2 batch digest delivered 2026-11-16 — review still pending, not marked). **#8 deferred** to the prod-deploy feature: the prod ingress/LB in front of the SSE planes must disable response buffering + compression for `text/event-stream` and allow an idle timeout > 30 s (the server deadline); the `next.config.ts` `proxyTimeout` + `TODO(deploy)` covers only the dev rewrite. **#10** remains open |
| #6 run identity | Red→Green `pytest -q -m integration tests/test_run_identity.py` | **Red**: `GET /api/v1/runs/{done.run_id}` → 404 "run not found" (the row got a fresh server-default uuid). **Green**: `run_sink.persist_run_record` stores the RunRecord's own `run_id` as `runs.id` (non-uuid ids fall back to the default). Console + published: `run_start.run_id == done.run_id`, the Runs detail opens, and the agent's `runs.id` set equals the streamed ids. Closes the L8 gap. Self-confirmed — **owner review pending (US2 batch)** |
| #7 playground bucket | Red→Green `pytest -q -m integration tests/test_playground_rate_class.py` | **Red**: with `playground=(60,1)` / `invoke=(60,100)` the 2nd console invoke returned 200 (it counted against `invoke`). **Green**: console route → `rate_limit("playground")`; 2nd console call → 429 `rate.limited`; the published plane (token-keyed `invoke`, `publish/token_auth.py`) unaffected, 2× 200. Effective console budget drops 120 → 60/60 s per subject (the configured value). Self-confirmed — **owner review pending (US2 batch)** |
| #9 lifecycle roles | Red→Green `pytest -q -m integration tests/test_agent_lifecycle_roles.py` | **Red**: a builder's PATCH `status:"archived"` and a re-point `active_version_id` both returned 200. **Green**: `patch_agent` gates actual transitions after the event set is derived — archive/restore need `agent.delete` (admin), re-point needs `agent.publish` (publisher), → 403 `access.denied` with `detail.required_permission`; ordinary edits stay `agent.edit` (builder+) and re-sending the current version/status is an ungated no-op. Matches the console gates (`agents/page.tsx` `agent.delete`, re-point `agent.publish`). 27 existing integration tests that archived/re-pointed as builder now use admin/publisher claims (7 files). Self-confirmed — **owner review pending (US2 batch)** |
| decisions #6/#7/#9 suites | `-m "not integration"`; `-m integration` | **978 passed / 1 skipped / 1 failed** (pre-existing `test_heldout_gate_sc004`, fails identically on a stash); **525 passed / 4 skipped**. No FE change (FE already gates; the archive/re-point actions are the only senders) |
| review-2 #1/#2/#7 lifecycle | Red→Green `pytest -q -m integration tests/test_api_026_review2_lifecycle.py` | **Red**: `POST /publish` on an archived agent → 200 (bypassed the admin-only restore) and wrote no audit row; a builder's PATCH `status` draft→published / published→draft / eval_failing→draft → 200; `POST /eval-runs` on an archived agent → 200 (the eval actually ran, spending provider calls). **Green**: publish + eval-runs refuse archived with 409 `agent.archived` before any work; publish records `agent.publish` (`prior_status`, `version_id`); every non-archive status transition in PATCH needs `agent.publish` (→403 `access.denied`) and is audited (`agent.publish` for →published, new `agent.status` action otherwise); re-sending the current status stays an ungated no-op. Self-confirmed — **owner review pending (US2 batch)** |
| review-2 #5 disconnect | Red→Green `pytest -q -m integration tests/test_invoke_disconnect_run.py` | **Red**: aclose at a yield / CancelledError mid-provider-await / late finalizer → **zero runs** persisted (the Runs page and the month-to-date spend cap missed the billed call); the runtime labelled an early close `success`. **Green**: runtime emits its RunRecord on `GeneratorExit` *or* `CancelledError` as new `Outcome.CANCELLED` (`outcome` is Text — no migration); the router's shielded `finally` persists it on the request session while it is still in its transaction, else on a fresh `tenant_session`. A mid-stream revoke now also leaves a `cancelled` run. A teardown segfault (2/4 full runs) traced to reusing an already-closed request session (autobegin on an un-GUC'd pooled connection) → guarded with `session.in_transaction()`; 4/4 full runs then clean. Self-confirmed — **owner review pending (US2 batch)** |
| review-2 #4 draft-derived version | Red→Green `pytest -q -m integration tests/test_api_draft_preserves_base.py` | **Red**: first version saved after a draft edit had `model_config.api_version` **None** and **no MCP binding** (the Draft can't express either). **Green**: same provider+model → inherit the base's extra `model_config` keys + frozen `profile_snapshot`; MCP/subagent bindings copied from the base (the draft edits http/builtin only). Guard: switching model does not carry the old `api_version`. Self-confirmed — **owner review pending (US2 batch)** |
| review-2 #8 version io_contract | Red→Green (same file) | **Red**: `KeyError: 'io_contract'` — `VersionOut` didn't expose it, so the playground sent `target=version` turns under the *draft's* field name. **Green**: `VersionOut.io_contract`; OpenAPI + `schema.ts` regenerated; adapter maps `Version.inputVariables`; `messageField(agent, run)` uses the running version's (or active's) contract. Self-confirmed — **owner review pending (US2 batch)** |
| review-2 #3/#10 FE | `tsc --noEmit`, `npm run lint`, `NEXT_PUBLIC_API_MODE=live npm run build`; throwaway Node check | §9 FE exception (no FE test suite). #3: `livePatch` with no cached ETag (list-page archive) force-loads the agent, then patches; still-missing → critical toast instead of an un-`If-Match`ed send. #10: `parseSseChunk` holds a trailing `\r` so a CRLF split across chunks isn't read as two line ends — old parser **1/97** split points mis-framed `done` as `message`; new **0/97**. tsc 0, lint clean, live build green |
| review-2 #6 / #9 | — | #6 (live Model card reads the mock catalog) **deferred to US3/T071**. #9 (`run_start.version` = label/`"draft"`, not UUID) **kept** — owner-accepted; `version_id` carries the UUID. cURL-snippet check moves to US3 T040+ |
| review-2 suites | `-m "not integration"`; `-m integration` | **978 passed / 1 skipped / 1 failed** (pre-existing `test_heldout_gate_sc004`); **541 passed / 4 skipped**, exit 0 on 4 consecutive runs |
| T012 | `npm run lint && npm run build` | lint clean, build green. `parseError(res, path)` exported (envelope `code/message/detail` + `Retry-After`), `ApiError.detail/retryAfter` added (constructor back-compatible), `apiFetchWithMeta` returns `{data, etag}`; `apiFetch` unchanged in signature |
| T013 | `npm run lint && npm run build` | lint clean, build green. `refusals.ts` covers 402 (+amounts), 429 (+Retry-After s), 403, 404, 404+`detail.binding` (kb/tool/skill), 502, 503, stream `timeout`, `auth.required` (mid-stream revoke), 409 `conflict`/`conflict.stale`, `agent.archived`, `eval.suite_has_runs`, `version.frozen`, `input.invalid`; unknown → server message verbatim |
