"use client";

/* Identity + permission surface. Store-backed (no React context): the active
   workspace + dev role override live in the store, so useAuth()/useWorkspace()
   re-derive whenever either changes. A real Auth.js + Trellix IAM OIDC session
   will expose the same shape later; only the source of `profile` changes. */

import {
  MOCK_PROFILE, WORKSPACES, memberWorkspaces, joinableWorkspaces, DEFAULT_JOIN_ROLE,
  type Workspace, type IamProfile,
} from "./iam";
import { ROLE_PERMISSIONS, hasPermission, type Permission, type Role } from "./permissions";
import {
  setActiveWorkspace, setRoleOverride, setTeamOverride,
  useActiveWorkspaceId, useRoleOverride, useTeamOverride, useJoinedWorkspaceIds,
} from "./store";

// @backend session - replace MOCK_PROFILE with the signed-in OIDC session (Auth.js + org Okta):
//   name/email come from the OIDC session; role-per-workspace memberships are app-managed (invite /
//   join-open roster, backend/09 §2-§3), NOT an IAM directory read; teams are Okta groups from the OIDC
//   `groups` claim (backend/09 §2.1). The useAuth / useWorkspace / usePrincipal surface stays identical;
//   only this source changes, and the dev role/team overrides are dropped. [ref: frontend-auth-access-plan.md#part-e]
const profile = MOCK_PROFILE;

/** Profile with any open spaces the user joined this session folded in at the default join role.
    @backend remove the merge once membership is sourced from the session roster. */
function effectiveProfile(joined: string[]): IamProfile {
  if (joined.length === 0) return profile;
  const memberships = { ...profile.memberships };
  for (const id of joined) memberships[id] ??= DEFAULT_JOIN_ROLE;
  return { ...profile, memberships };
}

/** Role for a workspace from the profile; viewer if not a member. */
function roleFor(p: IamProfile, workspaceId: string): Role {
  return p.memberships[workspaceId] ?? "viewer";
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
  const ep = effectiveProfile(useJoinedWorkspaceIds());
  const role: Role = override ?? roleFor(ep, activeWs);
  const teams = teamOverride ?? ep.teams;
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
  /** Spaces the user is a member of (the switcher's primary list). */
  workspaces: Workspace[];
  /** Open spaces the user is not yet a member of — discoverable + joinable. */
  joinable: Workspace[];
  workspace: Workspace | undefined;
  /** App-managed membership role for the active workspace (not the preview override). */
  membershipRole: Role;
  /** Effective role used for gating (honors the dev preview override). */
  role: Role;
  teams: string[];
  setWorkspace: (id: string) => void;
  setTeams: (teams: string[] | null) => void;
  /** Member of ≥1 space OR at least one open space to join — i.e. not locked out. */
  hasAccess: boolean;
};

export function useWorkspace(): WorkspaceCtx {
  const activeWs = useActiveWorkspaceId();
  const { role, user } = useAuth();
  const ep = effectiveProfile(useJoinedWorkspaceIds());
  const workspaces = memberWorkspaces(ep);
  const joinable = joinableWorkspaces(ep);
  const workspace = WORKSPACES.find((w) => w.id === activeWs);
  return {
    workspaces,
    joinable,
    workspace,
    membershipRole: roleFor(ep, activeWs),
    role,
    teams: user.teams,
    setWorkspace: setActiveWorkspace,
    setTeams: setTeamOverride,
    hasAccess: workspaces.length > 0 || joinable.length > 0,
  };
}

/** Principal for per-resource access-policy checks (effective role + Okta teams + subject for owner
    checks on `private` resources). */
export function usePrincipal(): { role: Role; teams: string[]; subject: string } {
  const { role, user } = useAuth();
  return { role, teams: user.teams, subject: user.email };
}
