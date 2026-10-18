import type { Role } from "./permissions";
import { ORG } from "./org";

/* Mock Trellix IAM profile. Real IAM replaces this with the OIDC session + claims;
   the shapes (memberships per workspace, teams) stay the same. */

// @backend identity has TWO sources — do NOT conflate them [ref: frontend-auth-access-plan.md, backend/09 §2.1]:
//   - WORKSPACES + MOCK_PROFILE.memberships (the workspaces a user belongs to + their role-per-workspace):
//     APP-MANAGED (invite / join-open), on top of org-level Okta authentication. A user authenticates
//     against the org Okta tenant; membership in a space is then an app roster entry, created by a space
//     admin's invite or by the user joining an `access: "open"` space. An optional per-space backing Okta
//     group (`Workspace.oktaGroup`) MAY auto-grant membership, but the app roster is the system of record.
//   - TEAMS + MOCK_PROFILE.teams: these ARE Okta groups, carried in the OIDC `groups` claim and normalized
//     to team slugs at login (NOT a directory read, NOT stored). They drive the per-resource access policy
//     and the channel end-user gate. Replace these constants with those two sources; keep the types.

export type Workspace = {
  id: string;
  name: string;
  short: string;
  // How a user gains membership (Google Chat / Teams / Splunk space model):
  //   open       — any authenticated org member can discover + join (default role: DEFAULT_JOIN_ROLE)
  //   restricted — invite-only; non-members cannot see it in the switcher
  access: "open" | "restricted";
  // Optional per-space backing Okta group that auto-grants membership (nullable; app roster stays SoR).
  oktaGroup?: string;
};

export const WORKSPACES: Workspace[] = [
  { id: "trellix-xdr", name: "Trellix XDR", short: "TX", access: "restricted", oktaGroup: "ap-xdr" },
  { id: "trellix-grc", name: "Trellix GRC", short: "GR", access: "restricted" },
  { id: "trellix-labs", name: "Trellix Labs", short: "TL", access: "open", oktaGroup: "ap-labs" },
  { id: "trellix-people", name: "Trellix People Ops", short: "PO", access: "restricted" },
  // An OPEN space the mock user is NOT a member of — exercises the discover/join flow.
  { id: "trellix-sandbox", name: "Trellix Sandbox", short: "SB", access: "open" },
];

// Default/fallback tenant for the mock world. Derived from the seed (NOT org branding) so it can never
// drift from WORKSPACES or the per-workspace metric maps in data.ts. Real workspaces come from IAM.
// @backend remove - the active workspace comes from the session; this is a demo-seed default only.
export const DEFAULT_WORKSPACE_ID = WORKSPACES[0].id;

// Role granted when a user self-joins an `access: "open"` space (least privilege).
// @backend the join endpoint (POST /v1/workspaces/:id/join, backend/08) assigns this default role.
export const DEFAULT_JOIN_ROLE: Role = "viewer";

// Teams ARE Okta groups (sourced from the OIDC `groups` claim, backend/09 §2.1). These names mirror the
// Okta group display names; a real session normalizes the claim to these slugs. Distinct from the optional
// per-space backing group (Workspace.oktaGroup) — do not conflate functional teams with space membership.
export const TEAMS = ["SOC", "Detection", "Platform", "GRC", "Research", "People"] as const;
export type Team = (typeof TEAMS)[number];

export type IamProfile = {
  name: string;
  email: string;
  teams: Team[]; // Okta groups (from the OIDC `groups` claim), not workspace memberships
  memberships: Record<string, Role>; // workspaceId -> role (app-managed roster)
};

export const MOCK_PROFILE: IamProfile = {
  name: "Raman Garg",
  email: `raman.garg@${ORG.emailDomain}`,
  teams: ["SOC", "Platform", "People"],
  memberships: {
    "trellix-xdr": "admin",
    "trellix-grc": "viewer",
    "trellix-labs": "builder",
    "trellix-people": "admin",
    // NOTE: no entry for "trellix-sandbox" — it is an open space the user can join.
  },
};

/** Spaces the user already belongs to (shown in the switcher, scoped by role). */
export const memberWorkspaces = (p: IamProfile): Workspace[] =>
  WORKSPACES.filter((w) => p.memberships[w.id]);

/** Open spaces the user is NOT yet a member of — discoverable + joinable. */
export const joinableWorkspaces = (p: IamProfile): Workspace[] =>
  WORKSPACES.filter((w) => !p.memberships[w.id] && w.access === "open");
