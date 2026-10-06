"use client";

/* Edge-state screens (Part A0): a user with no product access, or signed in but
   with no active workspace selected. Rendered by the (app) layout gate. */

import { ShieldAlert, Building2 } from "lucide-react";
import { Card, CardContent, Button } from "@/components/ui";
import { useWorkspace } from "@/lib/auth";

export function NoProductAccess() {
  return (
    <div className="grid min-h-[100dvh] place-items-center px-6">
      <Card className="max-w-md text-center">
        <CardContent className="flex flex-col items-center gap-4 py-10">
          <div className="grid size-12 place-items-center rounded-full bg-elevated text-ink-2">
            <ShieldAlert className="size-6" aria-hidden />
          </div>
          <div className="space-y-1.5">
            <h1 className="text-lg font-semibold text-ink">No access to Agent Catalyst</h1>
            <p className="text-sm leading-relaxed text-ink-2">
              Your Trellix IAM profile has no role for this product in any workspace. Access is
              managed in Trellix IAM. Ask a workspace admin to grant you a role.
            </p>
          </div>
          <Button variant="outline" size="sm">Request access in Trellix IAM</Button>
        </CardContent>
      </Card>
    </div>
  );
}

export function WorkspacePicker() {
  const { workspaces, setWorkspace } = useWorkspace();
  return (
    <div className="grid min-h-[100dvh] place-items-center px-6">
      <Card className="w-full max-w-md">
        <CardContent className="space-y-4 py-8">
          <div className="space-y-1.5 text-center">
            <h1 className="text-lg font-semibold text-ink">Choose a workspace</h1>
            <p className="text-sm text-ink-2">You have access to multiple workspaces. Pick one to continue.</p>
          </div>
          <ul className="space-y-2">
            {workspaces.map((w) => (
              <li key={w.id}>
                <button
                  onClick={() => setWorkspace(w.id)}
                  className="flex w-full items-center gap-3 rounded-lg border border-border bg-elevated px-3 py-2.5 text-left transition-colors hover:bg-overlay"
                >
                  <span className="grid size-8 shrink-0 place-items-center rounded-md bg-accent-soft text-xs font-semibold text-accent">
                    {w.short}
                  </span>
                  <span className="flex items-center gap-2 text-sm font-medium text-ink">
                    <Building2 className="size-3.5 text-ink-2" aria-hidden />
                    {w.name}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}
