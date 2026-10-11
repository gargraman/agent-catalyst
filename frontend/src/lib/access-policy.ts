import type { Role } from "./permissions";

export type AccessPolicy = {
  visibility: "workspace" | "restricted";
  useRoles: Role[];
  useTeams: string[];
  manageRoles: Role[];
  manageTeams: string[];
};

export type Principal = { role: Role; teams: string[] };

export function defaultPolicy(): AccessPolicy {
  return { visibility: "workspace", useRoles: [], useTeams: [], manageRoles: [], manageTeams: [] };
}

function matches(roles: Role[], teams: string[], p: Principal): boolean {
  // A "restricted" policy that names neither a role nor a team grants no one — fail closed
  // rather than silently open (otherwise "Restricted" would be a no-op visible to everyone).
  if (roles.length === 0 && teams.length === 0) return false;
  // A single empty list is not a constraint on its own (restrict by the other dimension).
  const roleOk = roles.length === 0 || roles.includes(p.role);
  const teamOk = teams.length === 0 || p.teams.some((t) => teams.includes(t));
  return roleOk && teamOk;
}

/** Can this principal USE (invoke / bind) the resource? Workspace admins can use anything in
    their workspace (RBAC: admin → all, B2 role model). */
export function canUse(policy: AccessPolicy | undefined, p: Principal): boolean {
  if (!policy || policy.visibility === "workspace") return true;
  if (p.role === "admin") return true;
  return matches(policy.useRoles, policy.useTeams, p);
}

/** Can this principal MANAGE (edit / publish) the resource? Workspace admins can manage anything
    (RBAC: admin → all, B2 role model) — notably so they can always fix a restricted policy. */
export function canManage(policy: AccessPolicy | undefined, p: Principal): boolean {
  if (!policy || policy.visibility === "workspace") return true;
  if (p.role === "admin") return true;
  return matches(policy.manageRoles, policy.manageTeams, p);
}

export function policySummary(policy: AccessPolicy | undefined): string {
  if (!policy || policy.visibility === "workspace") return "All workspace members";
  const bits: string[] = [];
  if (policy.useRoles.length) bits.push(policy.useRoles.join(", "));
  if (policy.useTeams.length) bits.push(policy.useTeams.join(", "));
  return bits.length ? `Restricted to ${bits.join(" / ")}` : "Restricted";
}
