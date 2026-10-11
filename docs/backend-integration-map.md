# Backend integration map

The frontend is currently **mock-only**: reads come from seeds in `frontend/src/lib/data.ts` through a
client store (`frontend/src/lib/store.ts`), writes are in-memory store mutations, auth/tenancy is a
mock IAM profile (`frontend/src/lib/auth.tsx` + `iam.ts`), and the runtime token check is a
deterministic decode (`frontend/src/lib/runtime-access.ts`).

This file is the index of every place that needs a real backend call. The code itself carries the
same markers inline:

```
grep -rn "@backend" frontend/src
```

- **Authoritative markers** live at the data layer (one per store hook/action and per lib mock) - that
  is where the fetch/mutation gets written.
- **Page markers** are a header comment block in each page file listing that page's integration points
  and pointing at the lib function it calls.

For full endpoint/architecture/deployment detail see **`backend-integration-plan.md`**; for the
auth/access model see **`frontend-auth-access-plan.md`**. This file does not duplicate those - it maps
code location -> contract.

Legend: **Auth** = console session (signed-in user, RBAC) vs caller token (runtime invoke). **Tenant**
= scoped to the active workspace / caller tenant. **Async** = returns a job and streams/polls status.

---

## Auth, session & tenancy

| Source (`file:symbol`) | Method / Path | Auth | Tenant | Async | Notes |
|---|---|---|---|---|---|
| `lib/auth.tsx:MOCK_PROFILE` | OIDC session | session | - | - | Replace mock profile with Auth.js + Trellix IAM OIDC; `useAuth`/`useWorkspace`/`usePrincipal` surface unchanged. |
| `lib/iam.ts:WORKSPACES/TEAMS/MOCK_PROFILE` | GET (IAM directory) | session | - | - | Workspaces, teams, role-per-workspace memberships from IAM for this registered product. |
| `lib/store.ts:setActiveWorkspace` | session / persist | session | yes | - | Active workspace from session; list from IAM memberships. |
| `lib/store.ts:setRoleOverride/setTeamOverride` | remove | - | - | - | Dev-only preview switches; delete with real IAM (also the switcher UI in `app-shell.tsx`). |
| `app/(app)/layout.tsx`, `components/app-boundary.tsx` | gate | session | yes | - | No-access / workspace-picker screens driven by OIDC session + memberships. |

## Agents

| Source (`file:symbol`) | Method / Path | Auth | Tenant | Async | Notes |
|---|---|---|---|---|---|
| `lib/store.ts:useAgents` | GET /v1/agents?workspace=:id | session | yes | - | List; server applies access-policy visibility. |
| `lib/store.ts:useAgent` | GET /v1/agents/:id | session | yes | - | Config, versions, bound knowledge/tools. |
| `lib/store.ts:addAgent` | POST /v1/agents | session | yes | - | Create draft; used by `agents/new`. |
| `lib/store.ts:updateAgent` | PATCH /v1/agents/:id | session | yes | - | Generic field patch (name/description/tags) + bind knowledge/tools. Config params go through `updateAgentConfig`. |
| `lib/store.ts:updateAgentConfig` | PATCH /v1/agents/:id | session | yes | - | Draft config: model params, prompt, **input variables (io_contract)**, memory, retrieval, guardrails, spend. Frozen into the version snapshot on publish. |
| `lib/store.ts:publishAgent` / `unpublishAgent` / `archiveAgent` / `restoreAgent` | PATCH /v1/agents/:id | session | yes | - | Guarded lifecycle wrappers. `publishAgent` is blocked when the agent is `eval_failing` (requires a passing eval). |
| `lib/store.ts:rollbackVersion` | PATCH /v1/agents/:id (activeVersion) | session | yes | - | Repoint the live version; validates the label against known versions. |
| `lib/store.ts:deleteAgent` | DELETE /v1/agents/:id | session | yes | - | Delete agent + versions (server cascade). Mock also scrubs eval runs, runs, tokens, and channel `agentIds` references. |
| `lib/store.ts:duplicateAgent` | POST /v1/agents/:id/duplicate | session | yes | - | Server copies config to a new draft. |
| `agents/[id]` Configure → Input variables | (io_contract on PATCH / version create) | session | yes | - | Editable schema builder (name/type/required/default); persists via `updateAgentConfig.inputVariables`. Maps to `agent_versions.io_contract` (spec B2.5). |
| `agents/[id]` Versions tab | POST version / PATCH baseline | session | yes | - | Rollback is wired (`rollbackVersion`); "Save as new version" + "Re-freeze baseline" are mock toasts today. |
| `agents/[id]` Model + `agents/new` | GET /v1/models | session | - | - | Provider/model list (`MODELS` seed). |

## Knowledge

| Source (`file:symbol`) | Method / Path | Auth | Tenant | Async | Notes |
|---|---|---|---|---|---|
| `lib/store.ts:useKnowledgeBases` | GET /v1/knowledge-bases?workspace=:id | session | yes | - | List. |
| `lib/store.ts:useKnowledgeBase` | GET /v1/knowledge-bases/:id | session | yes | - | Sources + stats. |
| `lib/store.ts:addKnowledgeBase` | POST /v1/knowledge-bases | session | yes | - | Embedding model, chunking. |
| `lib/store.ts:addDataSource` | POST /v1/knowledge-bases/:id/sources | session | yes | yes | Attach source + kick ingestion job. Connector sources carry `config{connector,fields,credentialRef}`. |
| `lib/store.ts:reingest` | POST /v1/knowledge-bases/:id/reingest | session | yes | yes | Re-run ingestion. |
| `lib/store.ts:useIngestionJobs` | GET /v1/ingestion-jobs (poll/SSE) | session | yes | yes | Job status stream. |
| `components/source-picker.tsx` | GET /v1/connectors · POST /v1/connectors/credentials · upload | session | yes | - | GET returns the connector registry (field specs per connector); credential capture POSTs secret/file → vault → `{masked,vaultPath}` (FE masks as a mock stand-in); file upload needs presign. |

## Tools & MCP

| Source (`file:symbol`) | Method / Path | Auth | Tenant | Async | Notes |
|---|---|---|---|---|---|
| `lib/store.ts:useTools` | GET /v1/tools?workspace=:id | session | yes | - | Tool repository. |
| `lib/store.ts:addTool` | POST /v1/tools | session | yes | - | Register HTTP/built-in/sub-agent tool + schema. HTTP carries `httpConfig{method,url,auth,authRef,headers,argsSchema}`; secret → vault, only `authRef` persisted. |
| `lib/store.ts:useMcpServers` | GET /v1/mcp-servers?workspace=:id | session | yes | - | Servers + discovered tools. |
| `lib/store.ts:useMcpServer` | GET /v1/mcp-servers/:id | session | yes | - | Single server. |
| `lib/store.ts:addMcpServer` | POST /v1/mcp-servers | session | yes | yes* | Connect; server runs tool discovery and returns the tool list. |

## Channels (messaging apps — P2)

> Let people talk to hosted agents from Slack/Teams/Google Chat. One channel → one workspace → many
> agents. Inbound adapter + Okta federation are **P2** (see `backend/12-channels.md`); this FE config +
> multi-secret credential capture lands ahead of it. Design: `channels-design.md`.

| Source (`file:symbol`) | Method / Path | Auth | Tenant | Async | Notes |
|---|---|---|---|---|---|
| `lib/channels.ts:CHANNEL_DEFS` | GET /v1/channel-defs | session | - | - | Per-platform config + **multi-secret** credential field specs + verification scheme (UI renders from this). |
| `lib/store.ts:useChannels` | GET /v1/channels?workspace=:id | session | yes | - | List connected channels for active workspace. |
| `lib/store.ts:useChannel` | GET /v1/channels/:id | session | yes | - | Single channel (routing, bound agents, masked creds, status). |
| `lib/store.ts:useChannelsForAgent` | GET /v1/agents/:id/channels | session | yes | - | Channels routing to an agent (agent-detail cross-link). |
| `lib/store.ts:addChannel` | POST /v1/channels | session | yes | - | Connect; raw secrets POSTed once to the vault (only masked refs persist); server registers the inbound webhook + returns its ingress URL. Credentials are a `Record<field,{masked,vaultPath}>` (multi-secret). |
| `lib/store.ts:updateChannel` | PATCH /v1/channels/:id | session | yes | - | Edit routing / bound agents / Okta group / status (pause-resume) / rotate creds. |
| `lib/store.ts:deleteChannel` | DELETE /v1/channels/:id | session | yes | - | Disconnect + revoke vaulted creds. |
| `channels/{new,[id]}` ingress (server-only) | POST /channels/{slack\|teams\|gchat}/events/:id | caller (webhook sig) | yes | yes | Inbound platform events: verify signature → federate user→Okta (mint short-lived token) → route→agent → `runtime.invoke()` → reply. Not a FE call; see `backend/12-channels.md`. |

## Evaluations

| Source (`file:symbol`) | Method / Path | Auth | Tenant | Async | Notes |
|---|---|---|---|---|---|
| `lib/store.ts:useEvalRuns` / `useEvalRunsForAgent` | GET /v1/evals / /v1/agents/:id/evals | session | yes | - | List. |
| `lib/store.ts:addEvalRun` | POST /v1/agents/:id/evals | session | yes | yes | Queue eval vs frozen baseline + judge. |
| `lib/store.ts:resolveEvalRun` | (job result) | session | yes | yes | Completion patches the run row via poll/stream. |
| `evaluations/page.tsx:runEval` | (job result) | session | yes | yes | `Math.random` result values are mock; replace with the job result. |
| `components/eval-explainer.tsx:generateStarterEval` | POST /v1/agents/:id/evals | session | yes | yes | Eval-onboarding "Generate starter eval set" reuses the eval endpoint (mock passing cases today). |
| `components/eval-explainer.tsx:rollbackToBaseline` | PATCH /v1/agents/:id (activeVersion) | session | yes | - | Eval-failing remedy "Roll back to {baseline}" re-points via the agent PATCH (see Agents). The explainer/banner/dialog copy + per-dimension deltas are client-only (from the `EvalRun` fields). |

## Runs & metrics

| Source (`file:symbol`) | Method / Path | Auth | Tenant | Async | Notes |
|---|---|---|---|---|---|
| `lib/store.ts:useRuns` / `useRunsForAgent` | GET /v1/runs?workspace=:id | session | yes | - | Caller/tenant-attributed runs. `source` includes `Channel` (messaging-app traffic). |
| `lib/store.ts:addRun` | none (server-recorded) | - | yes | - | Runs are emitted by `runtime.invoke()` server-side (playground/API/eval/channel). FE-only action so the channel "send test" surfaces a run; remove once runs come from the API. |
| `dashboard/page.tsx`, `runs/page.tsx`, `evaluations/page.tsx` charts + stat tiles | GET /v1/metrics?workspace=:id (ts) | session | yes | - | Now **per-workspace selectors** `spendSeries`/`runsSeries`/`latencySeries`/`activityFor`/`dashStats`/`runsStats`/`evalStats(ws)` over `data.ts` `*_BY_WS` maps -> workspace-scoped metrics API (replaces the old flat consts). |

## Access policy (per-resource)

| Source (`file:symbol`) | Method / Path | Auth | Tenant | Async | Notes |
|---|---|---|---|---|---|
| `lib/store.ts:setAccessPolicy` | PUT /v1/{agents\|knowledge\|tools\|mcp\|channels}/:id/access-policy | session (admin) | yes | - | Persist policy; server enforces on read/manage and at invoke. |
| `lib/access-policy.ts:canUse/canManage` | (server-enforced) | both | yes | - | Mirror the checks server-side; client use is UI gating only. |

## Runtime / invoke

> **Session & memory are designed but not implemented** — the invoke contract has an optional `session_id`
> (server-side LangGraph checkpointer memory), but the FE export snippet omits it and the Playground is
> ephemeral. The Configure → *Context & memory* card now captures memory settings as config but does not
> enforce them. See **`docs/session-memory-status.md`** for the full status, the open contract question,
> and the future-pickup checklist.

| Source (`file:symbol`) | Method / Path | Auth | Tenant | Async | Notes |
|---|---|---|---|---|---|
| `lib/runtime-access.ts:resolveRun/decodeToken` | POST /v1/agents/:id/invoke (auth) | caller token | yes | - | Validate caller JWT (tenant/role/teams/scopes/expiry/revocation); compute effective access server-side. Client decode is Playground preview only. |
| `agents/[id]` Playground `send()` | POST /v1/agents/:id/invoke (stream) | caller token | yes | stream | Mock reply today; real streaming invoke. Shared `components/playground.tsx`. |
| `app/(app)/playground/page.tsx` + `components/playground.tsx:send()` | POST /v1/agents/:id/invoke (stream) | caller token | yes | stream | Standalone Playground route (agent+version via URL params); same invoke path + run-context (my-session vs provided token) as the agent-detail tab. |
| `agents/[id]` Publish & Export | GET endpoint + OpenAPI/snippets | session | yes | - | Snippets generated from the published spec. |

## Export, tokens, provider keys, members

| Source (`file:symbol`) | Method / Path | Auth | Tenant | Async | Notes |
|---|---|---|---|---|---|
| `lib/store.ts:useTokens` | GET /v1/agents/:id/tokens | session | yes | - | Prefixes only, never the secret. |
| `lib/store.ts:addToken` | POST /v1/agents/:id/tokens | session | yes | - | Caller token, shown once. |
| `lib/store.ts:revokeToken` | DELETE /v1/tokens/:id | session | yes | - | Immediate revoke. |
| `lib/store.ts:useProviderKeys` | GET /v1/provider-keys?workspace=:id | session | yes | - | Masked. |
| `lib/store.ts:addProviderKey` | POST /v1/provider-keys (+rotate/revoke) | session (admin) | yes | - | Stored in vault; value never returned. |
| `lib/store.ts:useMembers` | GET /v1/members?workspace=:id | session | yes | - | IAM-sourced, read-only; Invite deep-links to IAM (no POST). `addMember` is unused and can be removed. |

## Guardrails & search

| Source (`file:symbol`) | Method / Path | Auth | Tenant | Async | Notes |
|---|---|---|---|---|---|
| `agents/[id]` Guardrails tab | GET/PUT /v1/agents/:id/guardrails | session | yes | - | Stage toggles persist via `updateAgentConfig.guardrails` (draft); per-version guardrail config, frozen on publish. |
| `app-shell.tsx` + `agents/page.tsx` search | GET /v1/search | session | yes | - | Topbar + list search inputs are non-functional today. |

---

*MCP "Async" is the discovery step; the connect call itself returns the server + discovered tools.
