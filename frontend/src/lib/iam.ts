import type { Role } from "./permissions";
import { ORG } from "./org";

/* Mock Trellix IAM profile. Real IAM replaces this with the OIDC session + `groups` claim. */

// @backend identity is CLAIM-BASED + IdP-AGNOSTIC [ref: backend/09 §2.1, backend/18; scope.md 2026-10-22 rev 1/2]:
//   - A user holds PER-WORKSPACE role claims in their `groups`: one claim per (workspace, role) they hold,
//     role ∈ viewer|builder|publisher. The same user can be publisher in one space and builder in another.
//     There is NO single per-workspace base role, NO app-managed roster, NO invites, NO self-join.
//   - The `ap-<prefix>-<role>` claim NAME is the OKTA ADAPTER'S convention (idpBinding below). Other IdPs
//     (Entra GUIDs, SAML attrs, custom) map role→native-ref via app-owned `idp_binding` config — the authz
//     core only ever consumes the normalized claim set, never a vendor naming scheme (backend/18 §1-§3).
//   - PLATFORM_ADMIN_GROUP: a deployment-wide claim whose holders are admin in EVERY workspace and are the
//     only principals allowed to create/delete/(de)activate/edit workspaces (backend/15 app-wide admin claim; the
//     app-wide admin binding is deployment-level config, not per-workspace — backend/18 §3).
//   - TEAMS + MOCK_PROFILE.teams: functional groups (OIDC `groups` claim) driving the per-resource access
//     policy + channel end-user gate. SEPARATE namespace from the workspace role claims — do not conflate.

/** The three per-workspace console roles a claim can confer. `admin` is app-wide only (PLATFORM_ADMIN_GROUP). */
export type ConsoleRole = "viewer" | "builder" | "publisher";

export type Workspace = {
  id: string;
  name: string;
  short: string;
  // Per-workspace prefix the role claims are built from (idpBinding). The `ap-<prefix>-<role>` expansion is
  // the Okta adapter's sugar; other adapters store explicit native refs instead (backend/18 §3).
  groupPrefix: string;
};

/** Okta ADAPTER convention: a workspace's role claims are `ap-<prefix>-<role>`. This is the one place the
    naming scheme lives — swap this helper (or back it with per-workspace `idp_binding` config) to support
    Entra group GUIDs, SAML attrs, or a custom IdP, per backend/18 §3. */
export const idpBinding = (ws: Workspace): Record<ConsoleRole, string> => ({
  viewer: `ap-${ws.groupPrefix}-viewer`,
  builder: `ap-${ws.groupPrefix}-builder`,
  publisher: `ap-${ws.groupPrefix}-publisher`,
});

export const WORKSPACES: Workspace[] = [
  { id: "trellix-xdr", name: "Trellix XDR", short: "TX", groupPrefix: "xdr" },
  { id: "trellix-grc", name: "Trellix GRC", short: "GR", groupPrefix: "grc" },
  { id: "trellix-labs", name: "Trellix Labs", short: "TL", groupPrefix: "labs" },
  { id: "trellix-people", name: "Trellix People Ops", short: "PO", groupPrefix: "people" },
  // A workspace the demo user holds NO role claim for — only visible because platform-admins see all.
  { id: "trellix-sandbox", name: "Trellix Sandbox", short: "SB", groupPrefix: "sandbox" },
];

// Default/fallback tenant for the mock world. Derived from the seed (NOT org branding) so it can never
// drift from WORKSPACES or the per-workspace metric maps in data.ts. Real workspaces come from IAM.
// @backend remove - the active workspace comes from the session; this is a demo-seed default only.
export const DEFAULT_WORKSPACE_ID = WORKSPACES[0].id;

// Deployment-wide platform-admin claim: admin in every workspace + the only principal allowed to manage
// workspaces (create/delete/(de)activate/edit). The app-wide admin binding is deployment-level config, not
// per-workspace. [ref: backend/15 app-wide admin claim; backend/18 §3 admin_group_ref]
export const PLATFORM_ADMIN_GROUP = "ap-platform-admin";

// Teams are functional groups (sourced from the OIDC `groups` claim, backend/09 §2.1). These names mirror the
// IdP group display names; a real session normalizes the claim to these slugs. Distinct from the per-workspace
// role claims (idpBinding) — do not conflate functional teams with workspace role membership.
export const TEAMS = ["SOC", "Detection", "Platform", "GRC", "Research", "People"] as const;
export type Team = (typeof TEAMS)[number];

export type IamProfile = {
  name: string;
  email: string;
  teams: Team[]; // functional groups (OIDC `groups` claim) — access policy + channel gate
  groups: string[]; // per-workspace role claims (idpBinding) + maybe PLATFORM_ADMIN_GROUP
};

export const MOCK_PROFILE: IamProfile = {
  name: "Raman Garg",
  email: `raman.garg@${ORG.emailDomain}`,
  teams: ["SOC", "Platform", "People"],
  // Demo user is a platform admin (so create/delete is demoable) + holds per-workspace role claims:
  // publisher in XDR, viewer in GRC, builder in Labs/People. NO claim for sandbox — that space is only
  // visible because platform-admins see all. Drop the admin claim via the dev "Platform admin" preview
  // toggle to see the member experience.
  groups: ["ap-xdr-publisher", "ap-grc-viewer", "ap-labs-builder", "ap-people-builder", PLATFORM_ADMIN_GROUP],
};

/** True when the user holds the deployment-wide platform-admin claim. */
export const isPlatformAdmin = (groups: string[]): boolean => groups.includes(PLATFORM_ADMIN_GROUP);

/** Console roles, ascending — lets memberWorkspaces test membership and roleFor pick the highest held claim. */
const WS_ROLE_RANK: ConsoleRole[] = ["viewer", "builder", "publisher"];

/** Workspaces the user can see: platform-admins see all; otherwise those they hold ANY role claim for.
    @backend the workspace list is store-backed (mutable) so callers pass the live list. */
export const memberWorkspaces = (
  p: IamProfile,
  workspaces: Workspace[] = WORKSPACES,
  platformAdmin: boolean = isPlatformAdmin(p.groups),
): Workspace[] =>
  platformAdmin
    ? workspaces
    : workspaces.filter((w) => {
        const b = idpBinding(w);                                  // build once per workspace (mirrors roleFor)
        return WS_ROLE_RANK.some((r) => p.groups.includes(b[r]));
      });

/** Effective role in a workspace: admin for platform-admins; else the HIGHEST role claim held for it; else
    viewer (the read-only auditor fallback, so the boundary/picker behavior is unchanged for non-members). */
export const roleFor = (
  p: IamProfile,
  workspace: Workspace | undefined,
  platformAdmin: boolean = isPlatformAdmin(p.groups),
): Role => {
  if (platformAdmin) return "admin";
  if (workspace) {
    let held: ConsoleRole | undefined;
    const binding = idpBinding(workspace);
    for (const r of WS_ROLE_RANK) if (p.groups.includes(binding[r])) held = r; // keep the last (highest) match
    if (held) return held;
  }
  return "viewer";
};

/** A roster member's resolved role for a workspace, from the claims THEY hold: admin for platform-admins;
    else the highest per-workspace role claim; else null (not a member of this workspace → omit from the
    roster). Mirrors roleFor but for an arbitrary member's claim set, so the Members tab projects per-workspace.
    @backend the server returns this already resolved per-workspace (GET /members?workspace, 18 §4). */
export const memberRoleFor = (groups: string[], workspace: Workspace | undefined): Role | null => {
  if (isPlatformAdmin(groups)) return "admin";
  if (!workspace) return null;
  const binding = idpBinding(workspace);
  let held: ConsoleRole | undefined;
  for (const r of WS_ROLE_RANK) if (groups.includes(binding[r])) held = r;  // keep the highest match
  return held ?? null;
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
