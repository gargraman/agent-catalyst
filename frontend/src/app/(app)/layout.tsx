import * as React from "react";
import { AppBoundary } from "@/components/app-boundary";
import { Toaster } from "@/components/toaster";

/* @backend integration points (this page):
 *  - @backend mounts the (app) surface; the session/no-access gate (OIDC) is enforced here / in app-boundary (src/lib/auth.tsx)
 * See src/lib/store.ts markers + docs/backend-integration-map.md for the authoritative contracts.
 */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <AppBoundary>{children}</AppBoundary>
      <Toaster />
    </>
  );
}
