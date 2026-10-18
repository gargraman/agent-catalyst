"use client";

import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  ArrowLeft, Bot, Check, Lock, MessagesSquare, Pause, Play, Send, Trash2, Users,
} from "lucide-react";
import {
  Badge, Button, Card, CardContent, CardHeader, CardTitle, CardDescription,
  EmptyState, StatusDot, Table, TD, TH, THead, TR, CodeBlock,
} from "@/components/ui";
import { StatTile } from "@/components/stat-tile";
import { ConfirmDialog } from "@/components/dialog";
import { AccessPolicyEditor } from "@/components/access-policy-editor";
import { useAuth, usePrincipal, useWorkspace } from "@/lib/auth";
import { DEFAULT_WORKSPACE_ID } from "@/lib/iam";
import { canManage } from "@/lib/access-policy";
import { CHANNEL_DEFS } from "@/lib/channels";
import { resolveRun, statusLabel, type RunCtx, type Session } from "@/lib/runtime-access";
import { addRun, deleteChannel, genId, nowIso, toast, updateChannel, useAgents, useChannel } from "@/lib/store";
import type { Channel } from "@/lib/data";
import { dateShort, relative } from "@/lib/format";

const statusTone = (s: Channel["status"]) =>
  s === "connected" ? "good" : s === "error" ? "critical" : s === "setup_pending" ? "warning" : "neutral";
const statusText = (s: Channel["status"]) =>
  s === "connected" ? "Connected" : s === "error" ? "Error" : s === "setup_pending" ? "Setup pending" : "Disconnected";

/* @backend integration points (this page):
 *  - @backend GET /v1/channels/:id - channel detail (routing, bound agents, masked creds, status)
 *  - @backend PATCH /v1/channels/:id - edit routing / bound agents / rotate creds [via updateChannel]
 *  - @backend DELETE /v1/channels/:id - disconnect + revoke vaulted creds [via deleteChannel]
 *  - @backend PUT /v1/channels/:id/access-policy - manage access policy [via setAccessPolicy]
 *  - @backend runs are emitted server-side by runtime.invoke(); the "send test" here uses addRun (mock)
 * See docs/channels-design.md / backend/12-channels.md for the authoritative contracts.
 */
export default function ChannelDetailPage() {
  const params = useParams();
  const id = String(params.id);
  const channel = useChannel(id);
  const agents = useAgents();
  const { user } = useAuth();
  const principal = usePrincipal();
  const { workspace } = useWorkspace();
  const [confirmOpen, setConfirmOpen] = React.useState(false);

  if (!channel) {
    return (
      <div className="space-y-6">
        <Button variant="ghost" size="sm" href="/channels" className="-ml-2"><ArrowLeft className="size-4" /> Channels</Button>
        <EmptyState icon={<MessagesSquare className="size-5" />} title="Channel not found" hint="It may have been disconnected." />
      </div>
    );
  }

  const def = CHANNEL_DEFS[channel.platform];
  const boundAgents = channel.agentIds.map((aid) => agents.find((a) => a.id === aid)).filter(Boolean) as typeof agents;
  const target = agents.find((a) => a.id === (channel.defaultAgentId ?? channel.agentIds[0]));
  const manage = canManage(channel.accessPolicy, principal);
  const tenant = channel.workspaceId ?? workspace?.id ?? DEFAULT_WORKSPACE_ID;

  // Access preview for the default agent, using the channel RunCtx arm — the federated Okta user
  // resolves to a real principal, then is gated by the agent's own access policy.
  const ctx: RunCtx = {
    mode: "channel", platform: channel.platform, identityMode: channel.identityMode,
    user: user.name, tenant, role: principal.role, teams: principal.teams,
  };
  const session: Session = { tenant, role: principal.role, teams: principal.teams };
  const resolution = target ? resolveRun(ctx, target, session) : null;

  function sendTest() {
    if (!target || !channel) return;
    addRun({
      id: genId("run"), agentId: target.id, agent: target.name, version: target.activeVersion,
      source: "Channel", status: "success", latencyMs: 1480,
      tokens: 4200, cost: 0.021,
      time: nowIso(), guardrail: "passed",
      caller: `${channel.platform} · @${user.name.split(" ")[0].toLowerCase()}`,
      subjectType: channel.identityMode === "service" ? "service" : "user",
      workspaceId: channel.workspaceId,
    });
    toast("Test message sent — see it on Runs");
  }

  return (
    <div className="space-y-6">
      <div>
        <Button variant="ghost" size="sm" href="/channels" className="-ml-2 mb-3"><ArrowLeft className="size-4" /> Channels</Button>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-xl font-semibold text-ink">{channel.name}</h1>
              <Badge tone={statusTone(channel.status)}><StatusDot tone={statusTone(channel.status)} />{statusText(channel.status)}</Badge>
            </div>
            <p className="mt-1 text-sm text-muted">{def.label} · {channel.identityMode === "service" ? "Service identity" : "Federated (Okta SSO)"}</p>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="secondary" size="sm" onClick={sendTest} disabled={!target}><Send className="size-4" /> Send test message</Button>
            {manage && (channel.status === "connected" || channel.status === "disconnected") && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => updateChannel(channel.id, { status: channel.status === "connected" ? "disconnected" : "connected" })}
              >
                {channel.status === "connected" ? <><Pause className="size-4" /> Pause</> : <><Play className="size-4" /> Resume</>}
              </Button>
            )}
            {manage && (
              <Button variant="outline" size="sm" onClick={() => setConfirmOpen(true)}><Trash2 className="size-4" /> Disconnect</Button>
            )}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatTile label="Agents reachable" value={String(channel.agentIds.length)} icon={<Bot className="size-4" />} />
        <StatTile label="Routing" value={channel.routing} icon={<Send className="size-4" />} />
        <StatTile label="Identity" value={channel.identityMode === "service" ? "Service" : "Federated"} icon={<Users className="size-4" />} />
        <StatTile label="Last event" value={channel.lastEventAt ? relative(channel.lastEventAt) : "—"} icon={<MessagesSquare className="size-4" />} />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          {/* Overview */}
          <Card>
            <CardHeader><CardTitle>Overview</CardTitle></CardHeader>
            <CardContent className="space-y-0 pt-0">
              <Row label="Platform" value={def.label} />
              <Row label="Workspace" value={tenant} />
              <Row label="Identity" value={channel.identityMode === "service" ? "Service account (Okta)" : "Federated end users (Okta SSO)"} />
              <Row label="Okta group" value={channel.oktaGroup ?? "—"} />
              <Row label="Inbound verification" value={def.verification.replace(/_/g, " ")} />
              <Row label="Installed by" value={`${channel.installedBy} · ${dateShort(channel.createdAt)}`} />
              {channel.webhookUrl && (
                <div className="pt-3">
                  <div className="mb-1 text-xs text-muted">Inbound webhook (register in {def.label})</div>
                  <CodeBlock code={channel.webhookUrl} />
                </div>
              )}
            </CardContent>
          </Card>

          {/* Routing + bound agents */}
          <Card>
            <CardHeader>
              <div><CardTitle>Routing</CardTitle><CardDescription>How a message selects which agent answers.</CardDescription></div>
              <Badge tone="neutral" className="capitalize">{channel.routing}</Badge>
            </CardHeader>
            <CardContent className="pt-0">
              {boundAgents.length === 0 ? (
                <p className="text-sm text-muted">No agents bound yet.</p>
              ) : (
                <ul className="space-y-2">
                  {boundAgents.map((a) => (
                    <li key={a.id} className="flex items-center justify-between gap-3 rounded-lg border border-border bg-elevated px-3 py-2">
                      <Link href={`/agents/${a.id}`} className="flex items-center gap-2 text-sm text-ink hover:underline">
                        <Bot className="size-4 text-muted" /> {a.name}
                      </Link>
                      <div className="flex items-center gap-2">
                        {channel.routing === "slash" && <code className="text-xs text-muted">/{a.id}</code>}
                        {channel.defaultAgentId === a.id && <Badge tone="accent">Default</Badge>}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          {/* Access preview via the channel RunCtx arm */}
          {resolution && target && (
            <Card>
              <CardHeader>
                <div><CardTitle>Access preview</CardTitle><CardDescription>What a chat user ({user.name}) can reach through {target.name}.</CardDescription></div>
                <Badge tone={resolution.status === "ok" ? "good" : "critical"}>{statusLabel[resolution.status]}</Badge>
              </CardHeader>
              <CardContent className="space-y-3 pt-0 text-sm">
                <p className="text-xs text-ink-2">
                  Subject <span className="text-ink">{resolution.subjectType}</span> · role <span className="text-ink">{resolution.role}</span>
                  {resolution.teams.length > 0 && <> · teams <span className="text-ink">{resolution.teams.join(", ")}</span></>}
                </p>
                <ResourceLine label="Tools" granted={resolution.grantedTools} denied={resolution.deniedTools.map((d) => d.name)} />
                <ResourceLine label="Knowledge" granted={resolution.grantedKbs} denied={resolution.deniedKbs.map((d) => d.name)} />
              </CardContent>
            </Card>
          )}
        </div>

        <div className="space-y-6">
          {/* Credentials */}
          <Card>
            <CardHeader><CardTitle>Credentials</CardTitle></CardHeader>
            <CardContent className="pt-0">
              <Table>
                <THead><tr><TH>Secret</TH><TH>Masked reference</TH></tr></THead>
                <tbody>
                  {def.credentialFields.map((f) => {
                    const ref = channel.credentials[f.key];
                    return (
                      <TR key={f.key}>
                        <TD className="text-ink-2">{f.label}</TD>
                        <TD className="font-mono text-xs text-muted">{ref?.masked || "— not set —"}</TD>
                      </TR>
                    );
                  })}
                </tbody>
              </Table>
              <p className="mt-3 flex items-center gap-1.5 text-xs text-muted">
                <Lock className="size-3.5" /> Raw secrets live in the vault; only masked references are kept.
              </p>
            </CardContent>
          </Card>

          {/* Access policy */}
          <Card>
            <CardHeader>
              <div><CardTitle>Access</CardTitle><CardDescription>Who may manage this channel. End users are gated by the Okta group ∩ each agent&apos;s policy.</CardDescription></div>
            </CardHeader>
            <CardContent className="pt-0">
              <AccessPolicyEditor kind="channel" id={channel.id} policy={channel.accessPolicy} readOnly={!manage} />
            </CardContent>
          </Card>
        </div>
      </div>

      <ConfirmDialog
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        onConfirm={() => { deleteChannel(channel.id); }}
        title="Disconnect channel"
        body={`Disconnect "${channel.name}"? Its vaulted credentials are revoked and inbound messages stop.`}
        confirmLabel="Disconnect"
      />
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-border py-2.5 last:border-0">
      <span className="text-sm text-muted">{label}</span>
      <span className="text-sm text-ink">{value}</span>
    </div>
  );
}

function ResourceLine({ label, granted, denied }: { label: string; granted: string[]; denied: string[] }) {
  return (
    <div>
      <div className="text-xs font-medium text-ink-2">{label}</div>
      <div className="mt-1 flex flex-wrap gap-1.5">
        {granted.length === 0 && denied.length === 0 && <span className="text-xs text-muted">None bound</span>}
        {granted.map((g) => (
          <span key={g} className="inline-flex items-center gap-1 rounded-md bg-good/12 px-2 py-0.5 text-[11px] text-good">
            <Check className="size-3" /> {g}
          </span>
        ))}
        {denied.map((d) => (
          <span key={d} className="inline-flex items-center gap-1 rounded-md bg-elevated px-2 py-0.5 text-[11px] text-muted line-through">
            <Lock className="size-3" /> {d}
          </span>
        ))}
      </div>
    </div>
  );
}
