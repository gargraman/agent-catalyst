# Research — 026 Core Loop Live

**Date**: 2026-11-16 · **Input**: [spec.md](spec.md) (Must items 1–9) · **Method**: two read-only code sweeps
(`services/`, `frontend/`) at the 025-closed tree, plus targeted reads of `api/models.py`, `evals/judge.py`,
`evals/runner.py`, `api/publish/token_auth.py`. Every **G** row is a verified fact, cited. Every **D** row is a
decision with the alternatives considered.

## Part 1 — Ground truth (G)

| # | Fact | Evidence | Consequence for 026 |
|---|------|----------|---------------------|
| G1 | `AgentPatch` accepts only `name, status, active_version_id, access_policy`. It has no description, model or draft config | `api/schemas/agents.py:29-34` | Editor saves have nowhere to go (FR-002). Needs a draft model (D1) |
| G2 | **No draft exists anywhere.** The `agents` row has no config. `create_version` **clones the latest version's** prompt/model/io_contract, and the request cannot change them | `api/models.py:86-103`; `api/routers/agents.py:368-374` | "Edit prompt → save → version" is impossible today (D1, D2) |
| G3 | `create_version` copies retrieval bindings but sets **tool and skill bindings to `[]`**, so every new version silently drops its tools/skills | `api/routers/agents.py:378-384` | A latent correctness bug. Fixed by D2 (bindings come from the draft) |
| G4 | Stale-edit detection already exists. ETag = `xmin`, `If-Match` is required (400 when missing), stale → 409 `conflict.stale` | `agents.py:216-261` | Reuse it. The FE currently **discards** the ETag (`store.ts:349-350`) |
| G5 | `agent_versions` has no `created_by` and no status. `publishable` is never set True | `models.py:105-129` | FR-003 needs the creator, so add a nullable `created_by` (D2). Version status stays out (audit gap 6, not this feature) |
| G6 | No `GET …/versions`. Versions are embedded in `AgentOut.versions`, oldest first | `agents.py:64`; `schemas/agents.py:72-82` | Reuse the embed (D12) |
| G7 | Playground invoke `InvokeIn{inputs, version_id?}` runs **only saved versions**. There is no draft target | `schemas/invoke.py:14-16`; `invoke.py:50-54` | US2-7 "run the draft" needs a target (D3) |
| G8 | `runs.version_id` is **nullable** | `models.py:508` | A draft run can be recorded honestly as "no version" (D3) |
| G9 | `AgentVersion.to_snapshot()` is a pure read over columns + loaded bindings → `VersionSnapshot` | `models.py:131-163` | A transient (unsaved) version built from the draft goes through the **same** snapshot + `invoke_result` path (D3, P6 preserved) |
| G10 | **Archived agents are still invocable.** Neither invoke plane nor token auth checks status | `invoke.py`; `publish/token_auth.py` | FR-004 is a real backend gap, not wiring (D4) |
| G11 | Publish never reads an eval and returns `advisory_verdict=None` | `routers/evals.py:130-155` | FR-010's "advisory verdict" does not exist (D5) |
| G12 | `POST …/eval-runs` takes `cases`, `baseline{quality,cost_usd,latency_ms,per_case}` and `policy` **from the body**. It persists only per-case `runs` rows (`source='eval'`), with no aggregate | `schemas/evals.py:22-52`; `evals.py:4,113-116` | This is the hole FR-015 closes (D6–D9) |
| G13 | Verdict enum = `pass \| fail \| low_confidence`. The runner forces `low_confidence` when no case has a usable judge score. Case sets are never matched by id | `evals/drift.py:94-142`; `runner.py:97-99` | Persist the engine verdict **plus** a reason code. The spec's "drift / insufficient evidence" labels are reason codes, not new verdicts (D8) |
| G14 | The judge returns **absolute** rubric scores per case **and** a pairwise winner. With an empty baseline output the rubric is still meaningful; only the pairwise vote is not | `evals/judge.py:87-114`; `runner.py:152` | A first run with no baseline still yields a real quality score, so it can serve as a future automatic baseline (D7) |
| G15 | No eval tables and no `eval_verdict` enum in any migration. They are designed in `docs/backend/01-data-model.md:441-512` (`eval_suites/_cases/_baselines/_runs/_results`) | migrations 0001–0015 | Build a subset, aligned to the designed names (D6) |
| G16 | **No audit table or helper.** Security events go only to the `api.security` logger. Designed: `audit_logs(id, workspace_id, actor, action, target, meta, created_at)` | `tokens.py:28,170`; `01-data-model.md:688-702` | FR-023 requires an audited freeze, so build the minimal table (D10) |
| G17 | RBAC: builder has `agent.edit, eval.run, eval.author`; publisher adds `agent.publish`; admin adds `eval.govern`. There is no separate lifecycle permission | `api/rbac_permissions.json` | Freeze/clear = `eval.govern` (admin), per the owner's answer to O2. This matches the design docs (D11) |
| G18 | Tokens are fully built: mint (secret once, `ac_live_…`, 90-day cap), list (masked prefix, `last_used_at`), revoke (DELETE + Redis deny-set), mid-stream re-check `AC_DENYSET_STREAM_RECHECK_S=2.0` | `routers/tokens.py`; `settings.py:101,115` | US3 is **pure FE wiring** |
| G19 | The published plane authenticates `Authorization: Bearer <secret>`, at `POST /v1/agents/{id}/invoke` | `publish/token_auth.py:46-47`; `invoke.py:177` | The cURL template (D17) |
| G20 | SSE frames: `run_start, text_delta, reasoning_delta, tool_call_*, usage{cost_usd,cost_is_estimate}, citations, knowledge_status (console only), error{code,message}, done{run_id,status}`. Pre-stream refusals use the HTTP envelope: 402 (detail `cap_usd, month_to_date_usd`), 429 (+`Retry-After`), 403, 404, 503 `config_error`, 502 `provider.unavailable` | `api/sse.py`; `deps.py:160-263`; `errors.py` | The FE refusal table (D16) |
| G21 | **The FE has no streaming client.** The playground answer is a synchronous mock with hardcoded cost/latency (`components/playground.tsx:54-88`). `apiFetch` is not exported and has a 15 s timeout | `client.ts:68-90` | A new streaming fetch is needed. The 15 s timeout must not apply to streams (D15) |
| G22 | FE live convention: `load(key, fn)` with in-flight de-dup, `RemoteStatus`, `useEnsureX`, `warnLocalOnly` toasts, `pickServerOwned`. The only live mutation is `addAgent` | `store.ts:198-391,641-667` | Each wired seam follows this convention. `warnLocalOnly` call sites are deleted as each seam is wired |
| G23 | Mock eval UI: re-freeze is `GateButton eval.govern` → toast only. Baseline text is hardcoded "Frozen at v6". `simulateEvalRun` uses `Math.random`. FE `EvalCaseDef.assertions` has kinds the backend cannot enforce (`no_refusal`, `has_citation`, …) | `agents/[id]/page.tsx:951-1215`; `store.ts:556-600,1054-1144`; `data.ts:916-942` | D6 case shape. Unenforceable assertion kinds must not *look* enforced in live mode (constitution §1) |
| G24 | `ApiToken.agent` is the agent **name**, not its id. Publish URL hardcoded `https://agent-platform.internal/…` | `data.ts:1036`; `page.tsx:1288,1294` | The adapter keys by id. The URL comes from config (D17) |
| G25 | Integration status counter: `docs/backend-integration-status.md` lines 5/12/105 read **4 / 257**. There is no row for eval suites/cases/baseline | that file | SC-002 bookkeeping (D20) |

## Part 2 — Decisions (D)

Format: **Decision** · Rationale · Alternatives rejected. **(Must)** unless tagged.

### D1 — Draft lives on the agent row (`agents.draft` jsonb + `agents.description`)
- **Decision**: Add `description text NOT NULL DEFAULT ''` and `draft jsonb NULL` to `agents`. The draft is a
  normalized object `{provider, model, model_params, system_prompt, io_contract, kb_ids[], tool_ids[], skill_ids[]}`.
  Saving is `PATCH /agents/{id}` with `description` and/or `draft`, where `draft` is a full replacement.
  - It reuses the existing `xmin` ETag (G4), so one token guards metadata and draft together.
  - A `NULL` draft reads as "the active version's config", so existing agents need no backfill and the migration is
    additive.
  - `AgentOut` gains `description`, `draft` (resolved), `draft_dirty` (the normalized draft differs from the active
    version) and `provider/model` (from the resolved draft).
- **Why**: US1-1 needs "saved but not yet a version" plus a "changed relative to active" flag. Postgres already gives
  the agent row optimistic concurrency.
- **Rejected**: (a) *Every save creates a version.* That buries the version list in noise and leaves no "changed vs
  active" state. (b) *A separate `agent_drafts` table.* It splits the ETag across two rows and adds a join. The draft
  is 1:1 with the agent anyway. (c) *Draft held only in the browser.* That is the current "local only" defect.

### D2 — `create_version` snapshots the draft, in one transaction, and records `created_by`
- **Decision**: `POST /agents/{id}/versions` builds the version from the **resolved draft**.
  - If the draft is NULL, it clones the active version, as today.
  - `profile_snapshot` and `prompt_hash` are computed exactly as `create_agent` computes them for v1.
  - Tool, skill and KB bindings are written from the draft's id lists **in the same transaction**. 025's
    insert-window trigger requires this, and it fixes G3.
  - Add a nullable `agent_versions.created_by text` (subject, from the principal). The freeze trigger allows it because
    it is set at INSERT.
  - A dangling draft id hits 025's composite FK, which maps to 404 `not_found` (FR-005). The route pre-checks and names
    the missing binding kind in `detail`, e.g. `{"binding":"knowledge_base","id":…}`. The id comes from the caller's own
    draft, so naming it leaks nothing.
- **Rejected**: keeping clone-latest. That makes draft editing pointless and keeps the G3 binding-drop bug.

### D3 — Playground "run the draft" uses a transient snapshot through the same runtime path
- **Decision**: `InvokeIn` gains `target: "active" | "draft" | "version"` (default `"active"`); `version_id` is
  required iff `target="version"`.
  - On the **console plane only**, `target="draft"` builds an **unsaved** `AgentVersion` (never added to the session),
    with its bindings loaded from the draft's id lists, calls `to_snapshot()` (G9), and runs the identical
    `invoke_result` / `_run_invoke` body.
  - The run is recorded with `version_id = NULL`, and `run_start.version = "draft"`.
  - The published plane rejects anything but `active` (it has no `target` field). Eval runs only accept a saved
    `version_id`.
- **Why**: P6 (one runtime contract) holds. The only difference is where the snapshot comes from, and that is
  already true for versions. Evals stay pinned to immutable versions, so eval evidence is never about a draft
  (constitution §4/§6).
- **Risk**: `snapshot_hash` for a transient version is computed from unsaved columns. The hash helper is pure (G9),
  but a unit test must pin that it does not touch the DB. If `invoke` assumes a UUID `version_id` anywhere (sink,
  call log), that is a Red-test finding to fix, not a reason to drop D3.
- **Fallback (owner call at plan review)**: drop the draft target and make the console's "Try draft" button do
  *create version → run it*. That is cheaper, but every experiment becomes a version.
- **Rejected**: an "ephemeral version row deleted afterwards". The freeze trigger and the 025 binding FKs make deletion
  a fight, and it pollutes audit.

### D4 — Archived agents stop answering; restore returns to the prior status
- **Decision**:
  - On the console invoke and eval-run routes, an archived agent returns **409 `agent.archived`** (a new
    `ConflictError` subclass).
  - On the published plane it returns **404 `not_found`**. A caller cannot tell archived from absent, which is
    consistent with the existence-hiding rule.
  - Add `agents.status_before_archive` (nullable).
    - Archive (`PATCH status:"archived"`) stores the prior status.
    - `AgentOut.restore_status` exposes that prior status. Restore is `PATCH status:<restore_status>`, and the server
      clears the column.
    - No new verb is added. Restoring a previously published agent therefore answers again.
- **Rejected**: restore always returning `draft` (US1-7 says restoring "brings both back"), and an orthogonal
  `archived_at` flag (the FE `AgentStatus` already models archived as a status; changing that would reshape the UI).

### D5 — Publish returns an advisory verdict read from persisted eval runs
- **Decision**: `POST /agents/{id}/publish` returns `advisory: {verdict, reason_code, eval_run_id, suite_id} | null`.
  This is the most recent **completed** eval run of the published `version_id` (any suite). With none, it returns
  `{verdict:"low_confidence", reason_code:"no_eval_run"}`.
  - It makes no provider call, has zero cost, and never blocks (the owner's advisory-gate decision). Publish writes an
    audit row (D10).
- **Rejected**: running an eval inside publish (cost and latency on a click; scope item 13 is the enforcing gate, Nice).

### D6 — Eval persistence: suites, cases, runs, results, baseline pointer (aligned to 01-data-model names)
- **Decision**: One migration `0016_core_loop` adds `eval_suites`, `eval_cases`, `eval_runs`, `eval_results`,
  `eval_baselines` and `audit_logs`, all `WorkspaceScoped`, with RLS FORCE + `_ws_isolation` policy (0008 pattern),
  composite same-workspace FKs (025 G8 lesson: RI bypasses RLS) and `ON DELETE RESTRICT`, except cases cascading from a
  deleted suite. Columns are in [data-model.md](data-model.md).
  - **Case shape** = what the engine can enforce: `input, rubric[{name,weight}], required_fields[], expected?`, plus a
    server-computed `content_hash`.
  - The FE's other assertion kinds are **not offered in live mode**. They show "not available in live yet" rather than
    being silently stored and ignored (constitution §1).
- **Deviation from the design doc**: `eval_baselines` is keyed **per suite → eval_run** (the frozen run), not per
  version + `results_hash`. A run carries its own results, which is what the comparison needs; the doc predates the
  automatic rule. This is recorded as a delta in `docs/backend/05-eval-drift.md` at DoD.
- **Rejected**: storing suites as a jsonb blob on the agent (no per-case identity for D8, and every edit would contend
  with the agent ETag).

### D7 — Eval run request carries only `{version_id, suite_id}`; baseline and policy are server-resolved (FR-015, FR-023)
- **Decision (owner, O3)**: `EvalRunIn = {version_id, suite_id}` with `extra="ignore"`. A body carrying
  `baseline`, `policy` or `cases` is accepted, and those fields are **dropped before any logic runs**. They never
  reach the resolver, the runner or the persisted row. SC-004 is proven by a test that sends a flattering
  baseline and asserts the verdict and persisted row are identical to the same run without it.
  - **Cost of this choice, stated plainly**: a stale client that still sends cases will now silently run the
    *server* suite instead. That's a bug that won't announce itself. Mitigation: the response always carries
    `suite_id`, `total_cases` and `baseline` provenance, so a mismatch is visible in the result. There's no log line,
    per "silently".
  - **Baseline resolution, in order:**
    1. `eval_baselines` row for the suite (frozen) → that run.
    2. Otherwise the latest `eval_runs` row with `status='completed'`, `suite_id` = this suite and
       `version_id = agents.active_version_id`, excluding the run being created.
    3. Otherwise none.
  - When the candidate *is* the active version, the automatic rule compares it with its own previous run. That is a
    legitimate "same version, did the model/provider drift" check, and the verdict names the baseline run.
  - Policy comes from `workspace_settings.eval_policy` merged over the engine defaults.
  - Cases are loaded from the suite, sorted by id, and capped at 25 (Assumption: synchronous at starter-suite size).
    Above the cap the request returns 400 `input.invalid` and never truncates silently.
- **Baseline aggregate** comes from the baseline run's `eval_results` over the **common** cases (D8):
  - quality = mean of stored per-case quality
  - cost = sum of stored per-case cost
  - latency = mean of stored per-case latency
  - `per_case` = stored candidate outputs, which feed the pairwise judge
- **No baseline**: the judge still runs with an empty baseline output, so rubric scores stay valid (G14). The verdict
  is forced to `low_confidence` with reason `no_baseline`, and the run is persisted, so it becomes the next automatic
  baseline once its version is active.
- **Rejected**: (a) 422 on extra fields (my original default; the owner chose silent ignore, O3); (b) re-running the baseline
  version every time, which doubles cost and lets the "baseline" itself drift.

### D8 — Case-set mismatch: compare common cases only, report the diff
- **Decision**: Two cases are *common* when `(case_id, content_hash)` matches between the candidate's suite and the
  baseline run's results.
  - The comparison uses the common set only. The run records `compared_cases`, `new_cases` and `dropped_cases`.
  - If `compared_cases < policy.min_cases`, the verdict is `low_confidence` with reason `baseline_stale`.
  - New cases are still executed and judged; they are shown but excluded from deltas.
- **Reason codes**, persisted alongside the engine verdict: `quality_drift, cost_drift, hard_fail, below_min_cases,
  no_baseline, baseline_stale, judge_unavailable, ok`. The engine's `verdict_with_reason` gives the mapping for the
  first three plus `ok`; 026 adds the rest at the call site.
  - The FE labels `low_confidence + {no_baseline|below_min_cases|baseline_stale}` as "Insufficient evidence".
- **Rejected**: comparing whole aggregates regardless of case set. FR-017 forbids it.

### D9 — Synchronous run, atomic aggregate
- **Decision**: Cases run inside the request deadline, as today. The `eval_runs` and `eval_results` rows are written
  in **one transaction after** scoring.
  - If the deadline or an unexpected error hits mid-run, a separate short transaction writes
    `eval_runs(status='failed', reason=<code>)` with no results. Per-case `runs` rows already written by the run sink
    stay, which keeps cost honest.
  - Only `completed` runs can be baselines or advisory verdicts.
  - A spend-cap refusal happens before anything is persisted, as today (US4-6).
- **Rejected**: async jobs (scope item 12, Nice).

### D10 — Minimal `audit_logs`, written in the same transaction as the change
- **Decision**:
  - **Table**: `audit_logs(id, workspace_id, actor, action, target_type, target_id, meta jsonb, created_at)`, RLS
    insert/select, with **no UPDATE/DELETE grant** to `ac_app` (append-only).
  - **Helper**: `api/audit.py:record(session, principal, action, target_type, target_id, meta)`. It is called inside the
    route's transaction, so the change and its audit row commit or roll back together.
  - **Actions in 026**: `baseline.freeze`, `baseline.clear` (FR-023, required), `agent.publish`, `agent.repoint`,
    `agent.archive`, `agent.restore`.
  - Token mint/revoke stay on the existing security logger (out of scope; noted).
  - A read endpoint is **Nice**. Evidence comes from integration tests that query the table.
- **Rejected**: logger-only, because a log line is not an auditable record queryable per workspace. Building the full
  018 audit programme is out of scope.

### D11 — Permissions
- **Decision**:

  | Action | Permission |
  |---|---|
  | Suites/cases CRUD | `eval.author` (builder+) |
  | Run an eval | `eval.run` (builder+) |
  | Freeze/clear the baseline | **`eval.govern`** (admin only, owner O2) |
  | Draft/metadata save, version create, re-point, archive/restore | `agent.edit` (as today) |
  | Publish, tokens | `agent.publish` (as today) |

  The FE `Re-freeze` `GateButton` keeps `eval.govern` (already correct). Per-resource access: suites/runs
  require `ensure_use(agent)` to read and `ensure_manage(agent)` to write. A denial returns 404, the same as absent
  (SC-007).
- **Resolved (O2)**: admin-only, which matches `docs/backend/08-api-surface.md`, so no doc delta is needed.
  Consequence: in a workspace with no admin, nobody can freeze, and every suite stays on the automatic rule. This
  is acceptable, because the automatic rule always produces a named baseline.

### D12 — Versions list reuses the `AgentOut.versions` embed
- **Decision**: No new route. `VersionOut` gains `created_by` and `is_active`; the FE sorts newest first. Re-point stays
  `PATCH active_version_id` (G4) and writes an `agent.repoint` audit row.
- **Rejected**: a paginated `GET …/versions` route. It is the right call once version counts grow (Nice), but it is
  not needed for v1 starter usage.

### D13 — Stale-edit UX
- **Decision**: `loadAgent` keeps the response `ETag` in a store-side map (`etags[agentId]`, not in the `Agent`
  type). Every PATCH sends `If-Match`. A 409 `conflict.stale` opens a "Changed by someone else — reload and re-apply"
  dialog, which refetches with `force` and preserves the user's unsaved form values for re-apply. A successful PATCH
  updates the ETag from the response header.

### D14 — Eval history routes
- **Decision**: `GET /agents/{id}/eval-runs` and `GET /eval-runs` (workspace scope), keyset-paginated newest first,
  returning `EvalRunOut` (summary + baseline provenance). `GET /eval-runs/{rid}` returns the detail with per-case
  results. Access is filtered per agent (`ensure_use`), and rows of agents the caller cannot use are omitted, as the
  agents list does.

### D15 — Streaming client: `fetch` + `ReadableStream`, not `EventSource`
- **Decision**: A new `src/lib/api/stream.ts:streamInvoke(principal, agentId, body, {signal, onFrame})`.
  - It POSTs (EventSource cannot POST or send dev headers) and parses `event:` / `data:` frames incrementally.
  - It has **no** 15 s timeout. The caller passes an `AbortController`, aborted on unmount or "Stop".
  - A non-2xx response before the stream starts is parsed through the same envelope logic as `apiFetch`, which is
    factored into a shared `parseError`.
  - A dropped connection without `done` is reported as `interrupted`.
- **Rejected**: an SSE library (adds a dependency for about 60 lines of parsing) and EventSource (GET-only).

### D16 — Refusal → message table (FR-007, SC-005 7/7)

| Condition | Wire signal | Console message |
|---|---|---|
| budget exceeded | 402 `budget.exceeded` `{cap_usd, month_to_date_usd}` | "Monthly spend cap reached — $X of $Y used." |
| rate limited | 429 `rate.limited` + `Retry-After` | "Too many requests — retry in N s." |
| access denied | 403 `access.denied` | "You don't have permission to run this agent." |
| not found | 404 `not_found` | "This agent or version is no longer available." |
| provider unavailable | 502 `provider.unavailable` (also an `error` frame) | "The model provider is temporarily unavailable." |
| configuration error | 503 `config_error` (also an `error` frame) | "No provider key is configured for this workspace." |
| deadline exceeded | `error{code:"timeout"}` frame | "The answer took too long and was stopped." |

  Plus `agent.archived` (409, D4) and mid-stream revocation (`error{auth.required}`). An unknown code shows the
  server message verbatim. The message never says a provider call happened for a pre-stream refusal.

### D17 — cURL snippet
- **Decision**: `${PUBLIC_API_BASE}/v1/agents/${agentId}/invoke` with `-H "Authorization: Bearer $AC_TOKEN"`,
  `-H "Content-Type: application/json"`, `-N`, and the body `{"inputs": <example from the version io_contract
  input_schema, else {"message":"Hello"}>}`.
  - `PUBLIC_API_BASE` = `NEXT_PUBLIC_PUBLIC_API_BASE`, falling back to `window.location.origin`.
  - The token is **never** interpolated, not even right after mint. The snippet always references `$AC_TOKEN`.
  - The fake `stream:true` field is removed because the backend has no such field.
- **Verified by**: quickstart §5 runs the copied command verbatim against `make up`.

### D18 — Tokens FE
- **Decision**: `loadTokens(agentId)` (key `tokens:${agentId}`), `mintToken` (shows `secret` once in the dialog and
  never puts it in the store), `revokeToken` (DELETE, then refetch). The adapter maps `TokenOut` → `ApiToken` keyed by
  `agent_id`. `CreateTokenDialog`'s fake secret is deleted for live workspaces.

### D19 — Mock mode untouched
- **Decision**: Every new store function branches on `isLiveWorkspace` / `isLiveAgent` (G22). The mock branch stays
  byte-identical in behaviour. The quickstart re-checks mock mode (FR-021).

### D20 — Seam bookkeeping (SC-002)
- **Decision**: [contracts/frontend-seams.md](contracts/frontend-seams.md) enumerates each `@backend` marker 026 flips.
  The status doc's counter rises by exactly that count, and two new rows are added (eval suites/cases, baseline). A
  seam counts only with a passing build **and** a recorded live check.

### D21 — Test strategy (constitution §9)
- **Backend**: Red-first per task.
  - `pytest -m integration` for the migration, RLS, isolation, FKs and the audit append-only grant.
  - Infra-free tests for baseline resolution (pure function over rows), case-set diff, reason-code mapping, the
    transient snapshot and the SSE target parsing.
  - The judge and provider use the existing fake transport. **No paid calls.**
- **Frontend**: no test suite exists (CLAUDE.md). Evidence = `npm run lint` + `npm run build` + scripted live checks in
  [quickstart.md](quickstart.md) against `make up` with the fake/dev provider path.
- **Disclosed gap**: there is no automated FE test of the streaming parser, and adding a test harness is out of scope
  (Nice). Mitigation: keep `stream.ts` parsing a pure function `parseSseChunk(buffer) → {frames, rest}`, so it can be
  unit-tested the moment a harness lands.

### D22 — Dev-only fake provider transport for live checks (no paid calls)
- **Finding**: the compose `api` has no fake or echo transport (`api/settings.py`, `runtime/providers/transport.py`).
  Every live playground or eval check would therefore be a **paid** provider call, which the constitution forbids
  as an ordinary test.
- **Decision**: Add `AC_PROVIDER_TRANSPORT=fake` (default `litellm`). It injects a deterministic scripted transport
  (streams a canned answer in chunks, fixed usage, optional scripted failure via the prompt marker
  `[[fail:<code>]]`), built from the existing test `_fault_transport` shape.
  - Settings validation **refuses `fake` when `AC_ENV=prod`**, so startup fails loudly.
  - The judge goes through the same transport, so evals are deterministic in dev.
  - Costs are labelled `cost_is_estimate=true` (constitution §7: it is not a real charge).
- **Real-provider smoke (owner-approved, O5)**: run **once**, at the end of implementation.
  - **Model**: `claude-haiku-5-5`, the cheapest model already in the capability profiles and the judge model. This
    is an assumption; the owner can name another.
  - **Input**: a synthetic prompt.
  - **Hard cap ≤ $0.05**, enforced three ways: a worst-case preflight (max input + `max_tokens` × profile price ≤ cap),
    a workspace spend cap set to $0.05 for the smoke workspace, and `max_tokens` ≤ 256.
  - **Scope**: one playground stream plus one published cURL call. No real eval run, because the judge doubles calls
    and a 3-case run alone could approach the cap.
  - **Evidence retained**: run ids, the usage frames, and the recorded `cost_usd` versus the preflight estimate.
  - **Needs**: the owner captures the provider key in the dev workspace (feature 014 flow). I won't handle the raw
    key.
- **Rejected**: requiring a real key for every live check (paid tests), and mocking at the FE (proves nothing about
  the wire).

## Part 3 — Owner items (answered 2026-11-16 at plan review)

| # | Question | Owner answer |
|---|----------|--------------|
| O1 | Draft run (D3) vs "Try draft = create version + run" | **Draft run** (D3 transient snapshot) |
| O2 | Baseline freeze permission | **Admin** (`eval.govern`) |
| O3 | Extra fields on the eval-run body | **Silently ignore** (`extra="ignore"`) |
| O4 | Eval suite cap per synchronous run | **25 cases** |
| O5 | Real-provider smoke | **Run once**, approved model, hard cap ≤ $0.05 (D22) |
| §9 | FE test-first exception (lint + build + recorded live checks instead of FE unit tests) | **Approved** (recommended option taken per owner instruction at /speckit-tasks) |
