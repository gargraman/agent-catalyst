import type { Role } from "./permissions";
import { ORG } from "./org";

/* Mock Trellix IAM profile. Real IAM replaces this with the OIDC session + `groups` claim. */

// @backend identity is Okta-GROUP-DRIVEN [ref: backend/09 §2.1, backend/15; reverses scope.md 2026-10-15]:
//   - Each Workspace binds ONE authoritative Okta group (Workspace.oktaGroup). Being in that group = access
//     to the workspace; the group confers a single base role (Workspace.memberRole). There is NO app-managed
//     roster, NO invites, NO self-join, NO open/restricted.
//   - PLATFORM_ADMIN_GROUP: a deployment-wide Okta group whose members are admin in EVERY workspace and are
//     the only principals allowed to create/delete/(de)activate/edit workspaces (backend/15 platform_admins).
//   - TEAMS + MOCK_PROFILE.teams: functional Okta groups (OIDC `groups` claim) driving the per-resource
//     access policy + channel end-user gate. SEPARATE namespace from workspace groups — do not conflate.

export type Workspace = {
  id: string;
  name: string;
  short: string;
  // Authoritative backing Okta group: membership in it grants access to this workspace.
  oktaGroup: string;
  // Role the group confers to every member. `admin` is reserved for platform-admins — never a memberRole.
  memberRole: Role;
};

export const WORKSPACES: Workspace[] = [
  { id: "trellix-xdr", name: "Trellix XDR", short: "TX", oktaGroup: "ap-xdr", memberRole: "publisher" },
  { id: "trellix-grc", name: "Trellix GRC", short: "GR", oktaGroup: "ap-grc", memberRole: "viewer" },
  { id: "trellix-labs", name: "Trellix Labs", short: "TL", oktaGroup: "ap-labs", memberRole: "builder" },
  { id: "trellix-people", name: "Trellix People Ops", short: "PO", oktaGroup: "ap-people", memberRole: "builder" },
  // A workspace whose group the demo user is NOT in — only visible because platform-admins see all.
  { id: "trellix-sandbox", name: "Trellix Sandbox", short: "SB", oktaGroup: "ap-sandbox", memberRole: "builder" },
];

// Default/fallback tenant for the mock world. Derived from the seed (NOT org branding) so it can never
// drift from WORKSPACES or the per-workspace metric maps in data.ts. Real workspaces come from IAM.
// @backend remove - the active workspace comes from the session; this is a demo-seed default only.
export const DEFAULT_WORKSPACE_ID = WORKSPACES[0].id;

// Deployment-wide platform-admin Okta group: admin in every workspace + the only principal allowed to
// manage workspaces (create/delete/(de)activate/edit). [ref: backend/15 platform_admins; §auto-upsert-from-group]
export const PLATFORM_ADMIN_GROUP = "ap-platform-admin";

// Teams ARE Okta groups (sourced from the OIDC `groups` claim, backend/09 §2.1). These names mirror the
// Okta group display names; a real session normalizes the claim to these slugs. Distinct from the workspace
// backing group (Workspace.oktaGroup) — do not conflate functional teams with space membership.
export const TEAMS = ["SOC", "Detection", "Platform", "GRC", "Research", "People"] as const;
export type Team = (typeof TEAMS)[number];

export type IamProfile = {
  name: string;
  email: string;
  teams: Team[]; // functional Okta groups (OIDC `groups` claim) — access policy + channel gate
  groups: string[]; // workspace backing Okta groups (+ maybe PLATFORM_ADMIN_GROUP) the user belongs to
};

export const MOCK_PROFILE: IamProfile = {
  name: "Raman Garg",
  email: `raman.garg@${ORG.emailDomain}`,
  teams: ["SOC", "Platform", "People"],
  // Demo user is a platform admin (so create/delete is demoable) + in four workspace groups. NOT in
  // ap-sandbox — that space is only visible because platform-admins see all. Drop the admin group via the
  // dev "Platform admin" preview toggle to see the member experience.
  groups: ["ap-xdr", "ap-grc", "ap-labs", "ap-people", PLATFORM_ADMIN_GROUP],
};

/** True when the user is in the deployment-wide platform-admin group. */
export const isPlatformAdmin = (groups: string[]): boolean => groups.includes(PLATFORM_ADMIN_GROUP);

/** Workspaces the user can see: platform-admins see all; otherwise those whose Okta group they're in.
    @backend the workspace list is store-backed (mutable) so callers pass the live list. */
export const memberWorkspaces = (
  p: IamProfile,
  workspaces: Workspace[] = WORKSPACES,
  platformAdmin: boolean = isPlatformAdmin(p.groups),
): Workspace[] => (platformAdmin ? workspaces : workspaces.filter((w) => p.groups.includes(w.oktaGroup)));

/** Effective role in a workspace: admin for platform-admins; else the group's memberRole if in it; else viewer. */
export const roleFor = (
  p: IamProfile,
  workspace: Workspace | undefined,
  platformAdmin: boolean = isPlatformAdmin(p.groups),
): Role => {
  if (platformAdmin) return "admin";
  if (workspace && p.groups.includes(workspace.oktaGroup)) return workspace.memberRole;
  return "viewer";
};

/** Derive a URL-safe slug/id from a workspace name (kebab-case).
    @backend the server assigns the immutable slug at creation (workspaces.slug, backend/01 §4.1); this
    mirrors it client-side for the mock create flow. */
export const slugify = (name: string): string =>
  name.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

/** Two-letter avatar initials for a workspace name (Workspace.short). */
export const shortOf = (name: string): string => {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "–";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
};
