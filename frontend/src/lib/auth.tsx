"use client";

/* Identity + permission surface. Store-backed (no React context): the active
   workspace + dev role override live in the store, so useAuth()/useWorkspace()
   re-derive whenever either changes. A real Auth.js + Trellix IAM OIDC session
   will expose the same shape later; only the source of `profile` changes. */

import { MOCK_PROFILE, WORKSPACES, workspacesForProfile, type Workspace } from "./iam";
import { ROLE_PERMISSIONS, hasPermission, type Permission, type Role } from "./permissions";
import {
  setActiveWorkspace, setRoleOverride, setTeamOverride,
  useActiveWorkspaceId, useRoleOverride, useTeamOverride,
} from "./store";

// @backend session - replace MOCK_PROFILE with the signed-in OIDC session (Auth.js + Trellix IAM):
//   name/email + role-per-workspace memberships + teams come from token claims. The useAuth /
//   useWorkspace / usePrincipal surface stays identical; only this source changes, and the dev
//   role/team overrides are dropped. [ref: frontend-auth-access-plan.md#part-e]
const profile = MOCK_PROFILE;

/** Role for a workspace from the IAM profile; viewer if not a member. */
function membershipRole(workspaceId: string): Role {
  return profile.memberships[workspaceId] ?? "viewer";
}

export type AuthUser = { name: string; email: string; role: Role; teams: string[] };

export type Auth = {
  user: AuthUser;
  /** Effective role = dev override (preview) if set, else the active workspace membership. */
  role: Role;
  /** True when the dev role switcher is forcing a role different from IAM. */
  overridden: boolean;
  permissions: Permission[];
  can: (p: Permission) => boolean;
  setRole: (role: Role | null) => void;
};

export function useAuth(): Auth {
  const activeWs = useActiveWorkspaceId();
  const override = useRoleOverride();
  const teamOverride = useTeamOverride();
  const role: Role = override ?? membershipRole(activeWs);
  const teams = teamOverride ?? profile.teams;
  return {
    user: { name: profile.name, email: profile.email, role, teams },
    role,
    overridden: override != null,
    permissions: ROLE_PERMISSIONS[role] ?? [],
    can: (p) => hasPermission(role, p),
    setRole: setRoleOverride,
  };
}

export type WorkspaceCtx = {
  workspaces: Workspace[];
  workspace: Workspace | undefined;
  /** IAM membership role for the active workspace (not the preview override). */
  membershipRole: Role;
  /** Effective role used for gating (honors the dev preview override). */
  role: Role;
  teams: string[];
  setWorkspace: (id: string) => void;
  setTeams: (teams: string[] | null) => void;
  hasAccess: boolean;
};

export function useWorkspace(): WorkspaceCtx {
  const activeWs = useActiveWorkspaceId();
  const { role, user } = useAuth();
  const workspaces = workspacesForProfile(profile);
  const workspace = WORKSPACES.find((w) => w.id === activeWs);
  return {
    workspaces,
    workspace,
    membershipRole: membershipRole(activeWs),
    role,
    teams: user.teams,
    setWorkspace: setActiveWorkspace,
    setTeams: setTeamOverride,
    hasAccess: workspaces.length > 0,
  };
}

/** Principal for per-resource access-policy checks (effective role + IAM teams). */
export function usePrincipal(): { role: Role; teams: string[] } {
  const { role, user } = useAuth();
  return { role, teams: user.teams };
}
