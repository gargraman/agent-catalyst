"use client";

/* Identity + permission surface. Store-backed (no React context): the active workspace, dev role override,
   and dev platform-admin override live in the store, so useAuth()/useWorkspace() re-derive whenever any
   changes. A real Auth.js + Trellix IAM OIDC session will expose the same shape later; only `profile` and
   the platform-admin source change. */

import {
  MOCK_PROFILE, memberWorkspaces, roleFor, isPlatformAdmin,
  type Workspace,
} from "./iam";
import { ROLE_PERMISSIONS, hasPermission, type Permission, type Role } from "./permissions";
import {
  setActiveWorkspace, setRoleOverride, setTeamOverride,
  useActiveWorkspaceId, useRoleOverride, useTeamOverride,
  useWorkspaces, usePlatformAdminOverride, useWorkspaceActiveFor,
} from "./store";

// @backend session - replace MOCK_PROFILE with the signed-in OIDC session (Auth.js + org Okta): name/email
//   from the session; `groups` = the OIDC `groups` claim (workspace backing groups + PLATFORM_ADMIN_GROUP);
//   `teams` = the functional Okta groups. Role per workspace is DERIVED from the workspace's group → memberRole
//   (backend/09 §2.1, backend/15). The useAuth/useWorkspace/usePrincipal surface stays identical; only this
//   source changes, and the dev role/team/platform-admin overrides are dropped. [ref: frontend-auth-access-plan.md]
const profile = MOCK_PROFILE;

export type AuthUser = { name: string; email: string; role: Role; teams: string[] };

export type Auth = {
  user: AuthUser;
  /** Effective role = dev override (preview) if set, else derived from the active workspace's group. */
  role: Role;
  /** True when the dev role switcher is forcing a role different from IAM. */
  overridden: boolean;
  /** True when the user is in the deployment-wide platform-admin group (admin everywhere + workspace CRUD). */
  isPlatformAdmin: boolean;
  /** True when the active workspace is Inactive — every write permission is denied (read-only freeze). */
  frozen: boolean;
  permissions: Permission[];
  can: (p: Permission) => boolean;
  setRole: (role: Role | null) => void;
};

export function useAuth(): Auth {
  const activeWs = useActiveWorkspaceId();
  const override = useRoleOverride();
  const teamOverride = useTeamOverride();
  const allWorkspaces = useWorkspaces();
  // Platform-admin: dev preview toggle wins, else the OIDC `groups` claim.
  const platformAdmin = usePlatformAdminOverride() ?? isPlatformAdmin(profile.groups);
  const active = useWorkspaceActiveFor(activeWs);
  // Resolve the active workspace from the VISIBLE set (membership-enforced): a non-member active id yields
  // no workspace → viewer, and the boundary shows the picker rather than leaking a non-member tenant.
  const workspace = memberWorkspaces(profile, allWorkspaces, platformAdmin).find((w) => w.id === activeWs);
  const role: Role = override ?? roleFor(profile, workspace, platformAdmin);
  const teams = teamOverride ?? profile.teams;
  // Inactive workspace = workspace-level read-only freeze: every write/invoke permission is denied, only
  // read ("view") survives. The single choke point for GateButton/Gate/page self-guards. Workspace CRUD
  // controls deliberately bypass this by checking `isPlatformAdmin`, not can().
  const frozen = !active;
  return {
    user: { name: profile.name, email: profile.email, role, teams },
    role,
    overridden: override != null,
    isPlatformAdmin: platformAdmin,
    frozen,
    permissions: frozen ? (["view"] as Permission[]) : (ROLE_PERMISSIONS[role] ?? []),
    can: (p) => (frozen ? p === "view" : hasPermission(role, p)),
    setRole: setRoleOverride,
  };
}

export type WorkspaceCtx = {
  /** Workspaces visible to the user: platform-admins see all, else those whose Okta group they're in. */
  workspaces: Workspace[];
  workspace: Workspace | undefined;
  /** Role conferred by the active workspace's group (ignores the dev preview override). */
  membershipRole: Role;
  /** False when the active workspace is Inactive (read-only freeze). */
  active: boolean;
  /** True when the user is a platform admin (workspace CRUD gate). */
  isPlatformAdmin: boolean;
  /** Effective role used for gating (honors the dev preview override). */
  role: Role;
  teams: string[];
  setWorkspace: (id: string) => void;
  setTeams: (teams: string[] | null) => void;
  /** Visible in ≥1 workspace — i.e. not locked out. */
  hasAccess: boolean;
};

export function useWorkspace(): WorkspaceCtx {
  const activeWs = useActiveWorkspaceId();
  const { role, user, isPlatformAdmin: platformAdmin } = useAuth();
  const allWorkspaces = useWorkspaces();
  const workspaces = memberWorkspaces(profile, allWorkspaces, platformAdmin);
  // Membership-enforced: resolve from the visible set, not the registry, so a non-member active id (e.g. after
  // dropping platform-admin while in a non-member space) renders the picker, not that tenant's console.
  const workspace = workspaces.find((w) => w.id === activeWs);
  const active = useWorkspaceActiveFor(activeWs);
  return {
    workspaces,
    workspace,
    membershipRole: roleFor(profile, workspace, platformAdmin),
    active,
    isPlatformAdmin: platformAdmin,
    role,
    teams: user.teams,
    setWorkspace: setActiveWorkspace,
    setTeams: setTeamOverride,
    hasAccess: workspaces.length > 0,
  };
}

/** Principal for per-resource access-policy checks (effective role + Okta teams + subject for owner
    checks on `private` resources). */
export function usePrincipal(): { role: Role; teams: string[]; subject: string } {
  const { role, user } = useAuth();
  return { role, teams: user.teams, subject: user.email };
}
