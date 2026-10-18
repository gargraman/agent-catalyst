import type { Agent } from "./data";
import type { ChannelPlatform } from "./channels";
import type { Role } from "./permissions";
import { canUse, type Principal } from "./access-policy";
import { DEFAULT_WORKSPACE_ID } from "./iam";

export type RunCtx =
  | { mode: "session" }
  | { mode: "token"; token: string }
  // A messaging channel: the platform-asserted user has already been federated to an Okta identity and a
  // short-lived token minted server-side (see docs/channels-design.md §2, backend/12 §4). So a federated
  // human arrives as a real "user" principal and a service account as "service" — no new subject class.
  // `teams` are the user's Okta groups (same model as the console, lib/iam.ts); `role` is a fixed baseline
  // invoke role, NOT a console RBAC tier. The channel user needs NO workspace membership — authorization is
  // channel okta_group ∩ agent accessPolicy.
  | { mode: "channel"; platform: ChannelPlatform; identityMode: "federated" | "service"; user?: string; tenant: string; role: Role; teams: string[] };
export type Session = { tenant: string; role: Role; teams: string[] };

export type RunStatus = "ok" | "expired" | "revoked" | "tenant_mismatch" | "policy_denied";
export type Denied = { name: string; reason: string };

export type RunResolution = {
  subjectType: "user" | "service";
  tenant: string;
  role: Role;
  teams: string[];
  scopes: string[];
  expiry: string;
  status: RunStatus;
  grantedTools: string[];
  deniedTools: Denied[];
  grantedKbs: string[];
  deniedKbs: Denied[];
};

// @backend POST /v1/agents/:id/invoke (auth) - at real invoke time the server validates the caller's
//   Trellix IAM JWT (signature, tenant, role, teams, scopes, expiry, revocation) and computes the
//   effective access = agent config ∩ caller grants ∩ tenant ∩ resource policy. This client decode is
//   a Playground preview only; keep the RunResolution shape, replace decodeToken with a /resolve-run
//   (or token-introspection) call. [ref: backend-integration-plan.md#runtime-request]
/** Deterministic mock decode of a pasted token. Real IAM validates a JWT and reads claims. */
function decodeToken(token: string, agent: Agent): Omit<RunResolution, "grantedTools" | "deniedTools" | "grantedKbs" | "deniedKbs"> {
  const t = token.toLowerCase();
  const subjectType: "user" | "service" = t.includes("user") ? "user" : "service";
  const tenant = t.includes("grc") ? "trellix-grc" : t.includes("labs") ? "trellix-labs" : (agent.workspaceId ?? DEFAULT_WORKSPACE_ID);
  const role: Role = t.includes("admin") ? "admin" : t.includes("pub") ? "publisher" : t.includes("view") ? "viewer" : "builder";
  const teams = t.includes("soc") ? ["SOC"] : t.includes("grc") ? ["GRC"] : t.includes("plat") ? ["Platform"] : [];
  const status: RunStatus = t.includes("expired") ? "expired" : t.includes("revoked") ? "revoked"
    : tenant !== (agent.workspaceId ?? DEFAULT_WORKSPACE_ID) ? "tenant_mismatch" : "ok";
  const expiry = t.includes("expired") ? "expired 2d ago" : "in 29 days";
  // scope breadth: "full" grants all; otherwise a deterministic partial subset
  const full = t.includes("full");
  const scopes = full ? ["*"] : ["partial"];
  return { subjectType, tenant, role, teams, scopes, expiry, status };
}

export function resolveRun(ctx: RunCtx, agent: Agent, session: Session): RunResolution {
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
        tenant: ctx.tenant, role: ctx.role, teams: ctx.teams,
        scopes: ["*"], expiry: ctx.identityMode === "service" ? "service account" : "minted · 15m",
        status: (ctx.tenant === agentTenant ? "ok" : "tenant_mismatch") as RunStatus,
      }
    : decodeToken(ctx.token, agent);

  // Run-time callers (token/channel) carry no `subject`, so a `private` agent (owner-only) is denied
  // here for everyone except an admin token — the intended fail-closed behavior (access-policy.ts).
  const principal: Principal = { role: base.role, teams: base.teams };
  const tools = agent.tools ?? [];
  const kbs = agent.knowledgeBases ?? [];

  // whole-run failures short-circuit resource grants
  let status = base.status;
  if (status === "ok" && agent.accessPolicy && !canUse(agent.accessPolicy, principal)) {
    status = "policy_denied";
  }
  const blocked = status !== "ok";

  const full = base.scopes.includes("*");
  const grantedTools: string[] = [];
  const deniedTools: Denied[] = [];
  const grantedKbs: string[] = [];
  const deniedKbs: Denied[] = [];

  if (!blocked) {
    tools.forEach((name, i) => {
      // session or "full" token grants all; partial token grants every other tool
      const inScope = full || i % 2 === 0;
      if (inScope) grantedTools.push(name);
      else deniedTools.push({ name, reason: "not in token scope" });
    });
    kbs.forEach((id, i) => {
      const inScope = full || i % 2 === 0;
      if (inScope) grantedKbs.push(id);
      else deniedKbs.push({ name: id, reason: "not in token scope" });
    });
  } else {
    // Whole run is blocked: nothing executes, but surface what WOULD be used so the access
    // preview shows the agent's bound resources (struck through) instead of "No tools bound".
    const reason = statusLabel[status];
    tools.forEach((name) => deniedTools.push({ name, reason }));
    kbs.forEach((id) => deniedKbs.push({ name: id, reason }));
  }

  return { ...base, status, grantedTools, deniedTools, grantedKbs, deniedKbs };
}

export const statusLabel: Record<RunStatus, string> = {
  ok: "Access granted",
  expired: "Token expired",
  revoked: "Token revoked",
  tenant_mismatch: "Token belongs to another tenant",
  policy_denied: "Denied by the agent's access policy",
};
