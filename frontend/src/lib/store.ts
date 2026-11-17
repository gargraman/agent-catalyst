"use client";

import { useEffect, useRef, useSyncExternalStore } from "react";
import {
  LIVE_API, ApiError, createAgent, getAgentWithEtag, listAgents, listMyWorkspaces, patchAgent,
  createVersion as apiCreateVersion,
  type AgentOut, type AgentPatch, type DevPrincipal, type WorkspaceMembershipOut,
} from "./api/client";
import { agentOutToAgent, agentToCreate, draftWithEdit, inputVariablesFromSchema, providerKey, type DraftEdit } from "./api/adapters";
import { refusalMessage } from "./api/refusals";
import { streamInvoke, type InvokeFrame, type InvokeIn, type StreamResult } from "./api/stream";
import {
  AGENTS, KNOWLEDGE_BASES, INGESTION_JOBS, MCP_SERVERS, TOOLS_REPO, SKILLS, EVAL_RUNS, EVAL_SUITES, RUNS, TOOL_APPROVALS,
  API_TOKENS, PROVIDER_KEYS, MEMBERS, CHANNELS_SEED, resolveConfig, getVersions,
  WORKSPACE_SETTINGS, LIMITS, WORKSPACE_SPEND, defaultWorkspaceSettings, defaultLimits, defaultWorkspaceSpend, defaultEvalPolicy,
  type Agent, type AgentConfig, type KnowledgeBase, type DataSource, type IngestionJob, type McpServer,
  type RepoTool, type Skill, type EvalRun, type EvalSuite, type EvalCaseDef, type Run, type ToolApproval, type ApiToken, type ProviderKey, type Member, type Channel,
  type WorkspaceSettings, type Limits, type WorkspaceSpend, type EvalPolicy, type Verdict,
} from "./data";
import { type AccessPolicy, defaultPolicy } from "./access-policy";
import type { Role } from "./permissions";
import { DEFAULT_WORKSPACE_ID, MOCK_PROFILE, WORKSPACES, slugify, shortOf, type Workspace } from "./iam";
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

// Recompute Skill.boundAgents (agents binding the skill by id) after a bind/unbind, mirroring
// withBoundAgentCounts. [ref: backend/08-api-surface.md §3 SkillOut.bound_agents]
function withBoundSkillCounts(agents: Agent[], skills: Skill[]): Skill[] {
  return skills.map((sk) => {
    const count = agents.filter((a) => (a.skills ?? []).includes(sk.id)).length;
    return sk.boundAgents === count ? sk : { ...sk, boundAgents: count };
  });
}

/* ----------------------------------------------------------------- state */
export type Toast = { id: string; tone: "good" | "critical" | "neutral"; message: string };

type State = {
  activeWorkspaceId: string;
  // @backend remove - the workspace registry is store-backed so the mock can create/edit/delete spaces.
  //   Real workspaces come from IAM (the user's Okta group memberships, session); this seeds from WORKSPACES.
  workspaces: Workspace[];
  // @backend remove - dev-only preview toggle for the platform-admin dimension (null = use MOCK_PROFILE's
  //   groups). Lets the demo drop platform-admin to preview the member experience. Real value = whether the
  //   OIDC `groups` claim contains PLATFORM_ADMIN_GROUP.
  platformAdminOverride: boolean | null;
  roleOverride: Role | null;
  teamOverride: string[] | null;
  agents: Agent[];
  knowledgeBases: KnowledgeBase[];
  ingestionJobs: IngestionJob[];
  mcpServers: McpServer[];
  tools: RepoTool[];
  skills: Skill[];
  evalRuns: EvalRun[];
  evalSuites: EvalSuite[];
  evalPolicy: Record<string, EvalPolicy>; // per-workspace; falls back to defaultEvalPolicy()
  runs: Run[];
  tokens: ApiToken[];
  providerKeys: ProviderKey[];
  // @backend read-only in the mock - the roster is a projection of the workspace's role claims (idp_binding);
  //   shown read-only (no invite/remove). Real source: GET /v1/members?workspace=:id (adapter projection,
  //   backend/18 §4 group_members cache).
  members: Member[];
  channels: Channel[];
  toolApprovals: ToolApproval[];
  workspaceSettings: Record<string, WorkspaceSettings>;
  limits: Record<string, Limits>;
  toasts: Toast[];
  // Live mode: per-resource load status, keyed "<family>:<consoleWorkspaceId>" (or "workspaces"). See `load()`.
  remote: Record<string, RemoteStatus>;
  // Live mode: a save refused 409 (stale ETag — someone else changed the agent). Keyed by agent id; the page shows
  // the "changed by someone else" dialog. `reapply` re-runs the SAME field-scoped edit on top of the latest state.
  editConflicts: Record<string, EditConflict>;
  // Live mode: bumped when an agent is reloaded after a conflict, so its edit forms re-initialise from the server.
  revisions: Record<string, number>;
};
export type EditConflict = { reapply: () => Promise<boolean> };

// @backend remove - initial state is hydrated from mock seeds (src/lib/data.ts). With the backend,
//   the store starts empty and each list hook fetches from its API; the tagResource/WS_OVERRIDES/
//   POLICY_OVERRIDES normalization and the seed arrays all go away (server owns workspaceId +
//   accessPolicy). [ref: backend-integration-plan.md#part-2]
const seedAgents = AGENTS.map(tagResource);
let state: State = {
  activeWorkspaceId: DEFAULT_WS,
  workspaces: WORKSPACES,
  platformAdminOverride: null,
  roleOverride: null,
  teamOverride: null,
  agents: seedAgents,
  knowledgeBases: withBoundAgentCounts(seedAgents, KNOWLEDGE_BASES.map(tagResource)),
  ingestionJobs: INGESTION_JOBS,
  mcpServers: MCP_SERVERS.map(tagResource),
  tools: TOOLS_REPO.map(tagResource),
  skills: withBoundSkillCounts(seedAgents, SKILLS.map(tagResource)),
  evalRuns: EVAL_RUNS.map((e) => ({ ...e, workspaceId: e.workspaceId ?? tenantOfAgent(e.agentId, seedAgents) })),
  evalSuites: EVAL_SUITES.map((s) => ({ ...s, workspaceId: s.workspaceId ?? tenantOfAgent(s.agentId, seedAgents) })),
  evalPolicy: {},
  runs: RUNS.map((r) => ({ ...r, workspaceId: r.workspaceId ?? tenantOfAgent(r.agentId, seedAgents) })),
  tokens: API_TOKENS,
  providerKeys: PROVIDER_KEYS,
  members: MEMBERS,
  channels: CHANNELS_SEED.map(tagResource),
  toolApprovals: TOOL_APPROVALS.map((a) => ({ ...a, workspaceId: a.workspaceId ?? tenantOfAgent(a.agentId, seedAgents) })),
  workspaceSettings: WORKSPACE_SETTINGS,
  limits: LIMITS,
  toasts: [],
  remote: {},
  editConflicts: {},
  revisions: {},
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
/* ------------------------------------------------- live API (NEXT_PUBLIC_API_MODE=live) */
// Wiring is INCREMENTAL by design: a workspace becomes "live" when discovery gives it a `backendId`; inside a live
// workspace each WIRED family (agents today) is sourced from the API, every other family stays on its mock seed.
// Wiring the next family = one `loadX()` on the `load()` convention + one adapter + one `useEnsureX()` in its page.
// With the flag off, none of this runs and the console is the pure mock it always was.

export type RemoteStatus = {
  status: "idle" | "loading" | "ready" | "error";
  error?: string;
  code?: string;       // backend error-envelope code (e.g. "access.denied")
  httpStatus?: number; // 0 = network / proxy failure (backend unreachable)
  at?: number;         // epoch ms of the last successful load (freshness for `maxAgeMs`)
};
/** Default freshness window: a key loaded within this window is served from the store without a refetch, so N
 *  components mounting together cost one request; older data is revalidated in the background on next mount. */
const DEFAULT_MAX_AGE_MS = 30_000;
const IDLE: RemoteStatus = { status: "idle" };
const inflight = new Map<string, Promise<void>>();

function setRemote(key: string, s: RemoteStatus) {
  set({ remote: { ...state.remote, [key]: s } });
}
function errStatus(e: unknown): RemoteStatus {
  if (e instanceof ApiError) return { status: "error", error: e.message, code: e.code, httpStatus: e.status };
  // fetch() rejections (network down, proxy refused, AbortSignal timeout) → httpStatus 0 = "unreachable".
  return { status: "error", error: e instanceof Error ? e.message : String(e), httpStatus: 0 };
}

/** The ONE async-load convention every wired family uses:
 *  - keyed status (idle/loading/ready/error) readable via `useRemoteStatus(key)`;
 *  - in-flight de-duplication — N components mounting the same key issue ONE request;
 *  - `fn` applies its result to the store itself, and only on success, so a failed refresh never blanks data
 *    that is already on screen (and there is no silent fallback to mock data — the error is surfaced).
 *  - stale-while-revalidate: a "ready" key younger than `maxAgeMs` is not refetched; an older one is refetched
 *    while the previous data stays on screen (`force` always refetches — e.g. the Retry button).
 *  Keys are family + console workspace id, so switching workspaces is a separate cache entry, and a response that
 *  lands after a switch still writes into the workspace it was fetched for. */
export type LoadOpts = { force?: boolean; maxAgeMs?: number };
function load(key: string, fn: () => Promise<void>, { force = false, maxAgeMs = DEFAULT_MAX_AGE_MS }: LoadOpts = {}): Promise<void> {
  const existing = inflight.get(key);
  if (existing) return existing;
  const prev = state.remote[key];
  if (!force && prev?.status === "ready" && Date.now() - (prev.at ?? 0) < maxAgeMs) return Promise.resolve();
  setRemote(key, { status: "loading", at: prev?.at });
  const p = fn()
    .then(() => setRemote(key, { status: "ready", at: Date.now() }), (e: unknown) => setRemote(key, errStatus(e)))
    .finally(() => inflight.delete(key));
  inflight.set(key, p);
  return p;
}

// @backend session - the subject comes from the OIDC session once real auth lands; `client.devAuthHeaders` is the
//   single place the header shape changes. No role claim is sent: the server resolves the role (members store).
const callerFor = (ws?: Workspace): DevPrincipal => ({ subject: MOCK_PROFILE.email, ws: ws?.backendId });
const activeWorkspace = () => state.workspaces.find((w) => w.id === state.activeWorkspaceId);

const BACKEND_ROLES: readonly Role[] = ["viewer", "builder", "publisher", "admin"];
/** Unknown role strings fail to least privilege. */
const toRole = (r: string): Role => (BACKEND_ROLES.includes(r as Role) ? (r as Role) : "viewer");

/** Join discovered backend workspaces onto the console registry. Match on `group_prefix` (the stable IdP binding
 *  both sides share), then on the name slug; an unmatched backend workspace is appended as a new live entry.
 *  Backend fields are reset first, so a membership revoked server-side stops being live on the next discovery. */
function mergeDiscovered(current: Workspace[], rows: WorkspaceMembershipOut[]): Workspace[] {
  const next: Workspace[] = current.map((w) => {
    if (w.backendId === undefined && w.backendRole === undefined) return w;
    const copy = { ...w };
    delete copy.backendId;
    delete copy.backendRole;
    return copy;
  });
  for (const r of rows) {
    const live = { backendId: r.id, backendRole: toRole(r.role) };
    const slug = slugify(r.name);
    const idx = next.findIndex((w) => (r.group_prefix && w.groupPrefix === r.group_prefix) || w.id === slug);
    if (idx >= 0) {
      next[idx] = { ...next[idx], ...live };
    } else {
      const id = slug && !next.some((w) => w.id === slug) ? slug : r.id;
      next.push({ id, name: r.name, short: shortOf(r.name), groupPrefix: r.group_prefix ?? "", ...live });
    }
  }
  return next;
}

/** Recompute the server-style binding rollups after the agent set changes (shared by every agent mutation). */
function rollupsFor(agents: Agent[]) {
  const toolCounts = withBoundToolCounts(agents, state.tools, state.mcpServers);
  return {
    knowledgeBases: withBoundAgentCounts(agents, state.knowledgeBases),
    tools: toolCounts.tools,
    mcpServers: toolCounts.mcpServers,
    skills: withBoundSkillCounts(agents, state.skills),
  };
}

/** Workspace discovery (GET /api/v1/workspaces). Live workspaces drop their mock agent seeds immediately — the
 *  server owns that list now, so the UI shows loading → real data, never a flash of fixtures. */
// TODO(fe-wiring): only the agents family is dropped here. As each family is wired, drop its seeds for live
//   workspaces too (KBs, tools, skills, runs, evals…) — until then a live workspace shows seed runs/evals that
//   reference seed agent ids the server doesn't have (rendered as unresolved, no crash).
// TODO(fe-wiring): if discovery FAILS in live mode, workspaces stay un-linked, so pages render seed data under the
//   error strip (surfaced, not silent — but misleading). Decide: hide wired families' seeds whenever LIVE_API is on
//   and discovery isn't "ready", at the cost of an empty console while the API is down.
// TODO(fe-wiring): two backend workspaces sharing a group_prefix both match the same console entry (last wins) —
//   the backend doesn't enforce group_prefix uniqueness (see services/api/models.py Workspace TODO).
export function loadWorkspaces(opts?: LoadOpts): Promise<void> {
  if (!LIVE_API) return Promise.resolve();
  return load("workspaces", async () => {
    const workspaces = mergeDiscovered(state.workspaces, await listMyWorkspaces(callerFor()));
    const liveIds = new Set(workspaces.filter((w) => w.backendId).map((w) => w.id));
    const agents = state.agents.filter((a) => !liveIds.has(a.workspaceId ?? "") || !isSeedAgent(a));
    set({ workspaces, agents, ...rollupsFor(agents) });
  }, opts);
}
/** Client-clock time each live agent was created in this tab (see the create/revalidate race in loadAgents). */
const createdLocallyAt = new Map<string, number>();
const SEED_AGENT_IDS = new Set(AGENTS.map((a) => a.id));
const isSeedAgent = (a: Agent) => SEED_AGENT_IDS.has(a.id);

/** Agents of the ACTIVE workspace from the API (no-op for a mock-only workspace). Replaces that workspace's slice
 *  wholesale — the server list is authoritative (access-policy visibility is applied server-side). */
export function loadAgents(opts?: LoadOpts): Promise<void> {
  const ws = activeWorkspace();
  if (!LIVE_API || !ws?.backendId) return Promise.resolve();
  const wsId = ws.id;
  return load(`agents:${wsId}`, async () => {
    const startedAt = Date.now();
    const byId = new Map(state.agents.map((a) => [a.id, a]));
    // Server-owned fields win; local-only fields of an agent already in this tab survive the revalidation.
    const live = (await listAgents(callerFor(ws))).map((o) => {
      const fresh = agentOutToAgent(o, wsId);
      const prev = byId.get(fresh.id);
      return prev ? { ...prev, ...pickServerOwned(fresh) } : fresh;
    });
    const liveIds = new Set(live.map((a) => a.id));
    // Keep agents this tab created WHILE the list was in flight (the response predates them), so a create racing a
    // background revalidation isn't dropped; everything else in this workspace is replaced by the server list.
    // (Client clock on both sides — `createdLocallyAt` is stamped by addAgent — so server clock skew can't bite.)
    const createdDuring = state.agents.filter(
      (a) => a.workspaceId === wsId && !liveIds.has(a.id) && (createdLocallyAt.get(a.id) ?? 0) >= startedAt);
    const agents = [...createdDuring, ...live, ...state.agents.filter((a) => a.workspaceId !== wsId)];
    set({ agents, ...rollupsFor(agents) });
  }, opts);
}

/** One agent by id, for a deep link / reload straight onto a detail page before the list has loaded. Resolved in
 *  the ACTIVE workspace (the API is workspace-scoped); already-present agents are not re-fetched. */
// TODO(fe-wiring): detail URLs (/agents/<uuid>) carry no workspace, so a deep link into a workspace other than the
//   active one 404s ("Agent not found"). Fix by scoping routes (/w/<ws>/agents/<id>) or by a server-side
//   agent→workspace lookup for the caller, then switching the active workspace before loading.
// The ETag (D13) is captured per agent in `etags`: the list response carries none, so an agent that is present only
// from the list is still fetched once here — the backend REQUIRES If-Match on every edit (400 without, 409 stale).
export function loadAgent(id: string, opts: LoadOpts = {}): Promise<void> {
  const ws = activeWorkspace();
  if (!LIVE_API || !ws?.backendId) return Promise.resolve();
  if (!opts.force && etags.has(id) && state.agents.some((a) => a.id === id)) return Promise.resolve();
  const wsId = ws.id;
  return load(`agent:${id}`, async () => {
    const seq = issueSeq(id);
    const { data, etag } = await getAgentWithEtag(callerFor(ws), id);
    applyAgentOut(data, etag, wsId, seq);
  }, { ...opts, force: true });
}

/** Per-agent ETag (xmin) from the last GET/PATCH. Not render state, so it lives outside `state`. */
const etags = new Map<string, string>();

/** Review #10: a GET issued BEFORE a PATCH can resolve AFTER it; applying it would roll the agent (and its ETag)
 *  back to the pre-edit row, so the next edit 409s against the user's own save. Every agent request takes a
 *  per-agent sequence number when it is ISSUED, and a response is applied only if it is newer than the last one
 *  applied. Responses then land in issue order (the server serialises the writes, so issue order is row order). */
const issuedSeq = new Map<string, number>();
const appliedSeq = new Map<string, number>();
function issueSeq(id: string): number {
  const n = (issuedSeq.get(id) ?? 0) + 1;
  issuedSeq.set(id, n);
  return n;
}

/** Merge one server AgentOut into the store (+ remember its ETag). Merges over an existing copy so a refresh doesn't
 *  wipe local-only display fields (see addAgent NOTE). A response older than one already applied is dropped. */
function applyAgentOut(o: AgentOut, etag: string | null, consoleWsId: string, seq: number) {
  if (seq <= (appliedSeq.get(o.id) ?? 0)) return;
  appliedSeq.set(o.id, seq);
  if (etag) etags.set(o.id, etag);
  const fresh = agentOutToAgent(o, consoleWsId);
  const prev = state.agents.find((x) => x.id === o.id);
  const a = prev ? { ...prev, ...pickServerOwned(fresh) } : fresh;
  const agents = [a, ...state.agents.filter((x) => x.id !== o.id)];
  set({ agents, ...rollupsFor(agents) });
}

/** The Agent fields the SERVER owns (AgentOut, feature 026 read model). Everything else is placeholder or
 *  local-only until the contract carries it, so merges must not overwrite it with placeholders. */
function pickServerOwned(a: Agent): Partial<Agent> {
  return {
    id: a.id, name: a.name, status: a.status, workspaceId: a.workspaceId, createdAt: a.createdAt,
    updatedAt: a.updatedAt, activeVersion: a.activeVersion, versions: a.versions, accessPolicy: a.accessPolicy,
    description: a.description, provider: a.provider, model: a.model, draftDirty: a.draftDirty,
    restoreStatus: a.restoreStatus, activeVersionId: a.activeVersionId, versionList: a.versionList, draft: a.draft,
    restrictedBindings: a.restrictedBindings, config: a.config,
  };
}

/** Toast a server refusal with its distinct D16 message (never a generic "failed"). */
function toastRefusal(action: string, e: unknown) {
  if (e instanceof ApiError) {
    const r = refusalMessage(e);
    toast(`${action} — ${r.title}: ${r.body}`, "critical");
  } else {
    toast(`${action} failed: ${e instanceof Error ? e.message : String(e)} (the API may be unreachable)`, "critical");
  }
}

/** The ONE live write path for an agent: PATCH with the remembered `If-Match`, then apply the response + new ETag.
 *  `build` runs at send time against the LATEST store copy, so a conflict re-apply is field-scoped (only the fields
 *  this edit touched overwrite the other writer's change). A 409 opens the conflict dialog instead of a toast. */
async function livePatch(id: string, action: string, build: (a: Agent) => AgentPatch | null, okMsg?: string): Promise<boolean> {
  const a = state.agents.find((x) => x.id === id);
  const ws = state.workspaces.find((w) => w.id === a?.workspaceId);
  if (!a || !ws?.backendId) return false;
  const body = build(a);
  if (!body) return false;
  const conflict = () => set({ editConflicts: { ...state.editConflicts, [id]: { reapply: () => livePatch(id, action, build, okMsg) } } });
  let etag = etags.get(id);
  if (!etag) {
    // 026 review-2 #3: the LIST response carries no ETag, and the conflict dialog is mounted only on the detail page —
    // so a list-page action (Archive/Restore) used to vanish silently. Never guess: load the agent (which captures its
    // ETag), then apply this edit to that fresh copy — the same thing "reload + re-apply" in the dialog does.
    try {
      await loadAgent(id, { force: true });
    } catch (e) {
      toastRefusal(action, e);
      return false;
    }
    etag = etags.get(id);
    if (!etag) { toast(`${action} failed: could not load the agent's current version — reload and try again.`, "critical"); return false; }
    const fresh = state.agents.find((x) => x.id === id);
    const rebuilt = fresh ? build(fresh) : null;
    if (!rebuilt) return false;
    return sendPatch(rebuilt, etag);
  }
  return sendPatch(body, etag);

  async function sendPatch(payload: AgentPatch, tag: string): Promise<boolean> {
    try {
      const seq = issueSeq(id);
      const { data, etag: next } = await patchAgent(callerFor(ws!), id, payload, tag);
      applyAgentOut(data, next, ws!.id, seq);
      if (okMsg) toast(okMsg);
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) { conflict(); return false; }
      toastRefusal(action, e);
      return false;
    }
  }
}

/** Resolve a 409 conflict: reload the agent (forms re-initialise via `revisions`), then optionally re-apply the
 *  refused edit on top of the latest version. */
export async function resolveEditConflict(id: string, reapply: boolean): Promise<void> {
  const c = state.editConflicts[id];
  const rest = { ...state.editConflicts };
  delete rest[id];
  set({ editConflicts: rest });
  await loadAgent(id, { force: true });
  set({ revisions: { ...state.revisions, [id]: (state.revisions[id] ?? 0) + 1 } });
  if (reapply && c) await c.reapply();
}
export const useEditConflict = (id: string) => useSelector((s) => s.editConflicts[id]);
export const useAgentRevision = (id: string) => useSelector((s) => s.revisions[id] ?? 0);

/** Versions for an agent: the live server embed, else the mock seed list. */
export const versionsOf = (a: Agent) => a.versionList ?? getVersions(a.id);

/** Live: what the console can't do yet for a server-owned agent — say so, never a fake success. */
function notAvailableYet(action: string) {
  toast(`${action} is not available yet for live agents.`, "neutral");
}

/** True when the active workspace's wired families are sourced from the API. */
export const isLiveWorkspace = (ws: Workspace | undefined) => LIVE_API && !!ws?.backendId;

// @backend wired(026) POST /api/v1/agents/:id/invoke (SSE) — the playground turn. `target` = active | draft | version
//   (+ version_id). Pre-stream refusals reject with a typed ApiError (render via refusalMessage); a stream that ends
//   without `done` resolves {interrupted:true}. The server records the run (no client addRun for live agents).
export function invokeAgentStream(
  agentId: string, body: InvokeIn, opts: { signal?: AbortSignal; onFrame: (f: InvokeFrame) => void },
): Promise<StreamResult> {
  return streamInvoke({ principal: callerFor(activeWorkspace()), agentId, body, signal: opts.signal, onFrame: opts.onFrame });
}

/** Is this agent server-owned (it lives in a live workspace)? */
export function isLiveAgent(agentId: string): boolean {
  const a = state.agents.find((x) => x.id === agentId);
  return !!a && isLiveWorkspace(state.workspaces.find((w) => w.id === a.workspaceId));
}
const warnedLocalOnly = new Set<string>();
/** Honesty guard for the agent mutations that are NOT wired yet: on a server-owned agent the change is applied to
 *  this tab only and is lost on reload — say so (once per agent per session) instead of a bare success toast.
 *  Remove each call site as its endpoint is wired. */
function warnLocalOnly(agentId: string, action: string) {
  if (!isLiveAgent(agentId) || warnedLocalOnly.has(agentId)) return;
  warnedLocalOnly.add(agentId);
  toast(`${action}: not saved to the server yet (agent edits aren't wired) — this change is local to this tab.`, "neutral");
}

// @backend remove - roleOverride/teamOverride/platformAdminOverride are dev-only preview switches; delete
//   once the real IAM session supplies role + teams + platform-admin from the OIDC `groups` claim.
//   [ref: frontend-auth-access-plan.md#part-e]
export function setRoleOverride(role: Role | null) {
  set({ roleOverride: role });
}
// Dev preview: force platform-admin on/off (null = use MOCK_PROFILE.groups). Lets the demo show the
// member experience without a real Okta directory.
export function setPlatformAdminOverride(v: boolean | null) {
  set({ platformAdminOverride: v });
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
// @backend POST /v1/workspaces/:id/archive (deactivate, unpublishes endpoints) + a NEW reactivate endpoint
//   (none exists today — flag). Reversible Inactive toggle; reuses the `archivedAt` marker. When inactive,
//   auth.tsx's can() freezes all writes (workspace-level read-only gate); resources are never mutated so
//   Activate restores everything instantly. Admin only (the Activate/Delete controls bypass the freeze).
export function setWorkspaceActive(wsId: string, active: boolean) {
  const cur = state.workspaceSettings[wsId] ?? defaultWorkspaceSettings();
  set({ workspaceSettings: { ...state.workspaceSettings, [wsId]: { ...cur, archivedAt: active ? undefined : nowIso() } } });
  toast(active ? "Workspace activated" : "Workspace deactivated — now read-only", "neutral");
}
// @backend NO create endpoint today — workspaces are org-provisioned (backend/08 has no POST /v1/workspaces).
//   Mock-only convenience: provision a space, make the creator its admin, and scaffold baseline config +
//   a starter kit so it's immediately usable. A real deploy would add POST /v1/workspaces or keep this out-of-band.
export function addWorkspace(input: { name: string; groupPrefix: string }): string {
  const name = input.name.trim();
  let id = slugify(name) || genId("ws");
  if (state.workspaces.some((w) => w.id === id)) id = `${id}-${genId("ws").split("-")[1]}`; // de-dupe
  const ws: Workspace = { id, name, short: shortOf(name), groupPrefix: input.groupPrefix.trim() };

  // Starter kit — explicit workspaceId so these land in the NEW space (not the active one). tagResource
  // stamps the default access policy. Bound counts are unaffected (the starter agent binds nothing).
  const starterAgent = tagResource<Agent>({
    id: genId("agent"), name: `Starter Agent · ${name}`,
    description: "An example draft agent. Configure it, test it in the playground, then publish.",
    status: "draft", provider: "Anthropic", model: "claude-opus-5-5", owner: MOCK_PROFILE.name,
    createdAt: nowIso(), updatedAt: nowIso(), activeVersion: "v1", versions: 1, tags: ["starter"],
    calls30d: 0, cost30d: 0, p95ms: 0, evalPass: 0, drift: "none", callsSpark: [0, 0, 0, 0, 0, 0, 0],
    knowledgeBases: [], tools: [], skills: [], workspaceId: id,
  });
  const starterKb = tagResource<KnowledgeBase>({
    id: genId("kb"), name: `Starter Knowledge Base · ${name}`, embeddingModel: "text-embedding-3-large",
    docs: 0, chunks: 0, sizeMB: 0, lastSync: nowIso(), status: "ready", boundAgents: 0, sources: [],
    description: "An empty knowledge base. Add sources and ingest to ground your agents.", workspaceId: id,
  });
  const starterTool = tagResource<RepoTool>({
    name: "http-example", kind: "HTTP", description: "An example HTTP tool. Point it at your API and bind it to an agent.",
    usedBy: 0, riskTier: "read", workspaceId: id,
  });

  // No membership overlay needed: workspace CRUD is platform-admin-only, and platform-admins see all spaces.
  set({
    workspaces: [...state.workspaces, ws],
    workspaceSettings: { ...state.workspaceSettings, [id]: defaultWorkspaceSettings() },
    limits: { ...state.limits, [id]: defaultLimits() },
    evalPolicy: { ...state.evalPolicy, [id]: defaultEvalPolicy() },
    agents: [starterAgent, ...state.agents],
    knowledgeBases: [starterKb, ...state.knowledgeBases],
    tools: [starterTool, ...state.tools],
  });
  setActiveWorkspace(id);
  toast(`Workspace "${name}" created`);
  return id;
}
// @backend PATCH /v1/workspaces/:id - update workspace identity (name, idp_binding group prefix; slug/id
//   immutable — the endpoint URL segment). Platform-admin only. [ref: backend/01 §4.1, backend/08, backend/18 §3]
export function updateWorkspace(wsId: string, patch: { name?: string; groupPrefix?: string }) {
  set({
    workspaces: state.workspaces.map((w) => {
      if (w.id !== wsId) return w;
      const name = patch.name?.trim() || w.name;
      return {
        ...w,
        name,
        short: patch.name ? shortOf(name) : w.short,
        groupPrefix: patch.groupPrefix?.trim() || w.groupPrefix,
      };
    }),
  });
  toast("Workspace saved");
}
// @backend NO hard-delete endpoint — the backend archives (soft). Mock-only cascade that removes the space
//   and EVERY resource scoped to it so no dangling references survive. providerKeys (org-level) and the
//   members roster (group-derived in the mock) are intentionally left intact. Platform-admin only.
export function deleteWorkspace(wsId: string) {
  const remaining = state.workspaces.filter((w) => w.id !== wsId);
  // Lockout guard: never delete the last workspace in the deployment.
  if (remaining.length === 0) {
    toast("Can't delete the last workspace", "neutral");
    return;
  }
  const nextActive = wsId === state.activeWorkspaceId ? remaining[0].id : state.activeWorkspaceId;

  // Name-string orphans (no workspaceId): collect the names of the KBs/agents being deleted so their
  // ingestion jobs / tokens (linked by name, not id) can be scrubbed too.
  const delKbNames = new Set(state.knowledgeBases.filter((k) => k.workspaceId === wsId).map((k) => k.name));
  const delAgentNames = new Set(state.agents.filter((a) => a.workspaceId === wsId).map((a) => a.name));

  const agents = state.agents.filter((a) => a.workspaceId !== wsId);
  const knowledgeBases = state.knowledgeBases.filter((k) => k.workspaceId !== wsId);
  const tools = state.tools.filter((t) => t.workspaceId !== wsId);
  const mcpServers = state.mcpServers.filter((m) => m.workspaceId !== wsId);
  const skills = state.skills.filter((s) => s.workspaceId !== wsId);
  const toolCounts = withBoundToolCounts(agents, tools, mcpServers); // recompute ONCE over survivors

  // tokens/ingestionJobs link by NAME (no workspaceId). A same-named resource (e.g. the "Starter Agent"
  // every new space seeds) can survive in another workspace, so only scrub a token/job whose referenced
  // name was deleted AND no longer exists among survivors — never one still backed by a surviving resource.
  const survivingAgentNames = new Set(agents.map((a) => a.name));
  const survivingKbNames = new Set(knowledgeBases.map((k) => k.name));
  const orphanedToken = (t: ApiToken) => delAgentNames.has(t.agent) && !survivingAgentNames.has(t.agent);
  const orphanedJob = (j: IngestionJob) => delKbNames.has(j.kb) && !survivingKbNames.has(j.kb);

  const nextSettings = { ...state.workspaceSettings }; delete nextSettings[wsId];
  const nextLimits = { ...state.limits }; delete nextLimits[wsId];
  const nextEvalPolicy = { ...state.evalPolicy }; delete nextEvalPolicy[wsId];

  set({
    workspaces: remaining,
    activeWorkspaceId: nextActive,
    agents,
    knowledgeBases: withBoundAgentCounts(agents, knowledgeBases),
    tools: toolCounts.tools,
    mcpServers: toolCounts.mcpServers,
    skills: withBoundSkillCounts(agents, skills),
    evalRuns: state.evalRuns.filter((e) => e.workspaceId !== wsId),
    evalSuites: state.evalSuites.filter((s) => s.workspaceId !== wsId),
    runs: state.runs.filter((r) => r.workspaceId !== wsId),
    toolApprovals: state.toolApprovals.filter((a) => a.workspaceId !== wsId),
    channels: state.channels.filter((c) => c.workspaceId !== wsId),
    ingestionJobs: state.ingestionJobs.filter((j) => !orphanedJob(j)),
    tokens: state.tokens.filter((t) => !orphanedToken(t)),
    workspaceSettings: nextSettings,
    limits: nextLimits,
    evalPolicy: nextEvalPolicy,
  });
  toast("Workspace deleted", "neutral");
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
// TODO(backend): there is NO baseline re-freeze endpoint — the baseline is only an input field of
//   POST /api/v1/agents/:id/eval-runs (EvalRunIn.baseline). Build the audited re-freeze (backend/05 §4), then wire.
export function reFreezeBaseline(agentId: string) {
  warnLocalOnly(agentId, "Re-freeze baseline");
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
// @backend PUT /v1/{agents|knowledge|tools|mcp|channels|skills}/:id/access-policy - persist per-resource
//   policy; server enforces it on read/manage and at invoke. [ref: frontend-auth-access-plan.md#part-b2]
/** Admin-configurable per-resource access policy (Part B2). */
export function setAccessPolicy(
  kind: "agent" | "knowledge" | "tool" | "mcp" | "channel" | "skill",
  id: string,
  policy: AccessPolicy,
) {
  // A private resource is owner-scoped: stamp the current user as owner if the editor didn't supply one.
  const stamped: AccessPolicy =
    policy.visibility === "private" && !policy.owner ? { ...policy, owner: MOCK_PROFILE.email } : policy;
  const apply = <T extends WithTenant>(arr: T[]) => arr.map((r) => (keyOf(r) === id ? { ...r, accessPolicy: stamped } : r));
  // TODO(fe-wiring): for a live agent, persist via PATCH /api/v1/agents/:id {access_policy} (BUILT; needs If-Match).
  if (kind === "agent") warnLocalOnly(id, "Access policy");
  if (kind === "agent") set({ agents: apply(state.agents) });
  else if (kind === "knowledge") set({ knowledgeBases: apply(state.knowledgeBases) });
  else if (kind === "tool") set({ tools: apply(state.tools) });
  else if (kind === "channel") set({ channels: apply(state.channels) });
  else if (kind === "skill") set({ skills: apply(state.skills) });
  else set({ mcpServers: apply(state.mcpServers) });
  toast("Access policy updated");
}

/* ------------------------------------------------------------- members (read-only)
   @backend membership is CLAIM-driven — the roster is a read-only projection of the workspace's per-role
   claims (GET /v1/members?workspace=:id, adapter projection). No invite/remove in-app: add/remove happens
   in the IdP. [ref: backend/18 §4] */

/* --------------------------------------------------------------- actions */
// POST /api/v1/agents - create a draft agent in the active workspace; resolves to the created id.
//   Live workspace: the SERVER creates it (and assigns the id) — the store reflects the response, never an
//   optimistic guess, and a rejection (400/403/…) is toasted and re-thrown so the form stays put.
//   Mock workspace: the in-memory create, as before (resolves immediately with the client id).
//   NOTE: the console form's richer fields (description, tags, KB/tool/skill bindings, config beyond the system
//   prompt) are not in AgentCreate yet — they are kept on the local copy so the UI shows what was entered, but are
//   NOT persisted server-side (contract gap; see adapters.ts).
// TODO(fe-wiring): once AgentCreate/versions carry them, persist the form's KB/tool/skill bindings + full config
//   (POST …/versions with bindings) instead of keeping them local-only.
export async function addAgent(a: Agent, opts: { idempotencyKey?: string } = {}): Promise<string> {
  const ws = state.workspaces.find((w) => w.id === (a.workspaceId ?? state.activeWorkspaceId));
  let stamped: Agent;
  if (isLiveWorkspace(ws)) {
    try {
      // One Idempotency-Key per form submission (the caller keeps it stable across a retry of the same draft): the
      // backend's guard (D8) rejects a concurrent duplicate with 409 instead of creating a second agent.
      // NOTE: the backend guard is in-flight-only + process-local today (completed-key replay deferred) — see
      // the TODO on services/api/deps.py idempotency_guard.
      const out = await createAgent(callerFor(ws), agentToCreate(a), {
        idempotencyKey: opts.idempotencyKey ?? crypto.randomUUID(),
      });
      createdLocallyAt.set(out.id, Date.now());
      // Server owns identity + lifecycle; the form's display fields ride along locally (see NOTE above).
      stamped = { ...a, ...pickServerOwned(agentOutToAgent(out, ws!.id)) };
    } catch (e) {
      toast(`Create failed: ${e instanceof Error ? e.message : String(e)}`, "critical");
      throw e;
    }
  } else {
    stamped = tagResource({ ...a, workspaceId: a.workspaceId ?? state.activeWorkspaceId });
  }
  const agents = [stamped, ...state.agents.filter((x) => x.id !== stamped.id)];
  set({ agents, ...rollupsFor(agents) });
  toast(`Agent "${stamped.name}" created as a draft`);
  return stamped.id;
}
// @backend PATCH /v1/agents/:id - generic field patch + bind knowledge/tools. Lifecycle transitions
//   (publish/unpublish/archive/restore) and version rollback go through the dedicated guarded wrappers
//   below (publishAgent/unpublishAgent/archiveAgent/restoreAgent/rollbackVersion), which map to the same
//   endpoint but centralize the passing-eval guard.
// @backend wired(026) live: name/description → PATCH (If-Match); provider/model → PATCH {draft}. Tags and
//   bindings (knowledge/tools/skills families are still MOCK) are "not available yet" for a live agent.
export function updateAgent(id: string, patch: Partial<Agent>, notify = true): Promise<boolean> {
  if (isLiveAgent(id)) return updateAgentLive(id, patch, notify);
  // A behavior-changing (trigger) field edit flags the agent for re-eval (publishable proxy, backend/05 §5),
  // unless the caller set needsReEval explicitly (e.g. a rollback that clears it).
  const TRIGGER_FIELDS: (keyof Agent)[] = ["provider", "model", "knowledgeBases", "tools", "skills"];
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
  if ("skills" in patch) extra.skills = withBoundSkillCounts(agents, state.skills);
  set({ agents, ...extra });
  if (notify) toast("Changes saved");
  return Promise.resolve(true);
}

const LIVE_UNSUPPORTED_AGENT_FIELDS: (keyof Agent)[] = ["tags", "knowledgeBases", "tools", "skills"];
async function updateAgentLive(id: string, patch: Partial<Agent>, notify: boolean): Promise<boolean> {
  const blocked = LIVE_UNSUPPORTED_AGENT_FIELDS.filter((k) => k in patch);
  const a = state.agents.find((x) => x.id === id);
  if (blocked.includes("tags") && a && JSON.stringify(patch.tags) === JSON.stringify(a.tags)) blocked.splice(blocked.indexOf("tags"), 1);
  if (blocked.length) notAvailableYet(`Editing ${blocked.join(", ")}`);
  let ok = true;
  const meta: AgentPatch = {};
  if (patch.name !== undefined && patch.name !== a?.name) meta.name = patch.name;
  if (patch.description !== undefined && patch.description !== a?.description) meta.description = patch.description;
  if (Object.keys(meta).length) ok = await livePatch(id, "Save", () => meta, notify ? "Changes saved" : undefined);
  if (ok && (patch.provider !== undefined || patch.model !== undefined)) {
    ok = await saveLiveDraft(id, { provider: patch.provider, model: patch.model }, notify);
  }
  return ok;
}

/** Live: save draft-config edits as ONE PATCH {draft}. Only fields that differ from the RAW server draft are sent.
 *  Review #9: diffing against `resolveConfig` compared an unset param with the console DEFAULT, so deliberately
 *  choosing that default was silently dropped. Untouched defaults are kept out by the caller (it passes a param only
 *  when the user edited it), not by comparing with a value the server never stored. */
export function saveLiveDraft(id: string, edit: DraftEdit, notify = true): Promise<boolean> {
  return livePatch(id, "Save draft", (a) => {
    if (!a.draft) { toast("This agent has no draft to edit yet.", "critical"); return null; }
    const d = a.draft;
    const io = d.io_contract as { input_schema?: unknown; output_format?: unknown };
    const e: DraftEdit = {};
    if (edit.provider !== undefined && providerKey(edit.provider) !== d.provider) e.provider = edit.provider;
    if (edit.model !== undefined && edit.model !== d.model) e.model = edit.model;
    if (edit.systemPrompt !== undefined && edit.systemPrompt !== d.system_prompt) e.systemPrompt = edit.systemPrompt;
    if (edit.temperature !== undefined && edit.temperature !== d.model_params.temperature) e.temperature = edit.temperature;
    if (edit.maxOutputTokens !== undefined && Math.round(edit.maxOutputTokens) !== d.model_params.max_tokens) e.maxOutputTokens = edit.maxOutputTokens;
    if (edit.outputFormat !== undefined && edit.outputFormat !== io.output_format) e.outputFormat = edit.outputFormat;
    if (edit.inputVariables !== undefined && JSON.stringify(edit.inputVariables) !== JSON.stringify(inputVariablesFromSchema(io.input_schema))) e.inputVariables = edit.inputVariables;
    if (!Object.keys(e).length) return null;
    return { draft: draftWithEdit(a.draft, e) };
  }, notify ? "Draft saved — create a version to freeze it" : undefined);
}

/** Live: freeze the draft into a new immutable version (POST …/versions). Not made active — re-point separately. */
// @backend wired(026) POST /api/v1/agents/:id/versions — replaces the toast-only SaveVersionDialog path.
export async function createVersion(id: string, label?: string): Promise<boolean> {
  const a = state.agents.find((x) => x.id === id);
  const ws = state.workspaces.find((w) => w.id === a?.workspaceId);
  if (!a || !ws?.backendId) return false;
  try {
    const v = await apiCreateVersion(callerFor(ws), id, label?.trim() || undefined);
    await loadAgent(id, { force: true });
    toast(`Version ${v.label} created — it is not active until you re-point to it`);
    return true;
  } catch (e) {
    toastRefusal("Create version", e);
    return false;
  }
}
// @backend PATCH /v1/agents/:id - persist draft config (model params, prompt, memory, retrieval, spend).
//   Guardrails specifically map to PUT /v1/agents/:id/guardrails (per-version config) per
//   docs/backend-integration-map.md. Deep-merges into agent.config (updateAgent is shallow); the merged
//   result is a full AgentConfig stored back into the Partial<AgentConfig> field.
// @backend wired(026) live: prompt / temperature / max tokens / io contract → PATCH {draft} (saveLiveDraft). The
//   rest of AgentConfig (reasoning, fallback, citations, memory, retrieval, guardrails, spend) has no server field in
//   026 → "not available yet" for a live agent, never a fake save.
const LIVE_DRAFT_CONFIG_KEYS = ["systemPrompt", "temperature", "maxOutputTokens", "inputVariables", "outputFormat"] as const;
export function updateAgentConfig(id: string, patch: Partial<AgentConfig>, notify = true): Promise<boolean> {
  if (isLiveAgent(id)) {
    const unsupported = Object.keys(patch).filter((k) => !(LIVE_DRAFT_CONFIG_KEYS as readonly string[]).includes(k));
    if (unsupported.length) notAvailableYet(`Saving ${unsupported.join(", ")}`);
    const edit: DraftEdit = {};
    for (const k of LIVE_DRAFT_CONFIG_KEYS) if (patch[k] !== undefined) (edit as Record<string, unknown>)[k] = patch[k];
    return Object.keys(edit).length ? saveLiveDraft(id, edit, notify) : Promise.resolve(false);
  }
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
  return Promise.resolve(true);
}
// @backend PATCH /v1/agents/:id - lifecycle transitions below are dedicated wrappers over the same
//   endpoint; they centralize the guards (publish requires a passing eval) the UI previously duplicated.
/** Publish the active version. Blocked when the agent is eval_failing. Returns false if blocked. */
// TODO(fe-wiring): wire to POST /api/v1/agents/:id/publish (BUILT, advisory verdict) — the server's eval gate
//   should replace the client-side policy check below. Local-only today.
export function publishAgent(id: string): boolean {
  warnLocalOnly(id, "Publish");
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
// Live: there is no unpublish transition in 026 (the server's lifecycle is archive/restore + publish) → not available.
export function unpublishAgent(id: string) {
  if (isLiveAgent(id)) { notAvailableYet("Unpublish"); return; }
  updateAgent(id, { status: "draft" }, false);
  toast("Agent unpublished", "neutral");
}
// @backend wired(026) lifecycle: archive / restore are PATCH status (If-Match). Restore returns to the server's
//   `restore_status` (the status before archive), audited server-side as agent.archive / agent.restore.
/** Archive (hide from active lists; reversible). */
export function archiveAgent(id: string) {
  if (isLiveAgent(id)) { void livePatch(id, "Archive", () => ({ status: "archived" }), "Agent archived"); return; }
  updateAgent(id, { status: "archived" }, false);
  toast("Agent archived", "neutral");
}
/** Restore an archived agent (mock: back to draft; live: to the server's restore_status). */
export function restoreAgent(id: string) {
  if (isLiveAgent(id)) {
    void livePatch(id, "Restore", (a) => ({ status: a.restoreStatus ?? "draft" }), "Agent restored");
    return;
  }
  updateAgent(id, { status: "draft" }, false);
  toast("Agent restored");
}
/** Repoint the active version. Live: PATCH active_version_id by version **id** (labels are display-only). Mock:
 *  validates the label against the agent's known versions. */
// @backend wired(026) re-point = PATCH active_version_id (If-Match), audited server-side as agent.repoint.
export function rollbackVersion(id: string, label: string, versionId?: string) {
  if (isLiveAgent(id)) {
    if (!versionId) { toast(`Version "${label}" has no server id — reload the agent`, "critical"); return; }
    void livePatch(id, "Re-point", () => ({ active_version_id: versionId }), `Re-pointed to ${label}`);
    return;
  }
  const a = state.agents.find((x) => x.id === id);
  if (!(a ? versionsOf(a) : getVersions(id)).some((v) => v.label === label)) {
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
// TODO(backend): DELETE /api/v1/agents/:id is DEFERRED server-side (agents router docstring) — build it, then wire
//   here. Until then a delete of a live agent is local-only and the agent reappears on the next list refresh.
export function deleteAgent(id: string): boolean {
  if (isLiveAgent(id)) { notAvailableYet("Delete"); return false; }
  const a = state.agents.find((x) => x.id === id);
  const agents = state.agents.filter((x) => x.id !== id);
  const toolCounts = withBoundToolCounts(agents, state.tools, state.mcpServers);
  set({
    agents,
    // The deleted agent's bindings are gone → decrement affected KB / tool / MCP counts.
    knowledgeBases: withBoundAgentCounts(agents, state.knowledgeBases),
    tools: toolCounts.tools,
    mcpServers: toolCounts.mcpServers,
    skills: withBoundSkillCounts(agents, state.skills),
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
  return true;
}
// @backend POST /v1/agents/:id/duplicate - server copies config into a new draft; returns new id.
// TODO(backend): POST /api/v1/agents/:id/duplicate is DEFERRED server-side — build it (or compose create +
//   versions client-side), then wire. Local-only today.
export function duplicateAgent(id: string): string | null {
  if (isLiveAgent(id)) { notAvailableYet("Duplicate"); return null; }
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
  set({ agents, knowledgeBases: withBoundAgentCounts(agents, state.knowledgeBases), tools: toolCounts.tools, mcpServers: toolCounts.mcpServers, skills: withBoundSkillCounts(agents, state.skills) });
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
  const prev = state.mcpServers.find((s) => s.id === id);
  const mcpServers = state.mcpServers.map((s) => (s.id === id ? { ...s, ...patch } : s));
  // auth_mode is a DRIFT_TRIGGER (doc 17 §2 / 05 §5): flipping shared_service↔per_user changes the frozen
  // tool_bindings snapshot, so it invalidates every bound agent's eval. Mirror the KB/skill fan-out.
  // auth_mode AND the per-user OAuth scopes change the frozen tool_bindings grant surface → DRIFT_TRIGGER.
  const driftRelevant = !!prev && (
    (patch.authMode !== undefined && patch.authMode !== prev.authMode) ||
    (patch.oauthScopes !== undefined && patch.oauthScopes !== prev.oauthScopes)
  );
  const prefix = `${MCP_REF_PREFIX}${id}/`;
  set({
    mcpServers,
    ...(driftRelevant
      ? { agents: state.agents.map((a) => (a.tools.some((ref) => ref.startsWith(prefix)) ? { ...a, needsReEval: true } : a)) }
      : {}),
  });
  if (notify) toast(driftRelevant ? "MCP server updated — bound agents need re-eval" : "MCP server updated");
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
  const prev = state.tools.find((t) => t.name === name);
  const tools = state.tools.map((t) => (t.name === name ? { ...t, ...patch } : t));
  // A tool's auth-mode OR per-user OAuth scopes change the frozen tool_bindings grant surface → DRIFT_TRIGGER.
  const driftRelevant = patch.httpConfig !== undefined && (
    (prev?.httpConfig?.authMode ?? "shared_service") !== (patch.httpConfig?.authMode ?? "shared_service") ||
    (prev?.httpConfig?.oauthScopes ?? "") !== (patch.httpConfig?.oauthScopes ?? "")
  );
  set({
    tools,
    ...(driftRelevant
      ? { agents: state.agents.map((a) => (a.tools.includes(name) ? { ...a, needsReEval: true } : a)) }
      : {}),
  });
  toast(driftRelevant ? "Tool updated — bound agents need re-eval" : "Tool updated");
}
// @backend DELETE /v1/tools/:id - remove a repository tool. Agent bindings by this name become dangling
//   refs (resolveToolRef renders them as removable).
export function deleteTool(name: string) {
  set({ tools: state.tools.filter((t) => t.name !== name) });
  toast(`Tool "${name}" removed`, "neutral");
}
/* ------------------------------------------------------------------ skills */
// @backend POST /v1/skills - create a draft skill (instruction + bundled resources) in active workspace.
//   Skills do not execute code; no sandbox/credential capture. [ref: backend/08 Skills, backend/01 §4.4]
export function addSkill(sk: Skill, notify = true) {
  const stamped = tagResource({ ...sk, workspaceId: sk.workspaceId ?? state.activeWorkspaceId });
  set({ skills: [stamped, ...state.skills] });
  // notify=false lets a create-then-publish flow suppress the transient "created as a draft" toast.
  if (notify) toast(`Skill "${stamped.name}" created as a draft`);
}
// @backend PUT /v1/skills/:id - edit a skill. A content change (instructions/resources/version) freezes a
//   new content hash into the version snapshot_hash of every bound agent → drift trigger, so flag those
//   agents for re-eval, mirroring a KB re-ingest. [ref: backend/05 §5 DRIFT_TRIGGERS.skill_bindings, §6]
export function updateSkill(id: string, patch: Partial<Skill>, notify = true) {
  const contentChanged = "instructions" in patch || "resources" in patch || "version" in patch || "contentHash" in patch;
  const skills = state.skills.map((s) => (s.id === id ? { ...s, ...patch, lastUpdated: nowIso() } : s));
  set({
    skills,
    ...(contentChanged
      ? { agents: state.agents.map((a) => ((a.skills ?? []).includes(id) ? { ...a, needsReEval: true } : a)) }
      : {}),
  });
  if (notify) toast("Skill updated");
}
// @backend POST /v1/skills/:id/publish - sign + publish a skill so it becomes bindable. Signing stamps
//   provenance (signed_by/at + masked signature). Re-publishing edited content re-freezes the hash, so
//   bound agents are flagged for re-eval. [ref: backend/09 §10 provenance/signing, backend/05 §6]
export function publishSkill(id: string) {
  const skills = state.skills.map((s) =>
    s.id === id
      ? {
          ...s,
          status: "published" as const,
          signature: { signedBy: MOCK_PROFILE.name, signedAt: nowIso(), masked: `sig:${genId("sig").slice(-8)}` },
          lastUpdated: nowIso(),
        }
      : s,
  );
  set({
    skills,
    // A (re)publish re-freezes the skill's content identity → bound agents need re-eval.
    agents: state.agents.map((a) => ((a.skills ?? []).includes(id) ? { ...a, needsReEval: true } : a)),
  });
  toast("Skill published & signed");
}
// @backend DELETE /v1/skills/:id - delete a skill. Agent bindings by this id become dangling refs
//   (the detail/picker render them as removable, like a deleted tool).
export function deleteSkill(id: string) {
  const sk = state.skills.find((s) => s.id === id);
  set({ skills: state.skills.filter((s) => s.id !== id) });
  toast(`Skill "${sk?.name ?? id}" deleted`, "neutral");
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
//   Live agents (026 US2): the playground streams the real invoke (`invokeAgentStream`) and NEVER calls this — the
//   server records the run, and a pre-stream refusal (402 budget.exceeded, 429, 403, 404, 503) records none and is
//   rendered through refusals.ts. Mock-only callers remain (e.g. the channel test send).
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
// @backend remove - dev-only preview of the platform-admin dimension (null = use the OIDC `groups` claim).
export const usePlatformAdminOverride = () => useSelector((s) => s.platformAdminOverride);

/* live API: load status + mount-time loaders (no-ops unless NEXT_PUBLIC_API_MODE=live) */
export const useRemoteStatus = (key: string) => useSelector((s) => s.remote[key] ?? IDLE);
/** Load status of the active workspace's agent list. */
export const useAgentsStatus = () => useSelector((s) => s.remote[`agents:${s.activeWorkspaceId}`] ?? IDLE);
const useActiveBackendId = () =>
  useSelector((s) => s.workspaces.find((w) => w.id === s.activeWorkspaceId)?.backendId);
/** True when the active workspace's wired families come from the API (drives the Live/Mock badge). */
export function useIsLiveWorkspace(): boolean {
  const backendId = useActiveBackendId();
  return LIVE_API && !!backendId;
}
/** Mount once at the app boundary: workspace discovery. */
export function useEnsureWorkspaces() {
  useEffect(() => { void loadWorkspaces(); }, []);
}
/** Mount in a page that lists agents. Re-runs on workspace switch AND when discovery assigns the backendId
 *  (so a cold reload works without ordering assumptions). */
export function useEnsureAgents() {
  const ws = useActiveWorkspaceId();
  const backendId = useActiveBackendId();
  useEffect(() => { void loadAgents(); }, [ws, backendId]);
}
/** Mount in an agent detail page (deep link / reload). */
export function useEnsureAgent(id: string) {
  const ws = useActiveWorkspaceId();
  const backendId = useActiveBackendId();
  useEffect(() => { void loadAgent(id); }, [id, ws, backendId]);
}

/* workspace-scoped lists
   @backend reads - every selector below replaces its mock store slice with a workspace-scoped API
   GET (filter by the active workspace server-side); the server also applies access-policy visibility
   so restricted resources never reach a caller who can't use them. [ref: backend-integration-plan.md#part-2] */
// @backend GET /v1/agents?workspace=:id - list agents for active workspace.
//   Live: the hook itself triggers the (de-duplicated, freshness-windowed) load, so EVERY consumer — dashboard,
//   playground, evaluations, settings, pickers — gets the server list, not just the agents page.
export function useAgents() {
  useEnsureAgents();
  return useSelector((s) => inWs(s.agents, s.activeWorkspaceId));
}
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
// @backend GET /v1/skills?workspace=:id - list skills (instruction+resource capability packages).
export const useSkills = () => useSelector((s) => inWs(s.skills, s.activeWorkspaceId));
// @backend GET /v1/skills/:id - single skill (instructions, resources, signing/provenance, access).
export const useSkill = (id: string) => useSelector((s) => s.skills.find((sk) => sk.id === id));
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
// @backend GET /v1/members?workspace=:id - READ-ONLY roster projected from the workspace's Okta group.
//   Identity + membership come from Okta; no in-app invite/remove.
export const useMembers = () => useSelector((s) => s.members);
// @backend GET /v1/workspaces (the user's Okta group memberships) - store-backed registry (mutable in the mock).
export const useWorkspaces = () => useSelector((s) => s.workspaces);
// Active (vs. Inactive/read-only) flag for a workspace — drives the read-only freeze gate (auth.tsx can()).
export const useWorkspaceActiveFor = (wsId: string) => useSelector((s) => !s.workspaceSettings[wsId]?.archivedAt);
// Stable fallbacks: useSelector caches by reference, so returning a fresh default object each call
// would loop. All workspaces are seeded, so these only guard an unseeded tenant.
const FALLBACK_WS_SETTINGS = defaultWorkspaceSettings();
const FALLBACK_LIMITS = defaultLimits();
const FALLBACK_SPEND = defaultWorkspaceSpend();
const FALLBACK_EVAL_POLICY = defaultEvalPolicy();
// @backend GET /v1/workspaces/:id - active workspace config (name, default provider/model, archived).
export const useWorkspaceSettings = () => useSelector((s) => s.workspaceSettings[s.activeWorkspaceId] ?? FALLBACK_WS_SETTINGS);
// @backend GET /v1/workspaces/:id/limits - active workspace spend/concurrency/timeout ceilings.
export const useLimits = () => useSelector((s) => s.limits[s.activeWorkspaceId] ?? FALLBACK_LIMITS);
// @backend GET /v1/workspace/spend - active workspace month-to-date spend vs the monthly cap (feature 009,
//   M6). Server-derived from the durable run-cost ledger; mock reads a static seed. Not store-mutated.
export const useWorkspaceSpend = (): WorkspaceSpend =>
  useSelector((s) => WORKSPACE_SPEND[s.activeWorkspaceId] ?? FALLBACK_SPEND);
// @backend GET /v1/workspaces/:id/eval-policy - admin-governed verdict/publish thresholds (eval.govern).
export const useEvalPolicy = () => useSelector((s) => s.evalPolicy[s.activeWorkspaceId] ?? FALLBACK_EVAL_POLICY);
// Agent-scoped eval policy: resolve from the AGENT's workspace, not the active one, so the UI matches
// publishAgent()'s gate (which reads evalPolicy[agent.workspaceId]) for a cross-workspace agent.
export const useEvalPolicyFor = (workspaceId?: string) =>
  useSelector((s) => s.evalPolicy[workspaceId ?? s.activeWorkspaceId] ?? FALLBACK_EVAL_POLICY);
// @backend none - toasts are local UI state.
export const useToasts = () => useSelector((s) => s.toasts);
