"use client";

import { useRef, useSyncExternalStore } from "react";
import {
  AGENTS, KNOWLEDGE_BASES, INGESTION_JOBS, MCP_SERVERS, TOOLS_REPO, EVAL_RUNS, RUNS,
  API_TOKENS, PROVIDER_KEYS, MEMBERS, CHANNELS_SEED, resolveConfig, getVersions,
  type Agent, type AgentConfig, type KnowledgeBase, type DataSource, type IngestionJob, type McpServer,
  type RepoTool, type EvalRun, type Run, type ApiToken, type ProviderKey, type Member, type Channel,
} from "./data";
import { type AccessPolicy, defaultPolicy } from "./access-policy";
import type { Role } from "./permissions";

/* ----------------------------------------------------------- tenancy seed */
const DEFAULT_WS = "trellix-xdr";
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
  runs: Run[];
  tokens: ApiToken[];
  providerKeys: ProviderKey[];
  members: Member[];
  channels: Channel[];
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
  knowledgeBases: KNOWLEDGE_BASES.map(tagResource),
  ingestionJobs: INGESTION_JOBS,
  mcpServers: MCP_SERVERS.map(tagResource),
  tools: TOOLS_REPO.map(tagResource),
  evalRuns: EVAL_RUNS.map((e) => ({ ...e, workspaceId: e.workspaceId ?? tenantOfAgent(e.agentId, seedAgents) })),
  runs: RUNS.map((r) => ({ ...r, workspaceId: r.workspaceId ?? tenantOfAgent(r.agentId, seedAgents) })),
  tokens: API_TOKENS,
  providerKeys: PROVIDER_KEYS,
  members: MEMBERS,
  channels: CHANNELS_SEED.map(tagResource),
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
// @backend remove - roleOverride/teamOverride are dev-only preview switches; delete once the real
//   IAM session supplies role-per-workspace + teams. [ref: frontend-auth-access-plan.md#part-e]
export function setRoleOverride(role: Role | null) {
  set({ roleOverride: role });
}
export function setTeamOverride(teams: string[] | null) {
  set({ teamOverride: teams });
}
// @backend PUT /v1/{agents|knowledge|tools|mcp|channels}/:id/access-policy - persist per-resource policy;
//   server enforces it on read/manage and at invoke. [ref: frontend-auth-access-plan.md#part-b2]
/** Admin-configurable per-resource access policy (Part B2). */
export function setAccessPolicy(
  kind: "agent" | "knowledge" | "tool" | "mcp" | "channel",
  id: string,
  policy: AccessPolicy,
) {
  const apply = <T extends WithTenant>(arr: T[]) => arr.map((r) => (keyOf(r) === id ? { ...r, accessPolicy: policy } : r));
  if (kind === "agent") set({ agents: apply(state.agents) });
  else if (kind === "knowledge") set({ knowledgeBases: apply(state.knowledgeBases) });
  else if (kind === "tool") set({ tools: apply(state.tools) });
  else if (kind === "channel") set({ channels: apply(state.channels) });
  else set({ mcpServers: apply(state.mcpServers) });
  toast("Access policy updated");
}

/* --------------------------------------------------------------- actions */
// @backend POST /v1/agents - create a draft agent in the active workspace; returns the created id.
export function addAgent(a: Agent) {
  const stamped = tagResource({ ...a, workspaceId: a.workspaceId ?? state.activeWorkspaceId });
  set({ agents: [stamped, ...state.agents] });
  toast(`Agent "${stamped.name}" created as a draft`);
}
// @backend PATCH /v1/agents/:id - generic field patch + bind knowledge/tools. Lifecycle transitions
//   (publish/unpublish/archive/restore) and version rollback go through the dedicated guarded wrappers
//   below (publishAgent/unpublishAgent/archiveAgent/restoreAgent/rollbackVersion), which map to the same
//   endpoint but centralize the passing-eval guard.
export function updateAgent(id: string, patch: Partial<Agent>, notify = true) {
  set({ agents: state.agents.map((a) => (a.id === id ? { ...a, ...patch, updatedAt: nowIso() } : a)) });
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
      return { ...a, config: next, updatedAt: nowIso() };
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
  updateAgent(id, { activeVersion: label }, false);
  toast(`Rolled back to ${label}`);
}
// @backend DELETE /v1/agents/:id - delete agent and its versions (server cascade). The mock mirrors the
//   cascade client-side: drop the agent's eval runs, runs, tokens, and scrub it from channel bindings so
//   no dangling references survive the delete.
export function deleteAgent(id: string) {
  const a = state.agents.find((x) => x.id === id);
  set({
    agents: state.agents.filter((x) => x.id !== id),
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
  set({ agents: [copy, ...state.agents] });
  toast(`Duplicated as "${copy.name}"`);
  return newId;
}
// @backend POST /v1/knowledge-bases - create a KB (embedding model, chunking) in active workspace.
export function addKnowledgeBase(kb: KnowledgeBase) {
  const stamped = tagResource({ ...kb, workspaceId: kb.workspaceId ?? state.activeWorkspaceId });
  set({ knowledgeBases: [stamped, ...state.knowledgeBases] });
  toast(`Knowledge base "${stamped.name}" created`);
}
// @backend POST /v1/knowledge-bases/:id/sources - attach a source and kick an async ingestion job;
//   job status streams back (see useIngestionJobs). [ref: backend-integration-plan.md#async-job-flow]
export function addDataSource(kbId: string, src: DataSource, docs = 0) {
  const kbs = state.knowledgeBases.map((kb) =>
    kb.id === kbId ? { ...kb, sources: [src, ...kb.sources], status: "indexing" as const } : kb,
  );
  const job: IngestionJob = {
    id: genId("job"), source: src.name, kb: kbs.find((k) => k.id === kbId)?.name ?? "",
    status: "running", docs, startedAt: nowIso(), duration: "0m 02s",
  };
  set({ knowledgeBases: kbs, ingestionJobs: [job, ...state.ingestionJobs] });
  toast(`Ingesting "${src.name}"`);
}
// @backend POST /v1/knowledge-bases/:id/reingest - re-run ingestion as an async job.
export function reingest(kbId: string) {
  const kb = state.knowledgeBases.find((k) => k.id === kbId);
  if (!kb) return;
  const job: IngestionJob = {
    id: genId("job"), source: kb.sources[0]?.name ?? kb.name, kb: kb.name,
    status: "running", docs: kb.docs, startedAt: nowIso(), duration: "0m 01s",
  };
  set({ ingestionJobs: [job, ...state.ingestionJobs] });
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
// @backend POST /v1/agents/:id/evals - queue an eval run (async job vs frozen baseline + judge).
export function addEvalRun(e: EvalRun) {
  const stamped = { ...e, workspaceId: e.workspaceId ?? state.activeWorkspaceId };
  // Propagate the verdict to lifecycle, but ONLY for pre-publish agents: a failing eval gates
  // publishing (draft -> eval_failing); a passing eval clears that gate (eval_failing -> draft).
  // Published/archived agents are left untouched — the live pinned version is immutable until an
  // explicit eval-gated re-point, so a regression surfaces via `drift`, not by auto-unpublishing.
  // Folded into one `set` with the eval insert so a single logical action is one store commit.
  const agent = state.agents.find((a) => a.id === e.agentId);
  let nextStatus: Agent["status"] | null = null;
  if (agent) {
    if (e.verdict === "fail" && agent.status === "draft") nextStatus = "eval_failing";
    else if (e.verdict === "pass" && agent.status === "eval_failing") nextStatus = "draft";
  }
  set({
    evalRuns: [stamped, ...state.evalRuns],
    agents: nextStatus
      ? state.agents.map((a) => (a.id === e.agentId ? { ...a, status: nextStatus!, updatedAt: nowIso() } : a))
      : state.agents,
  });
  toast(`Eval run queued for ${stamped.agent}`);
}
// @backend (job result) - eval job completion arrives via poll/stream and patches the run row.
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
// @backend POST /v1/provider-keys - store a provider key in the vault (value never returned);
//   also rotate/revoke. [ref: backend-integration-plan.md#part-5]
export function addProviderKey(k: ProviderKey) {
  set({ providerKeys: [k, ...state.providerKeys] });
  toast(`Provider key "${k.label}" added`);
}
// @backend none (IAM-managed) - membership changes happen in Trellix IAM, not here; Settings deep-
//   links to IAM instead of inviting. This action is unused by the UI and can be removed.
export function addMember(m: Member) {
  set({ members: [m, ...state.members] });
  toast(`Invited ${m.email}`);
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
// @backend GET /v1/evals?workspace=:id - list eval runs.
export const useEvalRuns = () => useSelector((s) => inWs(s.evalRuns, s.activeWorkspaceId));
// @backend GET /v1/runs?workspace=:id - list observability runs (caller/tenant attributed).
export const useRuns = () => useSelector((s) => inWs(s.runs, s.activeWorkspaceId));
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
// @backend GET /v1/agents/:id/tokens - list endpoint tokens (prefixes only, never the secret).
export const useTokens = () => useSelector((s) => s.tokens);
// @backend GET /v1/provider-keys?workspace=:id - list vaulted provider keys (masked).
export const useProviderKeys = () => useSelector((s) => s.providerKeys);
// @backend GET /v1/members?workspace=:id - IAM-sourced members + role-per-workspace (read-only).
export const useMembers = () => useSelector((s) => s.members);
// @backend none - toasts are local UI state.
export const useToasts = () => useSelector((s) => s.toasts);
