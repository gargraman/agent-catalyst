import type { Role } from "./permissions";

/* Mock Trellix IAM profile. Real IAM replaces this with the OIDC session + claims;
   the shapes (memberships per workspace, teams) stay the same. */

// @backend identity has TWO sources — do NOT conflate them [ref: frontend-auth-access-plan.md, backend/09 §2.1]:
//   - WORKSPACES + MOCK_PROFILE.memberships (the workspaces a user belongs to + their role-per-workspace):
//     APP-MANAGED, read from the Trellix IAM directory for this registered product. Okta-group membership
//     does NOT grant workspace access — app entry stays "member of >=1 workspace".
//   - TEAMS + MOCK_PROFILE.teams: these ARE Okta groups, carried in the OIDC `groups` claim and normalized
//     to team slugs at login (NOT a directory read, NOT stored). They drive the per-resource access policy
//     and the channel end-user gate. Replace these constants with those two sources; keep the types.

export type Workspace = { id: string; name: string; short: string };

export const WORKSPACES: Workspace[] = [
  { id: "trellix-xdr", name: "Trellix XDR", short: "TX" },
  { id: "trellix-grc", name: "Trellix GRC", short: "GR" },
  { id: "trellix-labs", name: "Trellix Labs", short: "TL" },
  { id: "trellix-people", name: "Trellix People Ops", short: "PO" },
];

// Teams ARE Okta groups (sourced from the OIDC `groups` claim, backend/09 §2.1). These names mirror the
// Okta group display names; a real session normalizes the claim to these slugs.
export const TEAMS = ["SOC", "Detection", "Platform", "GRC", "Research", "People"] as const;
export type Team = (typeof TEAMS)[number];

export type IamProfile = {
  name: string;
  email: string;
  teams: Team[]; // Okta groups (from the OIDC `groups` claim), not workspace memberships
  memberships: Record<string, Role>; // workspaceId -> role (app-managed)
};

export const MOCK_PROFILE: IamProfile = {
  name: "Raman Garg",
  email: "raman.garg@trellix.com",
  teams: ["SOC", "Platform", "People"],
  memberships: {
    "trellix-xdr": "admin",
    "trellix-grc": "viewer",
    "trellix-labs": "builder",
    "trellix-people": "admin",
  },
};

export const workspacesForProfile = (p: IamProfile): Workspace[] =>
  WORKSPACES.filter((w) => p.memberships[w.id]);
