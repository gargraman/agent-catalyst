"use client";

/* @backend integration points (this page):
 *  - @backend wired(026) POST /api/v1/agents/:id/invoke (SSE) - the chat run for live agents (streamInvoke via
 *    store.invokeAgentStream; mock agents keep the sample send — see src/components/playground.tsx)
 *  - @backend GET /v1/agents?workspace=:id - agent picker list [via useAgents]
 *  - @backend wired(026) version picker reads the live AgentOut versions embed [via versionsOf] (seeds in mock mode);
 *    for live agents it also offers "Draft" and maps the pick to invoke `target` (draft | active | version + version_id)
 *  - @backend runtime token decode previews server-side JWT validation (src/lib/runtime-access.ts)
 * See src/lib/store.ts markers + docs/backend-integration-map.md for the authoritative contracts.
 */

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Bot, MessageSquare } from "lucide-react";
import { Button, EmptyState, Field, PageHeader, Select } from "@/components/ui";
import { Playground, type RunTarget } from "@/components/playground";
import { isLiveAgent, useAgents, versionsOf } from "@/lib/store";
import { useAuth, usePrincipal } from "@/lib/auth";
import { canUse } from "@/lib/access-policy";

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
  const versions = selected ? versionsOf(selected) : [];
  const versionLabels = versions.map((v) => v.label);
  const live = !!selected && isLiveAgent(selected.id);
  // Live agents can also run the unsaved-as-version DRAFT (026 D3). "draft" is reserved as a picker key.
  const DRAFT = "draft";
  const pinned = versions.find((v) => v.isActive)?.label ?? selected?.activeVersion;
  // Only ever surface a version that is actually an option — guards an agent whose
  // activeVersion isn't in its version list, or a stale ?version= in the URL.
  const activeVersion =
    live && requestedVersion === DRAFT ? DRAFT
    : requestedVersion && versionLabels.includes(requestedVersion) ? requestedVersion
    : versionLabels.includes(pinned ?? "") ? pinned
    : versionLabels[0];
  // What a live turn runs: the draft, the pinned (active) version, or one specific version by id — never by label.
  const picked = versions.find((v) => v.label === activeVersion);
  const run: RunTarget | undefined = !live ? undefined
    : activeVersion === DRAFT ? { target: "draft", label: "Draft" }
    : picked && picked.label !== pinned && picked.id ? { target: "version", versionId: picked.id, label: picked.label }
    : { target: "active", label: `${pinned ?? activeVersion ?? "active"} (active)` };
  // A deep link to an agent that can't be tested here (archived or no access).
  const requestedMissing = !!requested && !requestedUsable;

  function pickAgent(id: string) {
    router.replace(`/playground?agent=${id}`);
  }
  function pickVersion(label: string) {
    router.replace(`/playground?agent=${selId}&version=${encodeURIComponent(label)}`);
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
            {(versions.length > 0 || live) && (
              <Field label={live ? "Run" : "Version"}>
                <Select value={activeVersion} onChange={(e) => pickVersion(e.target.value)} className="min-w-24">
                  {live && <option value={DRAFT}>Draft (unsaved changes)</option>}
                  {versions.map((v) => (
                    <option key={v.label} value={v.label}>{live && v.label === pinned ? `${v.label} (active)` : v.label}</option>
                  ))}
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
        ? <Playground key={`${selected.id}:${activeVersion}`} agent={selected} version={activeVersion} run={run} />
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
