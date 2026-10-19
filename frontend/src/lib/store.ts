"use client";

import { useRef, useSyncExternalStore } from "react";
import {
  AGENTS, KNOWLEDGE_BASES, INGESTION_JOBS, MCP_SERVERS, TOOLS_REPO, EVAL_RUNS, EVAL_SUITES, RUNS, TOOL_APPROVALS,
  API_TOKENS, PROVIDER_KEYS, MEMBERS, INVITES, CHANNELS_SEED, resolveConfig, getVersions,
  WORKSPACE_SETTINGS, LIMITS, defaultWorkspaceSettings, defaultLimits, defaultEvalPolicy,
  type Agent, type AgentConfig, type KnowledgeBase, type DataSource, type IngestionJob, type McpServer,
  type RepoTool, type EvalRun, type EvalSuite, type EvalCaseDef, type Run, type ToolApproval, type ApiToken, type ProviderKey, type Member, type Invite, type Channel,
  type WorkspaceSettings, type Limits, type EvalPolicy, type Verdict,
} from "./data";
import { type AccessPolicy, defaultPolicy } from "./access-policy";
import type { Role } from "./permissions";
import { DEFAULT_WORKSPACE_ID, MOCK_PROFILE } from "./iam";
import { maskSecret } from "./secrets";
import { MCP_REF_PREFIX } from "./tools";

/* ----------------------------------------------------------- tenancy seed */
const DEFAULT_WS = DEFAULT_WORKSPACE_ID;
/** A few seeds live in other workspaces so switching tenant shows a different set. */
const WS_OVERRIDES: Record<string, string> = {
  "compliance-qa": "trellix-grc",
  "onboarding-buddy": "trellix-labs",
  "policy-library": "trellix-grc",
};
/** A demo restricted policy so the Access tab has something to show. */
const POLICY_OVERRIDES: Record<string, AccessPolicy> = {
  "triage-copilot": {
    visibility: "restricted",
    useRoles: [], useTeams: ["SOC"],
    manageRoles: ["publisher", "admin"], manageTeams: ["SOC"],
  },
};

type WithTenant = { id?: string; name?: string; workspaceId?: string; accessPolicy?: AccessPolicy };
const keyOf = (r: WithTenant) => r.id ?? r.name ?? "";
function tagResource<T extends WithTenant>(r: T): T {
  const k = keyOf(r);
  return {
    ...r,
    workspaceId: r.workspaceId ?? WS_OVERRIDES[k] ?? DEFAULT_WS,
    accessPolicy: r.accessPolicy ?? POLICY_OVERRIDES[k] ?? defaultPolicy(),
  };
}
function tenantOfAgent(agentId: string, agents: Agent[]): string {
  return agents.find((a) => a.id === agentId)?.workspaceId ?? DEFAULT_WS;
}

// @backend remove - with the backend, KnowledgeBase.boundAgents is a server-computed rollup on
//   KnowledgeBaseOut (count of agents whose version binds the KB). The mock derives it client-side so the
//   count stays consistent across create/update/duplicate/delete; unchanged KBs keep their object identity.
//   [ref: backend/08-api-surface.md §3 KnowledgeBaseOut.bound_agents]
function withBoundAgentCounts(agents: Agent[], kbs: KnowledgeBase[]): KnowledgeBase[] {
  return kbs.map((kb) => {
    const count = agents.filter((a) => a.knowledgeBases.includes(kb.id)).length;
    return kb.boundAgents === count ? kb : { ...kb, boundAgents: count };
  });
}

// Recompute RepoTool.usedBy (agents referencing the tool by name) and McpServer.boundAgents (agents
// with at least one `mcp:<serverId>/…` ref) after a bind/unbind, mirroring withBoundAgentCounts.
function withBoundToolCounts(
  agents: Agent[], tools: RepoTool[], mcpServers: McpServer[],
): { tools: RepoTool[]; mcpServers: McpServer[] } {
  const nextTools = tools.map((t) => {
    const count = agents.filter((a) => a.tools.includes(t.name)).length;
    return t.usedBy === count ? t : { ...t, usedBy: count };
  });
  const nextServers = mcpServers.map((s) => {
    const prefix = `${MCP_REF_PREFIX}${s.id}/`;
    const count = agents.filter((a) => a.tools.some((ref) => ref.startsWith(prefix))).length;
    return s.boundAgents === count ? s : { ...s, boundAgents: count };
  });
  return { tools: nextTools, mcpServers: nextServers };
}

/* ----------------------------------------------------------------- state */
export type Toast = { id: string; tone: "good" | "critical" | "neutral"; message: string };

type State = {
  activeWorkspaceId: string;
  roleOverride: Role | null;
  teamOverride: string[] | null;
  agents: Agent[];
  knowledgeBases: KnowledgeBase[];
  ingestionJobs: IngestionJob[];
  mcpServers: McpServer[];
  tools: RepoTool[];
  evalRuns: EvalRun[];
  evalSuites: EvalSuite[];
  evalPolicy: Record<string, EvalPolicy>; // per-workspace; falls back to defaultEvalPolicy()
  runs: Run[];
  tokens: ApiToken[];
  providerKeys: ProviderKey[];
  members: Member[];
  invites: Invite[];
  // @backend remove - spaces the mock user self-joined this session (open spaces). Real membership is the
  //   session roster from the IAM/join endpoints; this shim merges into the effective profile in auth.tsx.
  joinedWorkspaceIds: string[];
  channels: Channel[];
  toolApprovals: ToolApproval[];
  workspaceSettings: Record<string, WorkspaceSettings>;
  limits: Record<string, Limits>;
  toasts: Toast[];
};

// @backend remove - initial state is hydrated from mock seeds (src/lib/data.ts). With the backend,
//   the store starts empty and each list hook fetches from its API; the tagResource/WS_OVERRIDES/
//   POLICY_OVERRIDES normalization and the seed arrays all go away (server owns workspaceId +
//   accessPolicy). [ref: backend-integration-plan.md#part-2]
const seedAgents = AGENTS.map(tagResource);
let state: State = {
  activeWorkspaceId: DEFAULT_WS,
  roleOverride: null,
  teamOverride: null,
  agents: seedAgents,
  knowledgeBases: withBoundAgentCounts(seedAgents, KNOWLEDGE_BASES.map(tagResource)),
  ingestionJobs: INGESTION_JOBS,
  mcpServers: MCP_SERVERS.map(tagResource),
  tools: TOOLS_REPO.map(tagResource),
  evalRuns: EVAL_RUNS.map((e) => ({ ...e, workspaceId: e.workspaceId ?? tenantOfAgent(e.agentId, seedAgents) })),
  evalSuites: EVAL_SUITES.map((s) => ({ ...s, workspaceId: s.workspaceId ?? tenantOfAgent(s.agentId, seedAgents) })),
  evalPolicy: {},
  runs: RUNS.map((r) => ({ ...r, workspaceId: r.workspaceId ?? tenantOfAgent(r.agentId, seedAgents) })),
  tokens: API_TOKENS,
  providerKeys: PROVIDER_KEYS,
  members: MEMBERS,
  invites: INVITES,
  joinedWorkspaceIds: [],
  channels: CHANNELS_SEED.map(tagResource),
  toolApprovals: TOOL_APPROVALS.map((a) => ({ ...a, workspaceId: a.workspaceId ?? tenantOfAgent(a.agentId, seedAgents) })),
  workspaceSettings: WORKSPACE_SETTINGS,
  limits: LIMITS,
  toasts: [],
};

const listeners = new Set<() => void>();
function emit() {
  listeners.forEach((l) => l());
}
function set(patch: Partial<State>) {
  state = { ...state, ...patch };
  emit();
}
function subscribe(l: () => void) {
  listeners.add(l);
  return () => listeners.delete(l);
}

export function genId(prefix: string) {
  return `${prefix}-${Math.random().toString(16).slice(2, 8)}`;
}
export const nowIso = () => new Date().toISOString();

/* --------------------------------------------------------------- toasts */
export function toast(message: string, tone: Toast["tone"] = "good") {
  const t: Toast = { id: genId("t"), tone, message };
  set({ toasts: [...state.toasts, t] });
  setTimeout(() => set({ toasts: state.toasts.filter((x) => x.id !== t.id) }), 3500);
}
export function dismissToast(id: string) {
  set({ toasts: state.toasts.filter((x) => x.id !== id) });
}

/* ------------------------------------------------------------- tenancy/auth */
// @backend session - activeWorkspaceId comes from the user's last-selected workspace (persist
//   server-side or in the session), and the workspace list from IAM memberships; keep local only
//   as a UI fallback. [ref: backend-integration-plan.md#part-4]
export function setActiveWorkspace(id: string) {
  if (id === state.activeWorkspaceId) return;
  set({ activeWorkspaceId: id });
}
// @backend POST /v1/workspaces/:id/join - self-join an `access: "open"` space; server assigns the
//   default join role (DEFAULT_JOIN_ROLE, iam.ts) and returns the new membership. The joinedWorkspaceIds
//   shim is merged into the effective profile in auth.tsx and goes away once membership is session-sourced.
export function joinWorkspace(id: string) {
  if (!state.joinedWorkspaceIds.includes(id)) {
    set({ joinedWorkspaceIds: [...state.joinedWorkspaceIds, id] });
  }
  set({ activeWorkspaceId: id });
  toast("Joined space");
}
// @backend remove - roleOverride/teamOverride are dev-only preview switches; delete once the real
//   IAM session supplies role-per-workspace + teams. [ref: frontend-auth-access-plan.md#part-e]
export function setRoleOverride(role: Role | null) {
  set({ roleOverride: role });
}
export function setTeamOverride(teams: string[] | null) {
  set({ teamOverride: teams });
}
// @backend PATCH /v1/workspaces/:id - update workspace config (name, default provider/model). Admin only.
//   Name/default provider/model are app-editable; slug is immutable (it is the endpoint URL segment).
//   [ref: backend/01 §4.1, backend/08 settings]
export function updateWorkspaceSettings(wsId: string, patch: Partial<WorkspaceSettings>) {
  const cur = state.workspaceSettings[wsId] ?? defaultWorkspaceSettings();
  set({ workspaceSettings: { ...state.workspaceSettings, [wsId]: { ...cur, ...patch } } });
  toast("Workspace settings saved");
}
// @backend POST /v1/workspaces/:id/archive - archive a workspace (unpublishes its endpoints). Admin only.
export function archiveWorkspace(wsId: string) {
  const cur = state.workspaceSettings[wsId] ?? defaultWorkspaceSettings();
  set({ workspaceSettings: { ...state.workspaceSettings, [wsId]: { ...cur, archivedAt: nowIso() } } });
  toast("Workspace archived", "neutral");
}
// @backend PUT /v1/workspaces/:id/limits - per-workspace spend ceiling + max concurrency + request
//   timeout. Admin only. Stored in P1; ENFORCED at runtime in P2 (BudgetGuard + Redis semaphores).
//   [ref: backend/01 §4.8, backend/07 §3]
export function updateLimits(wsId: string, patch: Partial<Limits>) {
  const cur = state.limits[wsId] ?? defaultLimits();
  set({ limits: { ...state.limits, [wsId]: { ...cur, ...patch } } });
  toast("Limits saved");
}
// @backend PUT /v1/workspaces/:id/eval-policy - admin-governed verdict/publish thresholds (eval.govern).
//   Gates the verdict (min cases / quality / efficiency) and the publish low-confidence override. [backend/05 §1,§5]
export function setEvalPolicy(wsId: string, patch: Partial<EvalPolicy>) {
  const cur = state.evalPolicy[wsId] ?? defaultEvalPolicy();
  set({ evalPolicy: { ...state.evalPolicy, [wsId]: { ...cur, ...patch } } });
  toast("Eval policy saved");
}
// @backend POST /v1/versions/:vid/baseline - re-freeze the trusted baseline to the active version; this is
//   explicit + audited (writes an audit_logs row) so quality can't erode one silent step at a time
//   (backend/05 §4, backend/08). Admin / eval.govern. Mock: records the action via a toast.
export function reFreezeBaseline(agentId: string) {
  const a = state.agents.find((x) => x.id === agentId);
  if (!a) return;
  toast(`Baseline re-frozen to ${a.activeVersion} for ${a.name} (audited).`);
}

/* ---------------------------------------------------- eval suite / case authoring (eval.author) */
// @backend POST /v1/agents/:id/eval-suites - create a suite (01 §4.5, 08 eval-suites). Mock: append in-memory.
export function addSuite(agentId: string, name: string, cases: EvalCaseDef[] = []): string {
  const id = genId("suite");
  const workspaceId = state.agents.find((a) => a.id === agentId)?.workspaceId ?? state.activeWorkspaceId;
  const suite: EvalSuite = { id, agentId, name: name.trim() || "Untitled suite", cases, workspaceId, updatedAt: nowIso() };
  set({ evalSuites: [...state.evalSuites, suite] });
  toast(`Suite "${suite.name}" created`);
  return id;
}
// @backend PATCH /v1/eval-suites/:sid - rename a suite.
export function renameSuite(suiteId: string, name: string) {
  set({ evalSuites: state.evalSuites.map((s) => (s.id === suiteId ? { ...s, name: name.trim() || s.name, updatedAt: nowIso() } : s)) });
}
// @backend DELETE /v1/eval-suites/:sid - delete a suite and its cases.
export function deleteSuite(suiteId: string) {
  set({ evalSuites: state.evalSuites.filter((s) => s.id !== suiteId) });
  toast("Suite deleted");
}
// @backend POST|PUT /v1/eval-suites/:sid/cases[/:cid] - create or update a case. The case object carries its
//   own freshly-cloned assertions[]/rubric[] from the editor, so this never aliases another case's arrays.
export function upsertCase(suiteId: string, c: EvalCaseDef) {
  set({
    evalSuites: state.evalSuites.map((s) => {
      if (s.id !== suiteId) return s;
      const exists = s.cases.some((x) => x.id === c.id);
      const cases = exists ? s.cases.map((x) => (x.id === c.id ? c : x)) : [...s.cases, c];
      return { ...s, cases, updatedAt: nowIso() };
    }),
  });
}
// @backend DELETE /v1/eval-suites/:sid/cases/:cid - delete a case.
export function deleteCase(suiteId: string, caseId: string) {
  set({ evalSuites: state.evalSuites.map((s) => (s.id === suiteId ? { ...s, cases: s.cases.filter((c) => c.id !== caseId), updatedAt: nowIso() } : s)) });
}
// @backend PUT /v1/{agents|knowledge|tools|mcp|channels}/:id/access-policy - persist per-resource policy;
//   server enforces it on read/manage and at invoke. [ref: frontend-auth-access-plan.md#part-b2]
/** Admin-configurable per-resource access policy (Part B2). */
export function setAccessPolicy(
  kind: "agent" | "knowledge" | "tool" | "mcp" | "channel",
  id: string,
  policy: AccessPolicy,
) {
  // A private resource is owner-scoped: stamp the current user as owner if the editor didn't supply one.
  const stamped: AccessPolicy =
    policy.visibility === "private" && !policy.owner ? { ...policy, owner: MOCK_PROFILE.email } : policy;
  const apply = <T extends WithTenant>(arr: T[]) => arr.map((r) => (keyOf(r) === id ? { ...r, accessPolicy: stamped } : r));
  if (kind === "agent") set({ agents: apply(state.agents) });
  else if (kind === "knowledge") set({ knowledgeBases: apply(state.knowledgeBases) });
  else if (kind === "tool") set({ tools: apply(state.tools) });
  else if (kind === "channel") set({ channels: apply(state.channels) });
  else set({ mcpServers: apply(state.mcpServers) });
  toast("Access policy updated");
}

/* ------------------------------------------------------------- members / invites */
// @backend POST /v1/workspaces/:id/invites - invite a member to the space (space admin → `member.manage`).
export function inviteMember(email: string, role: Member["role"]) {
  if (state.invites.some((i) => i.email === email) || state.members.some((m) => m.email === email)) {
    toast("Already a member or invited", "neutral");
    return;
  }
  const invite: Invite = { email, role, invitedBy: MOCK_PROFILE.name, invitedAt: nowIso(), status: "pending" };
  set({ invites: [invite, ...state.invites] });
  toast("Invitation sent");
}
// @backend DELETE /v1/workspaces/:id/invites/:id - revoke a pending invite (space admin).
export function revokeInvite(email: string) {
  set({ invites: state.invites.filter((i) => i.email !== email) });
  toast("Invitation revoked", "neutral");
}
// @backend DELETE /v1/members/:id - remove a member from the space (space admin).
export function removeMember(email: string) {
  set({ members: state.members.filter((m) => m.email !== email) });
  toast("Member removed", "neutral");
}

/* --------------------------------------------------------------- actions */
// @backend POST /v1/agents - create a draft agent in the active workspace; returns the created id.
export function addAgent(a: Agent) {
  const stamped = tagResource({ ...a, workspaceId: a.workspaceId ?? state.activeWorkspaceId });
  const agents = [stamped, ...state.agents];
  const toolCounts = withBoundToolCounts(agents, state.tools, state.mcpServers);
  set({
    agents,
    knowledgeBases: withBoundAgentCounts(agents, state.knowledgeBases),
    tools: toolCounts.tools,
    mcpServers: toolCounts.mcpServers,
  });
  toast(`Agent "${stamped.name}" created as a draft`);
}
// @backend PATCH /v1/agents/:id - generic field patch + bind knowledge/tools. Lifecycle transitions
//   (publish/unpublish/archive/restore) and version rollback go through the dedicated guarded wrappers
//   below (publishAgent/unpublishAgent/archiveAgent/restoreAgent/rollbackVersion), which map to the same
//   endpoint but centralize the passing-eval guard.
export function updateAgent(id: string, patch: Partial<Agent>, notify = true) {
  // A behavior-changing (trigger) field edit flags the agent for re-eval (publishable proxy, backend/05 §5),
  // unless the caller set needsReEval explicitly (e.g. a rollback that clears it).
  const TRIGGER_FIELDS: (keyof Agent)[] = ["provider", "model", "knowledgeBases", "tools"];
  const touchedTrigger = TRIGGER_FIELDS.some((k) => k in patch);
  const eff: Partial<Agent> = touchedTrigger && patch.needsReEval === undefined ? { ...patch, needsReEval: true } : patch;
  const agents = state.agents.map((a) => (a.id === id ? { ...a, ...eff, updatedAt: nowIso() } : a));
  // Recompute bound-agent counts only when a binding actually changed (bind/detach).
  const extra: Partial<State> = {};
  if ("knowledgeBases" in patch) extra.knowledgeBases = withBoundAgentCounts(agents, state.knowledgeBases);
  if ("tools" in patch) {
    const counts = withBoundToolCounts(agents, state.tools, state.mcpServers);
    extra.tools = counts.tools;
    extra.mcpServers = counts.mcpServers;
  }
  set({ agents, ...extra });
  if (notify) toast("Changes saved");
}
// @backend PATCH /v1/agents/:id - persist draft config (model params, prompt, memory, retrieval, spend).
//   Guardrails specifically map to PUT /v1/agents/:id/guardrails (per-version config) per
//   docs/backend-integration-map.md. Deep-merges into agent.config (updateAgent is shallow); the merged
//   result is a full AgentConfig stored back into the Partial<AgentConfig> field.
export function updateAgentConfig(id: string, patch: Partial<AgentConfig>, notify = true) {
  set({
    agents: state.agents.map((a) => {
      if (a.id !== id) return a;
      const cur = resolveConfig(a);
      const next: AgentConfig = {
        ...cur,
        ...patch,
        retrieval: { ...cur.retrieval, ...patch.retrieval },
        guardrails: { ...cur.guardrails, ...patch.guardrails },
        spend: { ...cur.spend, ...patch.spend },
      };
      // Any config edit except a spend-only change is a behavior (drift) trigger → needs re-eval (backend/05 §5).
      const behaviorChange = Object.keys(patch).some((k) => k !== "spend");
      return { ...a, config: next, ...(behaviorChange ? { needsReEval: true } : {}), updatedAt: nowIso() };
    }),
  });
  if (notify) toast("Changes saved");
}
// @backend PATCH /v1/agents/:id - lifecycle transitions below are dedicated wrappers over the same
//   endpoint; they centralize the guards (publish requires a passing eval) the UI previously duplicated.
/** Publish the active version. Blocked when the agent is eval_failing. Returns false if blocked. */
export function publishAgent(id: string): boolean {
  const a = state.agents.find((x) => x.id === id);
  if (!a) return false;
  if (a.status === "eval_failing") {
    toast("Publish blocked: the latest eval is failing. Run a passing eval first.", "critical");
    return false;
  }
  // The publish eval-gate (backend/05 §5): a behavior-changing edit since the last passing eval blocks
  // publish until a fresh passing eval clears needsReEval. Mirrors the server's 409 publish.eval_gate.
  if (a.needsReEval) {
    toast("Publish blocked: configuration changed since the last passing eval. Run a passing eval first.", "critical");
    return false;
  }
  // Low-confidence gate (backend/05 §5 publish.low_confidence): the latest eval's suite is below the
  // min-case floor, so the verdict isn't trustworthy — blocked unless an admin enabled the override.
  const latest = state.evalRuns.find((e) => e.agentId === id); // evalRuns are newest-first
  if (latest?.verdict === "low_confidence") {
    const policy = state.evalPolicy[a.workspaceId ?? state.activeWorkspaceId] ?? defaultEvalPolicy();
    if (!policy.allowLowConfidenceOverride) {
      toast("Publish blocked: the latest eval is low-confidence (suite below the min-case floor). Expand the suite, or an admin can enable the low-confidence override.", "critical");
      return false;
    }
  }
  const endpoint = a.endpoint ?? a.id;
  set({ agents: state.agents.map((x) => (x.id === id ? { ...x, status: "published", endpoint, updatedAt: nowIso() } : x)) });
  toast(`Published ${a.activeVersion}`);
  return true;
}
/** Unpublish back to draft (the endpoint stops serving until re-published). */
export function unpublishAgent(id: string) {
  updateAgent(id, { status: "draft" }, false);
  toast("Agent unpublished", "neutral");
}
/** Archive (hide from active lists; reversible). */
export function archiveAgent(id: string) {
  updateAgent(id, { status: "archived" }, false);
  toast("Agent archived", "neutral");
}
/** Restore an archived agent back to draft. */
export function restoreAgent(id: string) {
  updateAgent(id, { status: "draft" }, false);
  toast("Agent restored");
}
/** Repoint the live version. Validates the label against the agent's known versions. */
export function rollbackVersion(id: string, label: string) {
  if (!getVersions(id).some((v) => v.label === label)) {
    toast(`Unknown version "${label}"`, "critical");
    return;
  }
  // Re-pointing to a prior (already-evaluated) version clears the re-eval gate (backend/05 §5 re-point is
  // itself eval-gated to an already-passing version).
  updateAgent(id, { activeVersion: label, needsReEval: false }, false);
  toast(`Rolled back to ${label}`);
}
// @backend DELETE /v1/agents/:id - delete agent and its versions (server cascade). The mock mirrors the
//   cascade client-side: drop the agent's eval runs, runs, tokens, and scrub it from channel bindings so
//   no dangling references survive the delete.
export function deleteAgent(id: string) {
  const a = state.agents.find((x) => x.id === id);
  const agents = state.agents.filter((x) => x.id !== id);
  const toolCounts = withBoundToolCounts(agents, state.tools, state.mcpServers);
  set({
    agents,
    // The deleted agent's bindings are gone → decrement affected KB / tool / MCP counts.
    knowledgeBases: withBoundAgentCounts(agents, state.knowledgeBases),
    tools: toolCounts.tools,
    mcpServers: toolCounts.mcpServers,
    evalRuns: state.evalRuns.filter((e) => e.agentId !== id),
    runs: state.runs.filter((r) => r.agentId !== id),
    tokens: a ? state.tokens.filter((t) => t.agent !== a.name) : state.tokens,
    channels: state.channels.map((c) => ({
      ...c,
      agentIds: c.agentIds.filter((aid) => aid !== id),
      defaultAgentId: c.defaultAgentId === id ? undefined : c.defaultAgentId,
    })),
  });
  toast("Agent deleted", "neutral");
}
// @backend POST /v1/agents/:id/duplicate - server copies config into a new draft; returns new id.
export function duplicateAgent(id: string): string | null {
  const a = state.agents.find((x) => x.id === id);
  if (!a) return null;
  const newId = genId(a.id);
  const copy: Agent = {
    ...a, id: newId, name: `${a.name} (copy)`, status: "draft",
    createdAt: nowIso(), updatedAt: nowIso(), activeVersion: "v1", versions: 1,
    calls30d: 0, cost30d: 0, p95ms: 0, evalPass: 0, drift: "none", endpoint: undefined,
    // Materialize the full resolved config so the copy keeps its system prompt (PROMPTS is
    // id-keyed and the new id has no entry) and all other draft-config fields.
    config: resolveConfig(a),
  };
  const agents = [copy, ...state.agents];
  // The copy inherits the original's KB + tool/MCP bindings → increment affected counts.
  const toolCounts = withBoundToolCounts(agents, state.tools, state.mcpServers);
  set({ agents, knowledgeBases: withBoundAgentCounts(agents, state.knowledgeBases), tools: toolCounts.tools, mcpServers: toolCounts.mcpServers });
  toast(`Duplicated as "${copy.name}"`);
  return newId;
}
// @backend POST /v1/knowledge-bases - create a KB (embedding model, chunking) in active workspace.
export function addKnowledgeBase(kb: KnowledgeBase) {
  const stamped = tagResource({ ...kb, workspaceId: kb.workspaceId ?? state.activeWorkspaceId });
  set({ knowledgeBases: [stamped, ...state.knowledgeBases] });
  toast(`Knowledge base "${stamped.name}" created`);
}
// @backend remove - real ingestion is an async worker; the FE polls GET /v1/ingestion-jobs (IngestionJobOut)
//   for the running→completed transition. The mock simulates the terminal flip so the job, KB status, and
//   source land in a consistent state. Reads fresh `state` at fire time (like toast) and is scheduled from
//   the action (not an effect), so StrictMode does not double-fire it. [ref: backend/04-rag.md §2]
function scheduleIngestionCompletion(jobId: string, kbId: string, sourceName: string | null, delayMs = 1800) {
  setTimeout(() => {
    const kbName = state.knowledgeBases.find((k) => k.id === kbId)?.name;
    // Don't clear "indexing" while another job for the same KB is still running.
    const otherRunning = !!kbName && state.ingestionJobs.some((j) => j.id !== jobId && j.kb === kbName && j.status === "running");
    set({
      ingestionJobs: state.ingestionJobs.map((j) =>
        j.id === jobId ? { ...j, status: "completed" as const, duration: "0m 12s", chunks: j.chunks || j.docs * 18 } : j,
      ),
      knowledgeBases: state.knowledgeBases.map((k) => {
        if (k.id !== kbId) return k;
        // Only flip the source this job ingested. Reingest (sourceName=null) re-embeds existing docs and
        // must not touch per-source statuses (which would clobber other syncing/error sources).
        const sources = sourceName
          ? k.sources.map((s) => (s.name === sourceName ? { ...s, status: "synced" as const, updatedAt: nowIso() } : s))
          : k.sources;
        const status: KnowledgeBase["status"] = otherRunning
          ? "indexing"
          : sources.some((s) => s.status === "error")
            ? "error"
            : "ready";
        return { ...k, status, lastSync: nowIso(), sources };
      }),
    });
  }, delayMs);
}
// @backend POST /v1/knowledge-bases/:id/sources - attach a source and kick an async ingestion job;
//   job status streams back (see useIngestionJobs). [ref: backend-integration-plan.md#async-job-flow]
export function addDataSource(kbId: string, src: DataSource, docs = src.docs) {
  const kbs = state.knowledgeBases.map((kb) =>
    kb.id === kbId ? { ...kb, sources: [src, ...kb.sources], status: "indexing" as const } : kb,
  );
  const job: IngestionJob = {
    id: genId("job"), source: src.name, kb: kbs.find((k) => k.id === kbId)?.name ?? "",
    status: "running", docs, chunks: 0, startedAt: nowIso(), duration: "0m 00s",
  };
  // A KB change is a drift trigger for every bound agent (backend/05 §5, DRIFT_TRIGGERS.kb_binding) → flag re-eval.
  set({
    knowledgeBases: kbs,
    ingestionJobs: [job, ...state.ingestionJobs],
    agents: state.agents.map((a) => (a.knowledgeBases.includes(kbId) ? { ...a, needsReEval: true } : a)),
  });
  scheduleIngestionCompletion(job.id, kbId, src.name);
  toast(`Ingesting "${src.name}"`);
}
// @backend POST /v1/knowledge-bases/:id/reingest - re-run ingestion as an async job.
export function reingest(kbId: string) {
  const kb = state.knowledgeBases.find((k) => k.id === kbId);
  if (!kb) return;
  const job: IngestionJob = {
    id: genId("job"), source: kb.sources[0]?.name ?? kb.name, kb: kb.name,
    status: "running", docs: kb.docs, chunks: kb.chunks, startedAt: nowIso(), duration: "0m 00s",
  };
  // Set the KB to indexing up-front so the running→ready transition is visible.
  // Re-ingest bumps kb_index_version → a drift trigger for every bound agent (backend/05 §5) → flag re-eval.
  set({
    knowledgeBases: state.knowledgeBases.map((k) => (k.id === kbId ? { ...k, status: "indexing" as const } : k)),
    ingestionJobs: [job, ...state.ingestionJobs],
    agents: state.agents.map((a) => (a.knowledgeBases.includes(kbId) ? { ...a, needsReEval: true } : a)),
  });
  scheduleIngestionCompletion(job.id, kbId, null);
  toast(`Re-ingesting "${kb.name}"`);
}
// @backend POST /v1/mcp-servers - connect an MCP server; server performs tool discovery and returns
//   the discovered tool list + connection status. [ref: backend-integration-plan.md#part-1]
export function addMcpServer(s: McpServer) {
  const stamped = tagResource({ ...s, workspaceId: s.workspaceId ?? state.activeWorkspaceId });
  set({ mcpServers: [stamped, ...state.mcpServers] });
  toast(`MCP server "${stamped.name}" connected`);
}
// @backend POST /v1/tools - register a tool (HTTP/built-in/sub-agent) with its schema.
export function addTool(t: RepoTool) {
  const stamped = tagResource({ ...t, workspaceId: t.workspaceId ?? state.activeWorkspaceId });
  set({ tools: [stamped, ...state.tools] });
  toast(`Tool "${stamped.name}" added`);
}
// @backend PUT /v1/mcp-servers/:id - update server config (auth/egress/approval + tool allow-list).
export function updateMcpServer(id: string, patch: Partial<McpServer>, notify = true) {
  set({ mcpServers: state.mcpServers.map((s) => (s.id === id ? { ...s, ...patch } : s)) });
  if (notify) toast("MCP server updated");
}
// @backend DELETE /v1/mcp-servers/:id - disconnect + forget a server (vaulted creds revoked server-side).
//   Agent bindings referencing it become dangling refs (resolveToolRef renders them as removable).
export function deleteMcpServer(id: string) {
  const s = state.mcpServers.find((m) => m.id === id);
  set({ mcpServers: state.mcpServers.filter((m) => m.id !== id) });
  toast(`MCP server "${s?.name ?? id}" disconnected`, "neutral");
}
// @backend POST /v1/mcp-servers/:id/discover - re-run tools/list; refresh the cached catalog + health.
export function reconnectMcpServer(id: string) {
  set({
    mcpServers: state.mcpServers.map((s) =>
      s.id === id ? { ...s, status: "connected" as const, lastDiscovery: nowIso() } : s,
    ),
  });
  toast("Reconnected and re-discovered tools");
}
// @backend PATCH /v1/mcp-servers/:id/tools/:tool - toggle a discovered tool in the exposed allow-list.
export function setMcpToolEnabled(serverId: string, toolName: string, enabled: boolean) {
  set({
    mcpServers: state.mcpServers.map((s) =>
      s.id === serverId
        ? { ...s, tools: s.tools.map((t) => (t.name === toolName ? { ...t, enabled } : t)) }
        : s,
    ),
  });
}
// @backend PUT /v1/tools/:id - update a repository tool (config/schema/risk tier). Identity is `name`.
export function updateTool(name: string, patch: Partial<RepoTool>) {
  set({ tools: state.tools.map((t) => (t.name === name ? { ...t, ...patch } : t)) });
  toast("Tool updated");
}
// @backend DELETE /v1/tools/:id - remove a repository tool. Agent bindings by this name become dangling
//   refs (resolveToolRef renders them as removable).
export function deleteTool(name: string) {
  set({ tools: state.tools.filter((t) => t.name !== name) });
  toast(`Tool "${name}" removed`, "neutral");
}
// @backend POST /v1/approvals/:id/decide - approve/deny a paused high-risk tool call; the server resumes
//   (Command(resume=…)) or terminates the run. Publisher+ (tool.approve). [backend/08, backend/03 §2]
export function decideApproval(id: string, decision: "approve" | "deny", reason?: string) {
  set({
    toolApprovals: state.toolApprovals.map((a) =>
      a.id === id
        ? { ...a, status: (decision === "approve" ? "approved" : "denied") as ToolApproval["status"], decidedBy: MOCK_PROFILE.name, decidedAt: nowIso(), decisionReason: reason }
        : a,
    ),
  });
  toast(decision === "approve" ? "Tool call approved" : "Tool call denied", decision === "approve" ? "good" : "neutral");
}
// The verdict rule (backend/05 §1), shared by the Evaluations page and the agent Run-eval dialog so both
// score identically: a suite below minCases is low_confidence; a hard-assertion failure forces fail; then
// quality must clear the threshold AND the cost regression must stay within the efficiency budget.
export function computeVerdict(
  a: { quality: number; costDelta: number; cases: number; hardFail?: boolean },
  opts: { minCases: number; qualityThreshold: number; efficiencyBudgetPct: number },
): Verdict {
  // `hardFail` mirrors the backend's deterministic-layer result (a green/red hard-assertion gate, 05 §2).
  // The mock never EXECUTES the agent or its assertions (runs are simulated below), so no mock caller can
  // compute it — it is left unset here and wired when the real runtime lands. The author's hard/soft choice
  // is still persisted on the case and shipped in the contract; it just can't move a simulated score.
  if (a.cases < opts.minCases) return "low_confidence";
  if (a.hardFail) return "fail";
  if (a.quality < opts.qualityThreshold) return "fail";
  if (a.costDelta > opts.efficiencyBudgetPct) return "fail"; // efficiency axis: cost regression beyond budget
  return "pass";
}

// Mock only: synthesize one eval run. Single source of truth for the simulated run body so the two run
// surfaces (Evaluations page + agent-detail RunEvalDialog) can't drift apart. Replaced wholesale when the
// real async eval job (backend/05 §7) lands — until then quality/deltas are random, case count IS the
// selected suite's size, and `failingCases` echoes the suite so a FAIL has a drill-down.
export function simulateEvalRun(opts: {
  agent: Agent; version: string; baseline: string;
  suite?: EvalSuite; qualityThreshold: number; policy: EvalPolicy;
}): EvalRun {
  const { agent, version, baseline, suite, qualityThreshold, policy } = opts;
  const quality = Math.round((90 + (Math.random() * 12 - 6)) * 10) / 10;
  const qualityDelta = Math.round((Math.random() * 8 - 4) * 10) / 10;
  const costDelta = Math.round((Math.random() * 22 - 6) * 10) / 10; // -6..+16 so a cost regression can breach the budget
  const latencyDelta = Math.round(Math.random() * 160 - 80);
  // Case count IS the selected authored suite's size (so a thin suite honestly reads low-confidence);
  // fall back to an ad-hoc sample when the agent has no authored suite.
  const cases = suite ? suite.cases.length : 24 + Math.floor(Math.random() * 57);
  const verdict = computeVerdict({ quality, costDelta, cases }, { minCases: policy.minCases, qualityThreshold, efficiencyBudgetPct: policy.efficiencyBudgetPct });
  const reason =
    verdict === "fail"
      ? quality < qualityThreshold
        ? `Quality ${quality} fell below the ${qualityThreshold}% publish bar against ${baseline}.`
        : `Efficiency regressed: cost +${costDelta}% exceeds the ${policy.efficiencyBudgetPct}% budget.`
      : verdict === "low_confidence"
        ? `Only ${cases} cases in this run (min ${policy.minCases}); add more for a reliable verdict.`
        : "All dimensions at or above baseline, within the efficiency budget.";
  // A failed run echoes the selected suite's cases as drill-down examples, connecting authoring -> results.
  const failingCases = verdict === "fail" && suite?.cases.length
    ? suite.cases.slice(0, 2).map((c) => ({ input: c.input, issue: "Scored below the baseline on this case." }))
    : undefined;
  return {
    id: genId("ev"), agentId: agent.id, agent: agent.name, version,
    baseline, quality, qualityDelta, costDelta, latencyDelta, cases, verdict,
    date: nowIso(), trigger: "Manual run", reason,
    ...(failingCases ? { failingCases } : {}),
  };
}
// @backend POST /v1/agents/:id/evals - queue an eval run (async job vs frozen baseline + judge). The
//   lifecycle transition done below is a server concern that fires on job completion (see resolveEvalRun),
//   not at queue time; the mock applies it here only because mock evals are born already-resolved.
export function addEvalRun(e: EvalRun) {
  const stamped = { ...e, workspaceId: e.workspaceId ?? state.activeWorkspaceId };
  // Lifecycle STATUS transitions happen ONLY for pre-publish agents: a failing eval gates publishing
  // (draft -> eval_failing); a passing eval clears that gate (eval_failing -> draft). A published/archived
  // agent's STATUS is never changed here — the live pinned version is immutable until an explicit
  // eval-gated re-point, so a regression surfaces via `drift`, not by auto-unpublishing.
  // The denormalized read-models below (evalPass/drift/needsReEval) DO refresh for whichever agent the run
  // targets, published included — that is exactly how a live agent's drift badge reflects a new eval.
  const agent = state.agents.find((a) => a.id === e.agentId);
  let nextStatus: Agent["status"] | null = null;
  if (agent) {
    if (e.verdict === "fail" && agent.status === "draft") nextStatus = "eval_failing";
    else if (e.verdict === "pass" && agent.status === "eval_failing") nextStatus = "draft";
  }
  // Refresh the agent's denormalized eval read-models from this run (server does this on job completion):
  // evalPass = run quality; drift pass->stable / fail->regressed / low_confidence->watch; a passing eval
  // newer than the last trigger clears the re-eval gate. Folded into the same `set` as the eval insert.
  const readModel: Partial<Agent> = {
    evalPass: e.quality,
    drift: e.verdict === "pass" ? "stable" : e.verdict === "fail" ? "regressed" : "watch",
    ...(e.verdict === "pass" ? { needsReEval: false } : {}),
  };
  set({
    evalRuns: [stamped, ...state.evalRuns],
    agents: agent
      ? state.agents.map((a) =>
          a.id === e.agentId ? { ...a, ...readModel, ...(nextStatus ? { status: nextStatus } : {}), updatedAt: nowIso() } : a,
        )
      : state.agents,
  });
  toast(`Eval run queued for ${stamped.agent}`);
}
// @backend (job result) - eval job completion arrives via poll/stream and patches the run row. The same
//   completion must also transition the agent lifecycle server-side: a failing verdict gates a pre-publish
//   agent (draft -> eval_failing), a passing verdict clears it (eval_failing -> draft). Published/archived
//   agents are left untouched (the pinned live version is immutable; regressions surface via `drift`).
export function resolveEvalRun(id: string, patch: Partial<EvalRun>) {
  set({ evalRuns: state.evalRuns.map((e) => (e.id === id ? { ...e, ...patch } : e)) });
}
// @backend POST /v1/agents/:id/tokens - mint a caller token (shown once); scoped to the endpoint.
export function addToken(t: ApiToken) {
  set({ tokens: [t, ...state.tokens] });
  toast(`Token "${t.name}" created`);
}
// @backend DELETE /v1/tokens/:id - revoke a token immediately.
export function revokeToken(id: string) {
  set({ tokens: state.tokens.filter((t) => t.id !== id) });
  toast("Token revoked", "neutral");
}
// @backend POST /v1/provider-keys - store a provider key in the vault (value never returned).
//   [ref: backend-integration-plan.md#part-5]
export function addProviderKey(k: ProviderKey) {
  set({ providerKeys: [k, ...state.providerKeys] });
  toast(`Provider key "${k.label}" added`);
}
// @backend POST /v1/provider-keys/:id/rotate - replace the vaulted secret; the display mask + rotated_at
//   update and the old secret stops working. Admin only. [ref: backend/01 §4.3, backend/09 §9]
export function rotateProviderKey(id: string) {
  set({
    providerKeys: state.providerKeys.map((k) =>
      k.id === id ? { ...k, masked: maskSecret(`sk-${genId("rot")}${genId("")}`), rotatedAt: nowIso(), status: "active" } : k,
    ),
  });
  toast("Key rotated");
}
// @backend POST /v1/provider-keys/:id/revoke - revoke a key; agents using it fail until replaced.
//   Admin only. Kept as a tombstone (status=revoked) rather than deleted. [ref: backend/01 §4.3]
export function revokeProviderKey(id: string) {
  set({ providerKeys: state.providerKeys.map((k) => (k.id === id ? { ...k, status: "revoked" } : k)) });
  toast("Key revoked", "neutral");
}
// @backend POST /v1/channels - connect a messaging-app channel; raw credentials are POSTed once to the
//   vault (only masked refs persist). Server registers the inbound webhook + returns its ingress URL.
//   [ref: backend/12-channels.md §2-3]
export function addChannel(c: Channel) {
  const stamped = tagResource({ ...c, workspaceId: c.workspaceId ?? state.activeWorkspaceId });
  set({ channels: [stamped, ...state.channels] });
  toast(`Channel "${stamped.name}" connected`);
}
// @backend PATCH /v1/channels/:id - edit binding (routing, bound agents, Okta group, status, rotate creds).
export function updateChannel(id: string, patch: Partial<Channel>, notify = true) {
  set({ channels: state.channels.map((c) => (c.id === id ? { ...c, ...patch, updatedAt: nowIso() } : c)) });
  if (notify) toast("Channel updated");
}
// @backend DELETE /v1/channels/:id - disconnect a channel and revoke its vaulted credentials.
export function deleteChannel(id: string) {
  set({ channels: state.channels.filter((c) => c.id !== id) });
  toast("Channel disconnected", "neutral");
}
// @backend none (server-recorded) - runs are emitted by runtime.invoke() server-side at every call
//   (playground/API/eval/channel). This client action exists so the mock's "send test" affordances
//   surface a run on the Runs page; it goes away once runs come from the API. [ref: backend/10 §1]
export function addRun(r: Run) {
  const stamped = { ...r, workspaceId: r.workspaceId ?? tenantOfAgent(r.agentId, state.agents) };
  set({ runs: [stamped, ...state.runs] });
}

/* ----------------------------------------------------------------- hooks */
/** getSnapshot must return a stable reference when nothing changed, or useSyncExternalStore
   loops forever. Selectors here build new arrays (filter/map), so cache the last result and
   reuse it when shallow-equal to avoid an infinite render loop. */
function shallowEqual<T>(a: T, b: T): boolean {
  if (Object.is(a, b)) return true;
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) if (!Object.is(a[i], b[i])) return false;
    return true;
  }
  return false;
}
function useSelector<T>(selector: (s: State) => T): T {
  const cache = useRef<{ v: T } | null>(null);
  const get = () => {
    const next = selector(state);
    if (cache.current && shallowEqual(cache.current.v, next)) return cache.current.v;
    cache.current = { v: next };
    return next;
  };
  return useSyncExternalStore(subscribe, get, get);
}
const inWs = <T extends { workspaceId?: string }>(arr: T[], ws: string) => arr.filter((r) => r.workspaceId === ws);

/* tenancy / auth state */
export const useActiveWorkspaceId = () => useSelector((s) => s.activeWorkspaceId);
export const useRoleOverride = () => useSelector((s) => s.roleOverride);
export const useTeamOverride = () => useSelector((s) => s.teamOverride);

/* workspace-scoped lists
   @backend reads - every selector below replaces its mock store slice with a workspace-scoped API
   GET (filter by the active workspace server-side); the server also applies access-policy visibility
   so restricted resources never reach a caller who can't use them. [ref: backend-integration-plan.md#part-2] */
// @backend GET /v1/agents?workspace=:id - list agents for active workspace.
export const useAgents = () => useSelector((s) => inWs(s.agents, s.activeWorkspaceId));
// @backend GET /v1/agents/:id - single agent (config, versions, bound knowledge/tools).
/** Single resource getters resolve by id across workspaces; callers scope/guard as needed. */
export const useAgent = (id: string) => useSelector((s) => s.agents.find((a) => a.id === id));
// @backend GET /v1/knowledge-bases?workspace=:id - list KBs.
export const useKnowledgeBases = () => useSelector((s) => inWs(s.knowledgeBases, s.activeWorkspaceId));
// @backend GET /v1/knowledge-bases/:id - single KB (sources, stats).
export const useKnowledgeBase = (id: string) => useSelector((s) => s.knowledgeBases.find((k) => k.id === id));
// @backend GET /v1/ingestion-jobs (poll or SSE stream) - ingestion job status.
export const useIngestionJobs = () => useSelector((s) => s.ingestionJobs);
// @backend GET /v1/mcp-servers?workspace=:id - list MCP servers + discovered tools.
export const useMcpServers = () => useSelector((s) => inWs(s.mcpServers, s.activeWorkspaceId));
// @backend GET /v1/mcp-servers/:id - single MCP server.
export const useMcpServer = (id: string) => useSelector((s) => s.mcpServers.find((m) => m.id === id));
// @backend GET /v1/tools?workspace=:id - list tool repository.
export const useTools = () => useSelector((s) => inWs(s.tools, s.activeWorkspaceId));
// @backend GET /v1/tools/:id - single repository tool (by name identity).
export const useTool = (name: string) => useSelector((s) => s.tools.find((t) => t.name === name));
// @backend GET /v1/evals?workspace=:id - list eval runs.
export const useEvalRuns = () => useSelector((s) => inWs(s.evalRuns, s.activeWorkspaceId));
// @backend GET /v1/runs?workspace=:id - list observability runs (caller/tenant attributed).
export const useRuns = () => useSelector((s) => inWs(s.runs, s.activeWorkspaceId));
// @backend GET /v1/approvals?workspace=:id - pending/decided high-risk tool-call approvals.
export const useApprovals = () => useSelector((s) => inWs(s.toolApprovals, s.activeWorkspaceId));
// @backend GET /v1/channels?workspace=:id - list connected messaging channels for active workspace.
export const useChannels = () => useSelector((s) => inWs(s.channels, s.activeWorkspaceId));
// @backend GET /v1/channels/:id - single channel (routing, bound agents, masked creds, status).
export const useChannel = (id: string) => useSelector((s) => s.channels.find((c) => c.id === id));
// @backend GET /v1/agents/:id/channels - channels routing to this agent (agent-detail cross-link).
/** Channels that route to a given agent — for the agent detail "Reachable via channels" cross-link. */
export const useChannelsForAgent = (agentId: string) =>
  useSelector((s) => inWs(s.channels, s.activeWorkspaceId).filter((c) => c.agentIds.includes(agentId)));
// @backend GET /v1/agents/:id/evals and /runs - per-agent lists for the detail tabs.
/** Per-agent, store-backed so new eval/run mutations show on the agent detail tabs. Filtered by
   agentId only — an agentId already pins the rows to that agent's single workspace, and useAgent
   itself is not workspace-scoped, so adding an activeWorkspaceId filter here would wrongly empty the
   tabs when the active workspace is switched while viewing an agent from another one. */
export const useEvalRunsForAgent = (agentId: string) => useSelector((s) => s.evalRuns.filter((e) => e.agentId === agentId));
export const useRunsForAgent = (agentId: string) => useSelector((s) => s.runs.filter((r) => r.agentId === agentId));
// @backend GET /v1/agents/:id/eval-suites - authored eval suites + cases for this agent (eval.author to edit).
export const useSuitesForAgent = (agentId: string) => useSelector((s) => s.evalSuites.filter((x) => x.agentId === agentId));
// @backend GET /v1/agents/:id/tokens - list endpoint tokens (prefixes only, never the secret).
export const useTokens = () => useSelector((s) => s.tokens);
// @backend GET /v1/provider-keys?workspace=:id - list vaulted provider keys (masked).
export const useProviderKeys = () => useSelector((s) => s.providerKeys);
// @backend GET /v1/members?workspace=:id - space roster + role-per-workspace (app-managed: invite/join,
//   space admins can remove — see inviteMember/removeMember). Identity still comes from org Okta.
export const useMembers = () => useSelector((s) => s.members);
// @backend GET /v1/workspaces/:id/invites - pending invites for the active space (space admin view).
export const useInvites = () => useSelector((s) => s.invites);
// @backend remove - session-local record of open spaces the user joined this session (merged in auth.tsx).
export const useJoinedWorkspaceIds = () => useSelector((s) => s.joinedWorkspaceIds);
// Stable fallbacks: useSelector caches by reference, so returning a fresh default object each call
// would loop. All workspaces are seeded, so these only guard an unseeded tenant.
const FALLBACK_WS_SETTINGS = defaultWorkspaceSettings();
const FALLBACK_LIMITS = defaultLimits();
const FALLBACK_EVAL_POLICY = defaultEvalPolicy();
// @backend GET /v1/workspaces/:id - active workspace config (name, default provider/model, archived).
export const useWorkspaceSettings = () => useSelector((s) => s.workspaceSettings[s.activeWorkspaceId] ?? FALLBACK_WS_SETTINGS);
// @backend GET /v1/workspaces/:id/limits - active workspace spend/concurrency/timeout ceilings.
export const useLimits = () => useSelector((s) => s.limits[s.activeWorkspaceId] ?? FALLBACK_LIMITS);
// @backend GET /v1/workspaces/:id/eval-policy - admin-governed verdict/publish thresholds (eval.govern).
export const useEvalPolicy = () => useSelector((s) => s.evalPolicy[s.activeWorkspaceId] ?? FALLBACK_EVAL_POLICY);
// Agent-scoped eval policy: resolve from the AGENT's workspace, not the active one, so the UI matches
// publishAgent()'s gate (which reads evalPolicy[agent.workspaceId]) for a cross-workspace agent.
export const useEvalPolicyFor = (workspaceId?: string) =>
  useSelector((s) => s.evalPolicy[workspaceId ?? s.activeWorkspaceId] ?? FALLBACK_EVAL_POLICY);
// @backend none - toasts are local UI state.
export const useToasts = () => useSelector((s) => s.toasts);
