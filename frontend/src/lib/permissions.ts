export type Role = "viewer" | "builder" | "publisher" | "admin";

export type Permission =
  | "view"
  | "playground.use"
  | "agent.create"
  | "agent.edit"
  | "eval.run"
  | "knowledge.manage"
  | "tool.manage"
  | "mcp.manage"
  | "channel.manage"
  | "agent.publish"
  | "agent.delete"
  | "key.manage"
  | "member.manage"
  | "access.manage";

const BUILDER: Permission[] = [
  "view", "playground.use", "agent.create", "agent.edit", "eval.run",
  "knowledge.manage", "tool.manage", "mcp.manage", "channel.manage",
];
const PUBLISHER: Permission[] = [...BUILDER, "agent.publish"];
const ADMIN: Permission[] = [...PUBLISHER, "agent.delete", "key.manage", "member.manage", "access.manage"];

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
