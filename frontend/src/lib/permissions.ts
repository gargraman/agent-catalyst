export type Role = "viewer" | "builder" | "publisher" | "admin";

export type Permission =
  | "view"
  | "playground.use"
  | "agent.create"
  | "agent.edit"
  | "eval.run"
  | "eval.author"
  | "eval.govern"
  | "knowledge.manage"
  | "tool.manage"
  | "mcp.manage"
  | "channel.manage"
  | "agent.publish"
  | "tool.approve"
  | "agent.delete"
  | "key.manage"
  | "member.manage"
  | "access.manage"
  | "settings.manage";

const BUILDER: Permission[] = [
  "view", "playground.use", "agent.create", "agent.edit", "eval.run", "eval.author",
  "knowledge.manage", "tool.manage", "mcp.manage", "channel.manage",
];
// `tool.approve` — decide pending high-risk/destructive tool-call approvals (runtime gate). Publisher+
// per backend/09 §4. The approval inbox UI is a follow-up; the permission lands now for FE/BE alignment.
const PUBLISHER: Permission[] = [...BUILDER, "agent.publish", "tool.approve"];
const ADMIN: Permission[] = [...PUBLISHER, "agent.delete", "key.manage", "member.manage", "access.manage", "settings.manage", "eval.govern"];

export const ROLE_PERMISSIONS: Record<Role, Permission[]> = {
  viewer: ["view", "playground.use"],
  builder: BUILDER,
  publisher: PUBLISHER,
  admin: ADMIN,
};

export const ROLE_LABEL: Record<Role, string> = {
  viewer: "Viewer", builder: "Builder", publisher: "Publisher", admin: "Admin",
};
export const ROLE_ORDER: Role[] = ["viewer", "builder", "publisher", "admin"];

export function hasPermission(role: Role, p: Permission): boolean {
  return ROLE_PERMISSIONS[role]?.includes(p) ?? false;
}
