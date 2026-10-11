"use client";

/* Gates the whole (app) surface on tenancy (Part A0). With the mock IAM profile
   the user always has access, so this renders AppShell; it exists so real IAM
   returning no memberships / no active workspace lands on the right screen. */

import * as React from "react";
import { AppShell } from "./app-shell";
import { NoProductAccess, WorkspacePicker } from "./no-access";
import { useWorkspace } from "@/lib/auth";

/* @backend integration points (this page):
 *  - @backend no-access + workspace-picker gate, driven by the OIDC session + IAM memberships [via useWorkspace] (src/lib/auth.tsx, src/lib/iam.ts)
 * See src/lib/store.ts markers + docs/backend-integration-map.md for the authoritative contracts.
 */
export function AppBoundary({ children }: { children: React.ReactNode }) {
  const { hasAccess, workspace } = useWorkspace();
  if (!hasAccess) return <NoProductAccess />;
  if (!workspace) return <WorkspacePicker />;
  return <AppShell>{children}</AppShell>;
}
