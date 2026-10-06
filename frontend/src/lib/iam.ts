import type { Role } from "./permissions";

/* Mock Trellix IAM profile. Real IAM replaces this with OIDC token claims;
   the shapes (memberships per workspace, teams) stay the same. */

// @backend GET (IAM directory) - WORKSPACES, TEAMS and MOCK_PROFILE.memberships are read from
//   Trellix IAM for this registered product: the workspaces the user belongs to, their team/group
//   memberships, and their role per workspace. Replace these constants with that directory read;
//   keep the types. [ref: frontend-auth-access-plan.md#part-a0]

export type Workspace = { id: string; name: string; short: string };

export const WORKSPACES: Workspace[] = [
  { id: "trellix-xdr", name: "Trellix XDR", short: "TX" },
  { id: "trellix-grc", name: "Trellix GRC", short: "GR" },
  { id: "trellix-labs", name: "Trellix Labs", short: "TL" },
  { id: "trellix-people", name: "Trellix People Ops", short: "PO" },
];

export const TEAMS = ["SOC", "Detection", "Platform", "GRC", "Research", "People"] as const;
export type Team = (typeof TEAMS)[number];

export type IamProfile = {
  name: string;
  email: string;
  teams: Team[];
  memberships: Record<string, Role>; // workspaceId -> role
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
