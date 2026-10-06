"use client";

/* @backend integration points (this page):
 *  - @backend POST /v1/agents/:id/invoke streaming - the chat run (send() is mock; see src/components/playground.tsx)
 *  - @backend GET /v1/agents?workspace=:id - agent picker list [via useAgents]
 *  - @backend GET /v1/agents/:id/versions - version picker [via getVersions, local today]
 *  - @backend runtime token decode previews server-side JWT validation (src/lib/runtime-access.ts)
 * See src/lib/store.ts markers + docs/backend-integration-map.md for the authoritative contracts.
 */

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Bot, MessageSquare } from "lucide-react";
import { Button, EmptyState, Field, PageHeader, Select } from "@/components/ui";
import { Playground } from "@/components/playground";
import { useAgents } from "@/lib/store";
import { useAuth, usePrincipal } from "@/lib/auth";
import { canUse } from "@/lib/access-policy";
import { getVersions } from "@/lib/data";

function PlaygroundInner() {
  const router = useRouter();
  const params = useSearchParams();
  const { can } = useAuth();
  const principal = usePrincipal();

  // Testable agents: in the active workspace, usable by this principal, not archived.
  const agents = useAgents().filter((a) => a.status !== "archived" && canUse(a.accessPolicy, principal));

  const requested = params.get("agent");
  const requestedVersion = params.get("version") ?? undefined;
  const requestedUsable = !!requested && agents.some((a) => a.id === requested);
  // The URL is the single source of truth, so browser back/forward stays in sync
  // (deriving instead of seeding state once at mount).
  const selId = requestedUsable ? requested! : agents[0]?.id ?? "";
  const selected = agents.find((a) => a.id === selId) ?? agents[0];
  const versions = selected ? getVersions(selected.id) : [];
  const versionLabels = versions.map((v) => v.label);
  // Only ever surface a version that is actually an option — guards an agent whose
  // activeVersion isn't in its version list, or a stale ?version= in the URL.
  const activeVersion =
    requestedVersion && versionLabels.includes(requestedVersion) ? requestedVersion
    : versionLabels.includes(selected?.activeVersion ?? "") ? selected?.activeVersion
    : versionLabels[0];
  // A deep link to an agent that can't be tested here (archived or no access).
  const requestedMissing = !!requested && !requestedUsable;

  function pickAgent(id: string) {
    router.replace(`/playground?agent=${id}`);
  }
  function pickVersion(label: string) {
    router.replace(`/playground?agent=${selId}&version=${label}`);
  }

  if (!can("playground.use")) {
    return <EmptyState icon={<MessageSquare className="size-6" />} title="Playground not available" hint="Your role does not include playground access in this workspace." />;
  }

  return (
    <div className="space-y-6">
      <PageHeader title="Playground" description="Test any agent interactively before you publish. Resource access follows the running user's token.">
        {selected && (
          <div className="flex items-end gap-2">
            <Field label="Agent">
              <Select value={selected.id} onChange={(e) => pickAgent(e.target.value)} className="min-w-52">
                {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </Select>
            </Field>
            {versions.length > 0 && (
              <Field label="Version">
                <Select value={activeVersion} onChange={(e) => pickVersion(e.target.value)} className="min-w-24">
                  {versions.map((v) => <option key={v.label} value={v.label}>{v.label}</option>)}
                </Select>
              </Field>
            )}
          </div>
        )}
      </PageHeader>

      {requestedMissing && (
        <div className="rounded-lg border border-border bg-elevated px-3 py-2.5 text-sm text-ink-2">
          Agent <span className="font-mono text-xs">{requested}</span> isn&apos;t available to test here &mdash; it may be archived or restricted in this workspace.
          {selected && <> Showing <span className="font-medium text-ink">{selected.name}</span> instead.</>}
        </div>
      )}

      {selected
        ? <Playground key={`${selected.id}:${activeVersion}`} agent={selected} version={activeVersion} />
        : (
          <EmptyState
            icon={<Bot className="size-6" />}
            title="No agents to test yet"
            hint="Create an agent, then come back here to try it in the playground."
          />
        )}
      {!selected && (
        <div className="flex justify-center">
          <Button variant="primary" href="/agents/new">New agent</Button>
        </div>
      )}
    </div>
  );
}

export default function PlaygroundPage() {
  // useSearchParams requires a Suspense boundary for the statically-generated route.
  return (
    <React.Suspense fallback={<PageHeader title="Playground" description="Test any agent interactively before you publish." />}>
      <PlaygroundInner />
    </React.Suspense>
  );
}
