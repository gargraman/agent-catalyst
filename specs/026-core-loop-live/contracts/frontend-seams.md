# Frontend Seams — 026 Core Loop Live

**Convention** (unchanged, see research G22): each wired family gets one `loadX()` on `load()`, one adapter in
`src/lib/api/adapters.ts`, one `useEnsureX()` in its page, and typed calls in `src/lib/api/client.ts`. The live
branch is taken only when `isLiveWorkspace` / `isLiveAgent`. The mock branch is behaviourally unchanged (FR-021).
Each wired seam deletes its `warnLocalOnly` call and flips its `@backend` marker to `@backend wired(026)`.

**Counting rule (SC-002)**: a seam = one `@backend` marker whose behaviour becomes REAL. It counts only with
`npm run build` green **and** its quickstart live check recorded. The final count replaces "4 / 257" in
`docs/backend-integration-status.md` (lines 5/12/105). The **exact** marker list is pinned by
`/speckit-tasks`. The groups and minimum set are below.

| # | Family | Store fn(s) (`store.ts`) | Marker(s) to flip | Backend | UI file(s) |
|---|---|---|---|---|---|
| S1 | Agent read model | `loadAgent` (keep ETag), `agentOutToAgent` (description/provider/model/draft/draftDirty/restoreStatus) | `data.ts:108` agent shape | A GET | `agents/[id]/page.tsx` |
| S2 | Save metadata | `updateAgent` (live: PATCH + If-Match, 409 → reload dialog) | `store.ts:668` | A PATCH | page General card :353 |
| S3 | Save draft config | `updateAgentConfig` (live: PATCH `draft`) | `store.ts:695` | A PATCH | page Model/Prompt/Output :368-403 |
| S4 | Create version | `createVersion` (new; replaces the toast-only `SaveVersionDialog`) | page header block :85 (version create) | A POST versions | page :482, :1459 |
| S5 | Versions list | `getVersions` → live from `agent.versions` embed | page :85 (GET versions), `data.ts` VERSIONS | A GET | page Versions :877-910, playground page :37 |
| S6 | Re-point (rollback) | `rollbackVersion` (live: PATCH `active_version_id` by **id**, not label) | page :85 (PATCH activeVersion) | A PATCH | page :899 |
| S7 | Archive / restore | `archiveAgent` / `restoreAgent` (live: PATCH status / restore_status) | `store.ts:721` (lifecycle) | A PATCH | page :283, :498 |
| S8 | Publish (advisory) | `publishAgent` (live: POST publish, render `advisory`; drop client-side gate checks for live) | `store.ts:721` (publish TODO 724) | A publish | page :254, :522, Publish :1286 |
| S9 | Playground invoke (stream) | new `streamInvoke` (`src/lib/api/stream.ts`) + `Playground` turn state (partial / interrupted / refusal) | `playground/page.tsx:3-7` (invoke) | B | `components/playground.tsx` |
| S10 | Playground target | draft / active / version selector → `target` | `playground/page.tsx` (versions) | B | playground page :30-53 |
| S11 | Citations + cost/latency | render `citations` + `usage` frames; remove hardcoded `1.8s / 9,120 / $0.034` | `store.ts:1197-1206` addRun NOTE | B | playground.tsx :88 |
| S12 | Tokens list | `loadTokens(agentId)`, `useTokens` live | `store.ts:1147` | F | Publish :1288 |
| S13 | Token mint | `mintToken` (secret shown once, never stored) | `store.ts:1147` | F | `CreateTokenDialog` :1570 |
| S14 | Token revoke | `revokeToken` (DELETE) | `store.ts:1152` | F | Publish :1394 |
| S15 | cURL snippet | real base URL + `$AC_TOKEN` + io_contract example | page :88 (snippets) | — (G19) | Publish :1294-1299 |
| S16 | Suites list/create/rename/delete | `loadSuites(agentId)`, `addSuite`, `renameSuite`, `deleteSuite` | `store.ts:569, 578, 582` | C | page SuiteAuthoring :1006-1117 |
| S17 | Cases upsert/delete | `upsertCase`, `deleteCase` (live: unsupported assertion kinds disabled) | `store.ts:587, 599` | C | `CaseEditorDialog` :1166 |
| S18 | Run eval | `runEval` (live: POST `{version_id, suite_id}`; **no** baseline picker in live mode) | `store.ts:1105`, `evaluations/page.tsx:37-40` (POST) | E | `RunEvalDialog` :1404, evaluations page :76 |
| S19 | Eval history | `loadEvalRuns(agentId)` / workspace `loadEvalRuns()` | `evaluations/page.tsx:37` (GET), `store.ts:1140` | E | Evals tab :951, evaluations page |
| S20 | Baseline display + freeze/clear | `freezeBaseline(suiteId, runId)`, `clearBaseline` (replaces toast `reFreezeBaseline`; gate stays `eval.govern` = admin) | `store.ts:556` | D | Evals Baseline card :959 |

**Explicitly left MOCK in 026** (labelled "not available yet" in live mode, never a fake success):
- duplicate (`store.ts:809`) and delete (`store.ts:781`): no backend
- eval policy edit (`store.ts:549`): reads server policy, edit is Nice
- every other family (knowledge, tools, skills, channels, guardrails PUT, …)

## UI states required per seam (FR-019)

- loading / ready / error via `RemoteStatus` + `LiveStatus` strip. **No silent mock fallback.**
- Gating uses `useAuth().can` (server `backendRole`). The dev override must not unlock a server action. The server
  re-checks and returns 403, and the UI surfaces it.
- Playground turn states: `streaming → complete | incomplete (interrupted/error, partial kept, Retry) | refused (D16 message, no partial)`.
