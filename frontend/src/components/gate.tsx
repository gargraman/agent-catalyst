"use client";

/* Permission gating primitives (Part B). Use <Gate> to hide a whole control a
   role shouldn't see, and <GateButton> when the control should stay visible but
   disabled with a "why" tooltip (clearer than a silently missing button). */

import * as React from "react";
import { useAuth } from "@/lib/auth";
import { ROLE_LABEL, type Permission, type Role } from "@/lib/permissions";
import { Button } from "@/components/ui";

/** Lowest role that grants a permission, for the tooltip copy. */
const REQUIRED_ROLE: Partial<Record<Permission, Role>> = {
  "agent.create": "builder", "agent.edit": "builder", "eval.run": "builder",
  "knowledge.manage": "builder", "tool.manage": "builder", "mcp.manage": "builder", "channel.manage": "builder",
  "agent.publish": "publisher",
  "agent.delete": "admin", "key.manage": "admin", "member.manage": "admin", "access.manage": "admin",
};

export function requirementText(permission: Permission): string {
  const role = REQUIRED_ROLE[permission];
  return role ? `Requires ${ROLE_LABEL[role]} role` : "Not permitted for your role";
}

/** Render children only if the current role has `permission`; else `fallback` (default nothing). */
export function Gate({
  permission,
  children,
  fallback = null,
}: {
  permission: Permission;
  children: React.ReactNode;
  fallback?: React.ReactNode;
}) {
  const { can } = useAuth();
  return <>{can(permission) ? children : fallback}</>;
}

type GateButtonProps = React.ComponentProps<typeof Button> & { permission: Permission };

/** A Button that disables itself (with a reason title) when the role lacks `permission`.
   When blocked, any `href` is dropped so it renders a real disabled <button>, not a live link. */
export function GateButton({ permission, disabled, title, href, ...rest }: GateButtonProps) {
  const { can } = useAuth();
  const allowed = can(permission);
  return (
    <Button
      {...rest}
      href={allowed ? href : undefined}
      disabled={disabled || !allowed}
      title={allowed ? title : requirementText(permission)}
    />
  );
}
