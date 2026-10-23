import type { Agent, McpServer, Skill } from "./data";
import type { ChannelPlatform } from "./channels";
import type { Role } from "./permissions";
import { canUse, type Principal, type CallerRole } from "./access-policy";
import { parseMcpRef } from "./tools";
import { DEFAULT_WORKSPACE_ID } from "./iam";

export type RunCtx =
  | { mode: "session" }
  | { mode: "token"; token: string }
  // A messaging channel: the platform-asserted user has already been federated to an Okta identity and a
  // short-lived token minted server-side (see docs/channels-design.md §2, backend/12 §4). So a federated
  // human arrives as a real "user" principal and a service account as "service" — no new subject class.
  // `teams` are the user's Okta groups (same model as the console, lib/iam.ts). The channel caller resolves
  // to the execute-only `executer` role (set in resolveRun, NOT carried on the ctx); it needs NO workspace
  // membership — authorization is channel okta_group ∩ agent accessPolicy.
  | { mode: "channel"; platform: ChannelPlatform; identityMode: "federated" | "service"; user?: string; tenant: string; teams: string[] };
export type Session = { tenant: string; role: Role; teams: string[] };

// Caller role (backend/09 §1): the token + channel planes resolve to the execute-only `executer` baseline
// (a fixed invoke role, NOT a console RBAC tier); session ("run as me") keeps the console role. This is a
// Playground PREVIEW — the server is authoritative. See access-policy.ts `CallerRole`.

export type RunStatus = "ok" | "expired" | "revoked" | "tenant_mismatch" | "policy_denied";
export type Denied = { name: string; reason: string };

export type RunResolution = {
  subjectType: "user" | "service";
  tenant: string;
  role: CallerRole;   // "executer" for token/channel callers; the console Role for a session ("run as me")
  teams: string[];
  scopes: string[];
  expiry: string;
  status: RunStatus;
  grantedTools: string[];
  deniedTools: Denied[];
  // per_user MCP tools (doc 17) the caller may use only after connecting their OWN account first — a
  // distinct state from granted/denied. Populated for interactive callers (console / channel-federated);
  // token & service callers get these in deniedTools instead (no interactive connect path, doc 17 §6).
  connectTools: Denied[];
  grantedKbs: string[];
  deniedKbs: Denied[];
  grantedSkills: string[];
  deniedSkills: Denied[];
};

// @backend POST /v1/agents/:id/invoke (auth) - at real invoke time the server validates the caller's
//   Trellix IAM JWT (signature, tenant, role, teams, scopes, expiry, revocation) and computes the
//   effective access = agent config ∩ caller grants ∩ tenant ∩ resource policy. This client decode is
//   a Playground preview only; keep the RunResolution shape, replace decodeToken with a /resolve-run
//   (or token-introspection) call. [ref: backend-integration-plan.md#runtime-request]
/** Deterministic mock decode of a pasted token. Real IAM validates a JWT and reads claims. */
function decodeToken(token: string, agent: Agent): Omit<RunResolution, "grantedTools" | "deniedTools" | "connectTools" | "grantedKbs" | "deniedKbs" | "grantedSkills" | "deniedSkills"> {
  const t = token.toLowerCase();
  const subjectType: "user" | "service" = t.includes("user") ? "user" : "service";
  const tenant = t.includes("grc") ? "trellix-grc" : t.includes("labs") ? "trellix-labs" : (agent.workspaceId ?? DEFAULT_WORKSPACE_ID);
  const role: CallerRole = "executer";   // a caller token always resolves to the execute-only role (09 §1) — never a console tier
  const teams = t.includes("soc") ? ["SOC"] : t.includes("grc") ? ["GRC"] : t.includes("plat") ? ["Platform"] : [];
  const status: RunStatus = t.includes("expired") ? "expired" : t.includes("revoked") ? "revoked"
    : tenant !== (agent.workspaceId ?? DEFAULT_WORKSPACE_ID) ? "tenant_mismatch" : "ok";
  const expiry = t.includes("expired") ? "expired 2d ago" : "in 29 days";
  // scope breadth: "full" grants all; otherwise a deterministic partial subset
  const full = t.includes("full");
  const scopes = full ? ["*"] : ["partial"];
  return { subjectType, tenant, role, teams, scopes, expiry, status };
}

export function resolveRun(ctx: RunCtx, agent: Agent, session: Session, mcpServers: McpServer[] = [], skills: Skill[] = []): RunResolution {
  const agentTenant = agent.workspaceId ?? DEFAULT_WORKSPACE_ID;
  const base = ctx.mode === "session"
    ? {
        subjectType: "user" as const, tenant: session.tenant, role: session.role, teams: session.teams,
        scopes: ["*"], expiry: "session",
        // Even run-as-me must match the agent's tenant (e.g. opening another workspace's agent by URL).
        status: (session.tenant === agentTenant ? "ok" : "tenant_mismatch") as RunStatus,
      }
    : ctx.mode === "channel"
    ? {
        // Federated Okta user (or service account): a real principal with full invoke scope — access is
        // gated by the agent's accessPolicy below (and by the channel's Okta group upstream), not by a
        // partial token. The channel okta_group ∩ agent accessPolicy is the end-user access rule.
        subjectType: (ctx.identityMode === "service" ? "service" : "user") as "user" | "service",
        tenant: ctx.tenant, role: "executer" as CallerRole, teams: ctx.teams,
        scopes: ["*"], expiry: ctx.identityMode === "service" ? "service account" : "minted · 15m",
        status: (ctx.tenant === agentTenant ? "ok" : "tenant_mismatch") as RunStatus,
      }
    : decodeToken(ctx.token, agent);

  // Run-time callers (token/channel) resolve to `executer` and carry no `subject`, so a `private` agent
  // (owner-only) is denied here for every caller — the intended fail-closed behavior (access-policy.ts).
  // (admin is a console claim, not a caller role, so there is no admin-token bypass at invoke.)
  const principal: Principal = { role: base.role, teams: base.teams };
  const tools = agent.tools ?? [];
  const kbs = agent.knowledgeBases ?? [];
  const boundSkills = agent.skills ?? [];

  // whole-run failures short-circuit resource grants
  let status = base.status;
  if (status === "ok" && agent.accessPolicy && !canUse(agent.accessPolicy, principal)) {
    status = "policy_denied";
  }
  const blocked = status !== "ok";

  const full = base.scopes.includes("*");
  const grantedTools: string[] = [];
  const deniedTools: Denied[] = [];
  const connectTools: Denied[] = [];
  const grantedKbs: string[] = [];
  const deniedKbs: Denied[] = [];
  const grantedSkills: string[] = [];
  const deniedSkills: Denied[] = [];

  // Display label for a bound tool ref: MCP refs (`mcp:<serverId>/<tool>`) show "tool · Server".
  const toolLabel = (ref: string) => {
    const m = parseMcpRef(ref);
    if (!m) return ref;
    const s = mcpServers.find((x) => x.id === m.serverId);
    return s ? `${m.tool} · ${s.name}` : m.tool;
  };
  // An MCP ref is additionally denied when its owning server is restricted/removed for this caller.
  // No-op when no server list is supplied (can't evaluate → don't spuriously deny).
  const serverBlockedReason = (ref: string): string | null => {
    const m = parseMcpRef(ref);
    if (!m || mcpServers.length === 0) return null;
    const s = mcpServers.find((x) => x.id === m.serverId);
    if (!s) return "MCP server removed";
    if (!canUse(s.accessPolicy, principal)) return "MCP server not accessible";
    return null;
  };
  // per_user MCP (doc 17): the caller must connect their own account before this tool is usable.
  // Interactive callers can do it in-session; a published token / service account cannot (no interactive path).
  const isPerUserMcp = (ref: string): boolean => {
    const m = parseMcpRef(ref);
    if (!m) return false;
    const s = mcpServers.find((x) => x.id === m.serverId);
    return !!s && s.authMode === "per_user";
  };
  const interactiveCaller = ctx.mode === "session" || (ctx.mode === "channel" && base.subjectType === "user");
  const connectReason = ctx.mode === "channel" ? "connect via chat on first use" : "connect your account on first use";
  // A bound skill is denied when it was removed or is restricted by its own access policy for this caller.
  // Skills never execute code, so this is a pure visibility check (no scope breadth like tools/KBs).
  // No-op when no skill list is supplied (can't evaluate → don't spuriously deny; fall back to the id).
  const skillLabel = (id: string) => skills.find((sk) => sk.id === id)?.name ?? id;
  const skillBlockedReason = (id: string): string | null => {
    if (skills.length === 0) return null;
    const sk = skills.find((x) => x.id === id);
    if (!sk) return "Skill removed";
    if (!canUse(sk.accessPolicy, principal)) return "Skill not accessible";
    return null;
  };

  if (!blocked) {
    tools.forEach((ref, i) => {
      const label = toolLabel(ref);
      const serverReason = serverBlockedReason(ref);
      if (serverReason) { deniedTools.push({ name: label, reason: serverReason }); return; }
      // per_user MCP: grantable only after the caller connects their own account (doc 17). Interactive
      // callers get a "connect" state; token/service callers fail closed (no interactive connect path).
      if (isPerUserMcp(ref)) {
        if (interactiveCaller) connectTools.push({ name: label, reason: connectReason });
        else deniedTools.push({ name: label, reason: "per-user credentials — no interactive connect for this caller" });
        return;
      }
      // session or "full" token grants all; partial token grants every other tool
      const inScope = full || i % 2 === 0;
      if (inScope) grantedTools.push(label);
      else deniedTools.push({ name: label, reason: "not in token scope" });
    });
    kbs.forEach((id, i) => {
      const inScope = full || i % 2 === 0;
      if (inScope) grantedKbs.push(id);
      else deniedKbs.push({ name: id, reason: "not in token scope" });
    });
    boundSkills.forEach((id) => {
      const reason = skillBlockedReason(id);
      if (reason) deniedSkills.push({ name: skillLabel(id), reason });
      else grantedSkills.push(skillLabel(id));
    });
  } else {
    // Whole run is blocked: nothing executes, but surface what WOULD be used so the access
    // preview shows the agent's bound resources (struck through) instead of "No tools bound".
    const reason = statusLabel[status];
    tools.forEach((ref) => deniedTools.push({ name: toolLabel(ref), reason }));
    kbs.forEach((id) => deniedKbs.push({ name: id, reason }));
    boundSkills.forEach((id) => deniedSkills.push({ name: skillLabel(id), reason }));
  }

  return { ...base, status, grantedTools, deniedTools, connectTools, grantedKbs, deniedKbs, grantedSkills, deniedSkills };
}

export const statusLabel: Record<RunStatus, string> = {
  ok: "Access granted",
  expired: "Token expired",
  revoked: "Token revoked",
  tenant_mismatch: "Token belongs to another tenant",
  policy_denied: "Denied by the agent's access policy",
};
