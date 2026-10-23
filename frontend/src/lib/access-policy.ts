import type { Role } from "./permissions";

// Per-resource access policy. ONE shape shared by the FE and the backend (backend/09 §5, backend/01
// `access_policy` jsonb). Three visibility tiers, modelled on Google Chat / MS Teams / Splunk sharing:
//   workspace  — every space member may use/manage (default)        [Teams standard / Splunk "App"]
//   restricted — gated by role AND team, separately for use/manage  [Teams private channel]
//   private    — only the owner (+ space admins) may use/manage     [Splunk "Private"]
export type AccessPolicy = {
  visibility: "workspace" | "restricted" | "private";
  useRoles: Role[];
  useTeams: string[];
  manageRoles: Role[];
  manageTeams: string[];
  // Set only when visibility === "private": the resource owner's subject (email/OIDC subject).
  owner?: string;
};

// A caller's role at invoke is the execute-only `executer` baseline (token/channel planes, backend/09 §1),
// NOT a console RBAC tier; the console session keeps its console `Role`. One Principal type spans both.
export type CallerRole = Role | "executer";

// `subject` is the caller's identity (email/OIDC subject). Present on the console (from the session);
// absent at invoke time (token/channel callers have no owner identity) — so `private` resources are
// denied to every runtime caller (fail closed). `admin` is a console claim, never a caller role.
export type Principal = { role: CallerRole; teams: string[]; subject?: string };

export function defaultPolicy(): AccessPolicy {
  return { visibility: "workspace", useRoles: [], useTeams: [], manageRoles: [], manageTeams: [] };
}

function matches(roles: Role[], teams: string[], p: Principal): boolean {
  // A "restricted" policy that names neither a role nor a team grants no one — fail closed
  // rather than silently open (otherwise "Restricted" would be a no-op visible to everyone).
  if (roles.length === 0 && teams.length === 0) return false;
  // Caller plane (executer): `useRoles` gate CONSOLE principals only; a caller has no console role, so it is
  // gated on TEAMS alone — a role-only policy (no team) therefore denies callers (fail closed). Keeps the FE
  // preview aligned with backend/09 §5 and the channel model "channel okta_group ∩ agent accessPolicy".
  if (p.role === "executer") return teams.length > 0 && p.teams.some((t) => teams.includes(t));
  // Console principal: role AND team (a single empty list = unconstrained on that dimension).
  const roleOk = roles.length === 0 || roles.includes(p.role);
  const teamOk = teams.length === 0 || p.teams.some((t) => teams.includes(t));
  return roleOk && teamOk;
}

// A private resource is reachable only by its owner (by subject) — plus the admin bypass below.
function isOwner(policy: AccessPolicy, p: Principal): boolean {
  return !!p.subject && !!policy.owner && p.subject === policy.owner;
}

/** Can this principal USE (invoke / bind) the resource? Workspace admins can use anything in
    their workspace (RBAC: admin → all, B2 role model). */
export function canUse(policy: AccessPolicy | undefined, p: Principal): boolean {
  if (!policy || policy.visibility === "workspace") return true;
  if (p.role === "admin") return true;
  if (policy.visibility === "private") return isOwner(policy, p);
  return matches(policy.useRoles, policy.useTeams, p);
}

/** Can this principal MANAGE (edit / publish) the resource? Workspace admins can manage anything
    (RBAC: admin → all, B2 role model) — notably so they can always fix a restricted/private policy. */
export function canManage(policy: AccessPolicy | undefined, p: Principal): boolean {
  if (!policy || policy.visibility === "workspace") return true;
  if (p.role === "admin") return true;
  if (policy.visibility === "private") return isOwner(policy, p);
  return matches(policy.manageRoles, policy.manageTeams, p);
}

export function policySummary(policy: AccessPolicy | undefined): string {
  if (!policy || policy.visibility === "workspace") return "All workspace members";
  if (policy.visibility === "private") return "Only the owner";
  const bits: string[] = [];
  if (policy.useRoles.length) bits.push(policy.useRoles.join(", "));
  if (policy.useTeams.length) bits.push(policy.useTeams.join(", "));
  return bits.length ? `Restricted to ${bits.join(" / ")}` : "Restricted";
}
