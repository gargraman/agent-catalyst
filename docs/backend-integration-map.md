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
| `lib/org.ts:ORG` | GET /v1/branding (or deploy env/config) | - | - | - | **Per-deployment org branding** (orgName, productName, idpName, email/docs domain, logo). One deployment per org; branding resolved at deploy/build time. Sits **above** workspace tenancy (not tenant-scoped). Color/radius/font tokens live in `app/brand.css`; default tenant is seed-derived (`iam.ts:DEFAULT_WORKSPACE_ID`), not branding. See `docs/backend/13-branding.md`. |
| `lib/auth.tsx:MOCK_PROFILE` | OIDC session | session | - | - | Replace mock profile with Auth.js + Trellix IAM OIDC; `useAuth`/`useWorkspace`/`usePrincipal` surface unchanged. |
| `lib/iam.ts:WORKSPACES/TEAMS/MOCK_PROFILE` | GET (org Okta + app roster) | session | - | - | Identity + teams (Okta `groups` claim); workspaces carry `access` (open/restricted); role-per-workspace is the **app-managed** membership roster (invite / join-open). |
| `lib/store.ts:setActiveWorkspace` | session / persist | session | yes | - | Active workspace from session; visible list = the user's Okta group memberships (platform-admins see all). No self-join — membership is group-driven. |
| `lib/store.ts:setRoleOverride/setTeamOverride` | remove | - | - | - | Dev-only preview switches; delete with real IAM (also the switcher UI in `app-shell.tsx`). |
| `app/(app)/layout.tsx`, `components/app-boundary.tsx` | gate | session | yes | - | No-access / space-picker screens driven by the Okta session + app roster; picker offers **Join** on open spaces (no member *and* no open space → no access). |

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

## Adoption, governance & notifications (P0 — net-new FE)

These close the three P0 gaps (`backend/14`,`15`,`16`). No FE symbol exists yet — each needs a new store
hook + an inline `@backend` marker when the FE surface is built (follow-up, not part of the docs pass).

| Source (`file:symbol`) | Method / Path | Auth | Tenant | Async | Notes |
|---|---|---|---|---|---|
| *(net FE)* registration gate | POST /v1/agents/:id/register | session | yes | - | Sets `registered_at` + owner; audited `agent.register`. Build/publish unregistered → 409 `registration.gate` (14 §2). |
| *(net FE)* dedup scanner | POST /v1/agents/scan | session | yes | - | Similarity scan over a draft config → candidate matches; reuse or audited `dedup.override` (14 §3). |
| *(net FE)* import | POST /v1/agents/import | session | yes | yes | Bulk import a hand-wired agent → draft + first version; validates profile coverage (14 §4). |
| *(net FE)* org estate report | GET /v1/org/agents | **platform-admin** | **cross-tenant (elevated)** | - | Whole-estate rows; metadata-only for `private`; elevated audited path (15 §2). |
| *(net FE)* org spend / compliance | GET /v1/org/spend · /v1/org/compliance | **platform-admin** | **cross-tenant (elevated)** | - | Spend rollup; non-compliant list (unregistered/un-evaluated/over-budget/no-owner) (15 §3). |
| *(net FE)* owner inbox | GET /v1/notifications?workspace=:id | session | yes | - | Owner notifications + unread count; filtered to `recipient_subject` (16). Reuses Toast/Activity FE primitives. |
| *(net FE)* mark read | POST /v1/notifications/:id/read · /read-all | session | yes | - | Mark one/all read (16 §4). |

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

## Skills

> A **skill** is a reusable, versioned capability package = instruction fragment (injected into the prompt
> like the system prompt) + bundled reference files. **Peer to Knowledge Bases, not a tool: it does NOT
> execute code** — no sandbox/egress/approval, so no test-invoke/risk-tier here. Signed at publish; only
> signed skills are bindable; a bound skill's content folds into the version `snapshot_hash` (05 §6). These
> rows are **mocked** (the FE Skills mock exists), not net-new FE. See `backend/01 §4.4`, `backend/08 Skills`.

| Source (`file:symbol`) | Method / Path | Auth | Tenant | Async | Notes |
|---|---|---|---|---|---|
| `lib/store.ts:useSkills` | GET /v1/skills?workspace=:id | session | yes | - | List skills (instruction+resource packages). **Mocked.** |
| `lib/store.ts:useSkill` | GET /v1/skills/:id | session | yes | - | Single skill (instructions, resources, signing/provenance, access). **Mocked.** |
| `lib/store.ts:addSkill` | POST /v1/skills | session (builder+ `skill.manage`) | yes | - | Create a draft skill (instruction + bundled resources). No credential capture. **Mocked.** |
| `lib/store.ts:updateSkill` | PUT /v1/skills/:id | session (builder+ `skill.manage`) | yes | - | Edit; a content change re-freezes `content_hash` → bound agents `needsReEval` (05 §5). **Mocked.** |
| `lib/store.ts:publishSkill` | POST /v1/skills/:id/publish | session (builder+ `skill.manage`) | yes | - | Sign + publish so the skill becomes bindable (stamps signature/signed_by/signed_at). Audited `skill.publish`. **Mocked.** |
| `lib/store.ts:deleteSkill` | DELETE /v1/skills/:id | session (builder+ `skill.manage`) | yes | - | Delete a skill. **Mocked.** |
| bind via `updateAgent({skills})` | POST /v1/versions/:vid/skills | session | yes | - | Bind skills to a version (bind→freeze, mirrors `.../tools`); verifies each is published+signed, pins `skill_content_hash` into `snapshot_hash`. FE binds through `AgentPatch.skills`. **Mocked** (via `updateAgent`). |

## Tools & MCP

| Source (`file:symbol`) | Method / Path | Auth | Tenant | Async | Notes |
|---|---|---|---|---|---|
| `lib/store.ts:useTools` | GET /v1/tools?workspace=:id | session | yes | - | Tool repository. |
| `lib/store.ts:addTool` | POST /v1/tools | session | yes | - | Register HTTP/built-in/sub-agent tool + schema. HTTP carries `httpConfig{method,url,auth,authRef,headers,argsSchema}`; secret → vault, only `authRef` persisted. |
| `lib/store.ts:updateTool` · `deleteTool` | PUT/DELETE /v1/tools/:id | session (builder+) | yes | - | Edit/remove a repo tool. RBAC = **builder+** (`tool.manage`, reconciled 09 / spec B12). Audited `tool.*`. |
| `lib/store.ts:useMcpServers` | GET /v1/mcp-servers?workspace=:id | session | yes | - | Servers + discovered tools. |
| `lib/store.ts:useMcpServer` | GET /v1/mcp-servers/:id | session | yes | - | Single server. |
| `lib/store.ts:addMcpServer` | POST /v1/mcp-servers | session | yes | yes* | Connect; server runs tool discovery and returns the tool list. |
| `lib/store.ts:updateMcpServer` · `deleteMcpServer` | PUT/DELETE /v1/mcp-servers/:id | session (builder+) | yes | - | Edit/remove a server. RBAC = **builder+** (`mcp.manage`). Audited `mcp.*`. |
| `lib/store.ts:reconnectMcpServer` | POST /v1/mcp-servers/:id/discover | session (builder+) | yes | yes | Re-run MCP `tools/list` discovery; refreshes the cached catalog + health. |
| `lib/store.ts:setMcpToolEnabled` | PATCH mcp-tool enabled toggle (part of PUT /v1/mcp-servers/:id) | session (builder+) | yes | - | Allow-list toggle for a discovered MCP tool (not all-by-default, spec B7). |
| *(net FE)* tool dry-run | POST /v1/tools/:id/test-invoke | session (builder+) | yes | yes | Dry-run a repo tool against sample args in the sandbox (11 §1); no FE mock yet. |
| *(net FE)* MCP dry-run | POST /v1/mcp-servers/:id/test-invoke | session (builder+) | yes | yes | Dry-run a discovered MCP tool (discover→select→bind→invoke round-trip, spec D). No FE mock yet. |
| approvals inbox | GET /v1/approvals?workspace=:id | session (publisher+ `tool.approve`) | yes | - | Pending/decided tool-call approvals (`tool_approvals`, 01); pairs with the `approval_requested` notification (16 §2.6). **Mocked:** `useApprovals` → `/approvals` page. |
| approval decide | POST /v1/approvals/:id/decide | session (publisher+ `tool.approve`) | yes | - | Approve/deny a paused run's tool call → resumes `runtime` (03). Audited. **Mocked:** `decideApproval`. |

> **Field notes (new shapes ahead of the backend).** `McpServer.{egress, approval, credentials, auth(enum)}`
> — per-server `egress` allow-list (deny-by-default, 09 §10 / 01 §4.6), an `approval` policy, multi-secret
> `credentials`, and an `auth` enum (none/bearer/oauth/headers). `McpTool.riskTier` and
> `RepoTool.{subagentTarget, builtinId, riskTier}` (`tools.ts`) carry the risk tier that drives the
> approval gate + the sub-agent/built-in binding target. `HttpToolConfig.egress` mirrors the per-server
> egress field for HTTP tools. **Agent tool bindings encode MCP tools as `mcp:<serverId>/<tool>` refs**
> (`tools.ts:mcpRef`/`resolveToolRef`). `RepoTool.{maxDepth, maxFanout}` carry the sub-agent delegation caps
> (`subagent_configs`, 01). `Run.source` now includes `tool_test` / `subagent` values (mocked in `data.ts` +
> the Runs source filter)
> once dry-run + sub-agent runs are surfaced on the Runs page.

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
| `lib/store.ts:addEvalRun` | POST /v1/agents/:id/evals | session | yes | yes | Queue eval vs frozen baseline + judge. (Mock applies the lifecycle transition synchronously since mock evals are born resolved; server does it on completion — see `resolveEvalRun`.) |
| `lib/store.ts:resolveEvalRun` | (job result) | session | yes | yes | Completion patches the run row via poll/stream **and transitions the agent lifecycle**: failing verdict gates a pre-publish agent (draft → eval_failing), passing verdict clears it (eval_failing → draft). Published/archived agents untouched (pinned live version is immutable; regressions surface via `drift`). |
| `evaluations/page.tsx:runEval` | (job result) | session | yes | yes | `Math.random` result values are mock; replace with the job result. |
| `components/eval-explainer.tsx:generateStarterEval` | POST /v1/agents/:id/evals | session | yes | yes | Eval-onboarding "Generate starter eval set" reuses the eval endpoint (mock passing cases today). |
| `components/eval-explainer.tsx:rollbackToBaseline` | PATCH /v1/agents/:id (activeVersion) | session | yes | - | Eval-failing remedy "Roll back to {baseline}": a compound re-point that sets activeVersion → baseline, status → published, and drift → none in one PATCH. Intentionally **bypasses the publish eval-gate** (it serves a known-passing version), so it does **not** go through the guarded `publishAgent`/`rollbackVersion`. The explainer/banner/dialog copy + per-dimension deltas are client-only (from the `EvalRun` fields). |

## Runs & metrics

| Source (`file:symbol`) | Method / Path | Auth | Tenant | Async | Notes |
|---|---|---|---|---|---|
| `lib/store.ts:useRuns` / `useRunsForAgent` | GET /v1/runs?workspace=:id | session | yes | - | Caller/tenant-attributed runs. `source` includes `Channel` (messaging-app traffic). |
| `lib/store.ts:addRun` | none (server-recorded) | - | yes | - | Runs are emitted by `runtime.invoke()` server-side (playground/API/eval/channel). FE-only action so the channel "send test" surfaces a run; remove once runs come from the API. |
| `dashboard/page.tsx`, `runs/page.tsx`, `evaluations/page.tsx` charts + stat tiles | GET /v1/metrics?workspace=:id (ts) | session | yes | - | Now **per-workspace selectors** `spendSeries`/`runsSeries`/`latencySeries`/`activityFor`/`dashStats`/`runsStats`/`evalStats(ws)` over `data.ts` `*_BY_WS` maps -> workspace-scoped metrics API (replaces the old flat consts). |

## Access policy (per-resource)

| Source (`file:symbol`) | Method / Path | Auth | Tenant | Async | Notes |
|---|---|---|---|---|---|
| `lib/store.ts:setAccessPolicy` | PUT /v1/{agents\|knowledge\|tools\|mcp\|channels\|skills}/:id/access-policy | session (admin) | yes | - | Persist policy; server enforces on read/manage and at invoke. |
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
| `lib/store.ts:useWorkspaces` | GET /v1/workspaces (user's Okta group memberships) | session | yes | - | Store-backed registry (mutable in the mock); real list = workspaces whose backing Okta group the user is in (platform-admins see all). |
| `lib/store.ts:useWorkspaceSettings` | GET /v1/workspaces/:id | session | yes | - | Slug read-only identity; name / backing okta_group / member_role / default provider+model editable. |
| `lib/store.ts:updateWorkspace` | PATCH /v1/workspaces/:id | session (**platform-admin**) | yes | - | Saves identity (name, `okta_group`, `member_role`); slug/id immutable. No more `access_mode`. |
| `lib/store.ts:updateWorkspaceSettings` | PATCH /v1/workspaces/:id | session (admin) | yes | - | Saves default provider/model; slug immutable. |
| `lib/store.ts:addWorkspace` | **no endpoint** (org-provisioned) | session (**platform-admin**) | **no** | - | **Mock-only** self-serve create: binds an Okta group + member role, seeds config + a starter kit. A real deploy adds `POST /v1/workspaces` (platform-admin) or keeps provisioning out-of-band. |
| `lib/store.ts:setWorkspaceActive` | POST /v1/workspaces/:id/archive (deactivate) **+ NEW reactivate endpoint** | session (**platform-admin**) | partial | - | Reversible Inactive toggle via `archived_at`; deactivate exists (unpublishes endpoints), reactivate needs a new endpoint. Drives the FE read-only freeze. |
| `lib/store.ts:deleteWorkspace` | **no endpoint** (backend archives) | session (**platform-admin**) | **no** | - | **Mock-only** hard-delete cascade: removes the space + every scoped resource. A real deploy would add a guarded platform-admin cascade or disallow it in favor of archive. |
| `lib/store.ts:useLimits` / `updateLimits` | GET/PUT /v1/workspaces/:id/limits | session (admin) | yes | - | Per-workspace spend + max-concurrency + timeout. **Stored P1, enforced P2** (backend/07). |
| `lib/store.ts:useTokens` | GET /v1/agents/:id/tokens | session | yes | - | Prefixes only, never the secret. |
| `lib/store.ts:addToken` | POST /v1/agents/:id/tokens | session (publisher) | yes | - | Caller token bound to a published endpoint, shown once. |
| `lib/store.ts:revokeToken` | DELETE /v1/tokens/:id | session (publisher) | yes | - | Immediate revoke. |
| `lib/store.ts:useProviderKeys` | GET /v1/provider-keys?workspace=:id | session | yes | - | Masked. |
| `lib/store.ts:addProviderKey` | POST /v1/provider-keys | session (admin) | yes | - | Stored in vault; value never returned. |
| `lib/store.ts:rotateProviderKey` / `revokeProviderKey` | POST /v1/provider-keys/:id/rotate\|revoke | session (admin) | yes | - | Rotate replaces the vaulted secret + mask; revoke tombstones (status=revoked). Audited (09 §12). |
| `lib/store.ts:useMembers` | GET /v1/members?workspace=:id | session | yes | - | **READ-ONLY** roster projected from the workspace's backing Okta group (role = the workspace's `member_role`). No in-app invite/remove — membership is managed in Okta. |
| `settings/page.tsx:RolesAccess` → `lib/store.ts:setAccessPolicy` | PUT /v1/{agents\|knowledge\|tools\|mcp\|channels\|skills}/:id/access-policy | session (admin) | yes | - | Roles & access tab EDITS per-resource policies across all kinds (agents/knowledge/tools/mcp/channels/skills) via `AccessPolicyEditor`. |

## Guardrails & search

| Source (`file:symbol`) | Method / Path | Auth | Tenant | Async | Notes |
|---|---|---|---|---|---|
| `agents/[id]` Guardrails tab | GET/PUT /v1/agents/:id/guardrails | session | yes | - | Stage toggles persist via `updateAgentConfig.guardrails` (draft); per-version guardrail config, frozen on publish. |
| `app-shell.tsx` + `agents/page.tsx` search | GET /v1/search | session | yes | - | Topbar + list search inputs are non-functional today. |

---

*MCP "Async" is the discovery step; the connect call itself returns the server + discovered tools.
