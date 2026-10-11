# People Ops (non-AI) workspace — design & implementation plan

- **Status:** Implemented — shipped in `640453f` (People Ops workspace + per-workspace
  metrics + standalone Playground). The eval-failure explainer/remedies that `resume-screener`
  (`eval_failing`) exercises landed as a follow-on in `4a397e2`.
- **Date:** 2026-10-05
- **Author:** Raman Garg (with Claude Code)
- **Scope of change:** frontend seed data + a bounded chart/stat refactor + one
  template bug fix. No backend (the app is mock/seeded today).
- **Shipped delta vs this plan:** the third HR MCP server shipped as **Slack (People)**
  (`slack-hr-mcp`), not Google Drive as drafted in §1b below (the `gdrive-mcp` seed stayed
  in another workspace). Everything else landed as planned.

---

## Background / motivation

**Agent Catalyst** is an internal platform to build, evaluate, publish and govern LLM
agents. All data is currently mock/seeded in a client store (`src/lib/data.ts` +
`src/lib/store.ts`); `@backend` markers indicate where REST calls will replace seeds.

A two-persona usability review (AI-expert builder vs. non-AI automator in HR / Ops /
CXO) surfaced one dominant problem for the non-AI persona: **the entire seed world is
Trellix security/SOC** — default workspace "Trellix XDR", templates like "Alert
triage", seed agents all SOC/eng (SOC Triage Copilot, Threat Intel Summarizer, …),
default prompts citing MITRE/CVE/XDR. A non-technical automator opens the product and
sees someone else's tool. That "this isn't for me" reaction is the single biggest
turn-off, and it's a *data/representation* problem, not an architecture problem.

**Chosen fix:** the app already has a multi-workspace (tenancy) concept
(Trellix XDR / GRC / Labs). Keep the security workspace (it's the strong demo for the
AI-expert persona) and **add a parallel "Trellix People Ops" workspace** seeded with
relatable HR use cases. The demo user switches in as Admin and sees themselves.

**What this deliberately does NOT fix** (tracked separately, larger passes):
- The conceptual-vocabulary wall (embedding model, top-k, reasoning effort, drift,
  eval gate are shown as bare labels with no glossary/tooltips).
- The "build an agent" vs "automate my task" mental-model mismatch.
- SOC-flavored new-agent templates + generic default prompt (would need outcome-based,
  non-security templates).

---

## Decisions log

| Decision | Choice | Rationale |
|---|---|---|
| Approach | Add a new workspace (don't reskin XDR) | Preserves the security demo for persona 1; reuses existing tenancy plumbing |
| Domain | People Ops / HR | Most relatable to persona 2 |
| Demo user's role in new workspace | Admin | No gating friction; showcase full product |
| Leaking global charts/stats | Make workspace-aware | Otherwise HR workspace shows SOC spend/activity/latency |
| Global tables (ingestion jobs, Settings members/tokens/keys) | NOT changed | Accepted residual; deferred to keep scope tight |

---

## Key mechanics (from code investigation — so an implementer needn't re-trace)

**Tenancy** (`iam.ts`, `auth.tsx`, `store.ts`):
- Workspace shape `{ id, name, short }` (`iam.ts:11`); list at `iam.ts:13`. No slug field.
- Switcher shows only workspaces the user is a member of: `workspacesForProfile`
  filters `WORKSPACES` by `MOCK_PROFILE.memberships` (`iam.ts:29,40`).
- Effective role = `memberships[activeWs] ?? "viewer"` (`auth.tsx:21`), honoring the
  dev preview override.
- Seeds carry no explicit `workspaceId`; `tagResource` stamps it at store init with
  precedence **explicit field > `WS_OVERRIDES` > `DEFAULT_WS`("trellix-xdr")**
  (`store.ts:14,16,30`). Tagged types: agents, knowledgeBases, mcpServers, tools.
  evalRuns/runs **inherit** their agent's workspace via `tenantOfAgent` (`store.ts:40,78`).
- Workspace-filtered hooks (`inWs` on `activeWorkspaceId`, `store.ts:277`): `useAgents`,
  `useKnowledgeBases`, `useMcpServers`, `useTools`, `useEvalRuns`, `useRuns`.
- **Global / NOT filtered:** `useIngestionJobs`, `useTokens`, `useProviderKeys`,
  `useMembers`, and all chart series.
- `activeWorkspaceId` is in-memory only — no persistence/migration risk.

**Seed shapes** (`data.ts`): Agent (`:58`), Version (`:47`; `VERSIONS` keyed by agent
id `:169`), KnowledgeBase/DataSource (`:201`), IngestionJob (`:253`), McpServer (`:264`),
RepoTool (`:307`, keyed by **name**, no id), EvalRun (`:318`), Run (`:334`), Member
(`:37`), ApiToken (`:357`), ProviderKey (`:364`). Reuse existing MODELS — cheap ones
exist (`claude-haiku-4-5`, `gpt-5-mini`, `llama-3.3-70b (self-host)` $0). Lookups that
must stay satisfied: `getVersions/getKb/getModel` (`data.ts:395`) and
`statusTone/verdictTone/driftTone` (exact enum strings).

---

## Audit result: side effects & dependent flows

**Correctness bug surfaced (must fix):** new-agent templates hardcode XDR ids
(`agents/new/page.tsx` L46/L56 `knowledgeBases:["product-docs"]`, L66
`tools:["http-enrich-ip","subagent-log-query"]`). `applyTemplate` sets them
unconditionally (L116-117) and `create()` binds them (L134-135); pickers are
workspace-filtered (L84-85) but there is **no reconciliation** — so creating an agent
in any non-XDR workspace silently binds XDR resources. Pre-existing; our feature
exposes it.

**Const→map refactor is bounded & shape-sensitive:** only `dashboard/page.tsx` and
`runs/page.tsx` import the 7 series/label/ACTIVITY consts. Call sites index
`*_SERIES[0].values` (dashboard L38/L50/L52, runs L57/L59), so selectors MUST return
`Series[]` (array of `{name,values}`), labels stay `string[]`, `ACTIVITY` stays
`Activity[]`. Chart components (`charts.tsx` AreaChart/BarChart/Sparkline) need no change.

**Literal stat tiles (would show XDR numbers in HR):** every `delta` and `spark` on
dashboard/runs/evaluations is literal; whole-value literals = runs `182940`/`1840`/`214.8`
(L57/59/60), evaluations `142`/`90.8%` (L131/132). Dashboard whole-values are derived
(published/spend/eval/calls) → correct per-workspace already; only its deltas+sparks are literal.

**Derived (safe, auto-correct per workspace):** dashboard counts/donut/top-agents,
evaluations pass-rate-by-agent + verdict donut + rows, runs table + success rate,
knowledge-page tiles (all from `useKnowledgeBases`). No change needed.

**Safe:** TEAMS has no exhaustive-set assumption (adding "People" only adds chips in
`access-policy-editor.tsx` L74 and `app-shell.tsx` L203).

---

## Part 1 — Mandatory changes

### 1a. Register the workspace — `src/lib/iam.ts`
- `WORKSPACES` += `{ id: "trellix-people", name: "Trellix People Ops", short: "PO" }`.
- `MOCK_PROFILE.memberships` += `"trellix-people": "admin"`.
- `TEAMS` += `"People"`; add `"People"` to `MOCK_PROFILE.teams` (enables an HR
  team-restricted access-policy demo, parity with SOC).

### 1b. Author HR seed data — `src/lib/data.ts`
Explicit `workspaceId: "trellix-people"` on every new agent/KB/tool/MCP seed
(explicit field wins in `tagResource`). evalRuns/runs inherit via `agentId`.
- **Agents (6):** `benefits-qa` (published, `claude-haiku-4-5`, KBs benefits-guide+employee-handbook),
  `pto-helper` (published, `gpt-5-mini`, tool http-pto-balance), `onboarding-concierge`
  (published, `claude-haiku-4-5`, tool http-hris-lookup), `policy-assistant` (draft,
  `claude-sonnet-5-5`, KB hr-policy-library), `resume-screener` (**eval_failing**, tool
  http-ats-lookup, **restricted accessPolicy to team "People"** for parity),
  `survey-summarizer` (archived). Reference only existing MODELS strings.
- **Versions:** a `VERSIONS["<id>"]` per agent (active label = `activeVersion`; one
  published, older deprecated; draft = single draft/none). `agents/[id]` uses
  `getVersions` from data.ts, so these are required.
- **KnowledgeBases (3):** employee-handbook (ready), benefits-guide (ready),
  hr-policy-library (indexing); realistic sources, reused embeddingModel strings,
  `boundAgents` matching usage.
- **RepoTools (3):** http-hris-lookup, http-pto-balance, http-ats-lookup (HTTP,
  workspaceId-tagged, `usedBy` matching).
- **McpServers (3):** Workday, Greenhouse, ~~Google Drive~~ **Slack (People)** (HR; see shipped
  delta above); realistic tools[]/auth; one `error` for variety.
- **EvalRuns:** one per published agent (mixed verdicts; resume-screener=`fail`; a
  first-baseline `low_confidence`).
- **Runs:** ~6-8 across published HR agents, spanning source/status, incl. one
  `blocked` ("PII blocked on input") and one `error`.
- (Optional) add HR prompts to the `PROMPTS` map in `agents/[id]/page.tsx:66`.

### 1c. Fix the cross-workspace template-binding bug — `src/app/(app)/agents/new/page.tsx`
Reconcile preset ids against the workspace-filtered pickers so no cross-workspace
(or stale) resource is ever bound:
- In `applyTemplate` (L116-117): set `knowledgeBases` to
  `p.knowledgeBases.filter(id => kbOptions.some(kb => kb.id === id))` and `tools` to
  `p.tools.filter(n => toolOptions.some(t => t.name === n))`.
- In `create()` (L134-135): apply the same intersection as a final guard.
Fixes all workspaces, not just HR (GRC/Labs have the same latent bug today).

### 1d. Make charts/stats workspace-aware — `src/lib/data.ts` + 3 pages
In `data.ts`, convert each global to a per-workspace map + selector that **preserves
shape** (return `Series[]`/`string[]`/`Activity[]`), e.g.:
```ts
export const SPEND_SERIES_BY_WS: Record<string, Series[]> = {
  "trellix-xdr":   [/* copy current values verbatim */],
  "trellix-people":[/* HR-scale values */],
};
export const spendSeries = (ws?: string) =>
  SPEND_SERIES_BY_WS[ws ?? ""] ?? SPEND_SERIES_BY_WS["trellix-xdr"];
```
Same for RUNS_SERIES, LATENCY_SERIES, ACTIVITY (labels can stay shared constants).
Add per-workspace **stat-tile maps** for the literal values/deltas/sparks on
dashboard (deltas+sparks), runs (`182940`/`1840`/`214.8` + deltas+sparks),
evaluations (`142`/`90.8%` + deltas+sparks) — e.g. `DASH_STATS_BY_WS`,
`RUNS_STATS_BY_WS`, `EVAL_STATS_BY_WS`. **Copy XDR literals verbatim** so the XDR view
is byte-for-byte unchanged (regression safety); grc/labs fall back to XDR.
In `dashboard/page.tsx`, `runs/page.tsx`, `evaluations/page.tsx`: get the active
workspace id via `useWorkspace().workspace?.id` (`@/lib/auth`) and read through the
selectors instead of the raw consts.

---

## Part 2 — Global tables: NOT changed (decision: charts/stats only)

The global, non-workspace-filtered lists stay as-is and are accepted residuals:
- **Ingestion jobs** (`useIngestionJobs`, global) — HR workspace will show the existing
  SOC ingestion jobs; HR KBs show an empty "Ingestion activity" on their detail page
  (filtered by KB name → no match).
- **Settings → Members / API tokens / Provider keys** (global) — show SOC data in HR.

**Hard constraint from this decision:** do **NOT** seed any HR rows into global lists
(ingestion jobs, members, API tokens, provider keys). Adding HR rows there would make
them appear in **every** workspace and break the "XDR byte-for-byte unchanged"
guarantee. HR seeds are limited to the workspace-filtered entity types only.

---

## Part 3 — Residuals / explicitly out of scope
- Global tables above — a clean follow-up is to make them workspace-aware (add
  `workspaceId?`, filter hooks, stamp write sites) but that's deferred.
- SOC-flavored new-agent templates + generic default prompt still show in HR.
- Vocabulary wall / "automate my task" framing (separate, larger pass).
- Cosmetic: `settings/page.tsx:48` slug literal; `runtime-access.ts` People
  token→tenant branch + an HR sample-token hint (playground-preview only).
- grc/labs trend charts fall back to XDR values (unchanged from today).

---

## Files changed
- `src/lib/iam.ts` — workspace + membership + team.
- `src/lib/data.ts` — HR seeds (workspace-filtered entity types only); per-workspace
  series/activity/stat maps + selectors.
- `src/app/(app)/agents/new/page.tsx` — template-binding reconciliation (1c).
- `src/app/(app)/dashboard/page.tsx`, `runs/page.tsx`, `evaluations/page.tsx` —
  select charts/activity/stat tiles by active workspace.
- (optional) `src/app/(app)/agents/[id]/page.tsx` — HR `PROMPTS` entries.

(`src/lib/store.ts` is intentionally untouched — no changes to global lists.)

---

## Verification
- `npm run dev`; switch to **Trellix People Ops** (badge = Admin).
- **Agents:** only the 6 HR agents; `resume-screener` shows eval-failing.
- **Dashboard:** counts/donut/top-agents reflect HR; Spend-by-provider + Recent
  activity + all deltas/sparks show HR data (no security spend/activity).
- **Runs:** HR runs table + HR trend charts + headline tiles (not `182,940`).
- **Evaluations:** HR eval runs + HR tiles (not `142`/`90.8%`).
- **Knowledge:** 3 HR KBs. (Expected residual: Ingestion jobs table still shows SOC
  jobs — global list, by design.)
- **Template-bug fix:** create an agent in HR from the default (rag) and triage
  templates → confirm it is **NOT** bound to `product-docs` or XDR tools. Repeat in
  GRC to confirm the general fix.
- **New agent pickers:** KB/tool lists show only HR resources.
- **Regression (critical):** switch back to **Trellix XDR** → every page byte-for-byte
  as before (literals copied verbatim; no HR rows in any global list). Spot-check
  GRC/Labs (fall back to XDR trend data, unchanged).
- `npm run build` and `npm run lint` pass.
