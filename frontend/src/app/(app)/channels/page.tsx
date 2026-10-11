"use client";

import Link from "next/link";
import { MessagesSquare, Plus, Bot, Radio, ShieldCheck, Users } from "lucide-react";
import {
  Badge, StatusDot, PageHeader, EmptyState,
} from "@/components/ui";
import { StatTile } from "@/components/stat-tile";
import { GateButton } from "@/components/gate";
import { useChannels } from "@/lib/store";
import { usePrincipal } from "@/lib/auth";
import { canUse } from "@/lib/access-policy";
import { CHANNEL_DEFS, type ChannelPlatform } from "@/lib/channels";
import type { Channel } from "@/lib/data";
import { relative } from "@/lib/format";

const statusTone = (s: Channel["status"]) =>
  s === "connected" ? "good" : s === "error" ? "critical" : s === "setup_pending" ? "warning" : "neutral";
const statusLabel = (s: Channel["status"]) =>
  s === "connected" ? "Connected" : s === "error" ? "Error" : s === "setup_pending" ? "Setup pending" : "Disconnected";
const platformLabel = (p: ChannelPlatform) => CHANNEL_DEFS[p].label;

/* @backend integration points (this page):
 *  - @backend GET /v1/channels?workspace=:id - list connected messaging channels [via useChannels]
 * See src/lib/store.ts markers + docs/channels-design.md / backend/12-channels.md for contracts.
 */
export default function ChannelsPage() {
  const principal = usePrincipal();
  const channels = useChannels().filter((c) => canUse(c.accessPolicy, principal));

  const connected = channels.filter((c) => c.status === "connected").length;
  const agentsReached = new Set(channels.flatMap((c) => c.agentIds)).size;
  const attention = channels.filter((c) => c.status === "error" || c.status === "setup_pending").length;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Channels"
        description="Let people talk to your agents from Slack, Teams and Google Chat. Users sign in with their own Okta identity; access follows the channel's Okta group and each agent's policy."
      >
        <GateButton permission="channel.manage" variant="primary" href="/channels/new"><Plus className="size-4" /> Connect channel</GateButton>
      </PageHeader>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatTile label="Channels" value={String(channels.length)} icon={<MessagesSquare className="size-4" />} />
        <StatTile label="Connected" value={String(connected)} icon={<Radio className="size-4" />} />
        <StatTile label="Agents reachable" value={String(agentsReached)} icon={<Bot className="size-4" />} />
        <StatTile label="Needs attention" value={String(attention)} hint="error or setup pending" icon={<ShieldCheck className="size-4" />} />
      </div>

      {channels.length === 0 ? (
        <EmptyState
          icon={<MessagesSquare className="size-5" />}
          title="No channels connected"
          hint="Connect a Slack, Teams or Google Chat app to let people chat with your agents."
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {channels.map((c) => (
            <Link key={c.id} href={`/channels/${c.id}`} className="card-elev lift block rounded-xl border border-border bg-surface p-5">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h3 className="truncate font-semibold text-ink">{c.name}</h3>
                  <p className="mt-0.5 text-xs text-muted">{platformLabel(c.platform)}</p>
                </div>
                <Badge tone={statusTone(c.status)}><StatusDot tone={statusTone(c.status)} />{statusLabel(c.status)}</Badge>
              </div>

              <div className="mt-4 grid grid-cols-2 gap-3 border-y border-border py-3 text-xs">
                <div><div className="text-muted">Agents</div><div className="tnum font-medium text-ink-2">{c.agentIds.length}</div></div>
                <div><div className="text-muted">Routing</div><div className="font-medium capitalize text-ink-2">{c.routing}</div></div>
              </div>

              <div className="mt-3 flex items-center gap-2 text-xs text-ink-2">
                <Users className="size-3.5 shrink-0 text-muted" />
                <span className="min-w-0 flex-1 truncate">{c.identityMode === "service" ? "Service identity" : "Federated (Okta)"}{c.oktaGroup ? ` · ${c.oktaGroup}` : ""}</span>
              </div>

              <div className="mt-4 flex items-center justify-between text-xs text-muted">
                <span>by {c.installedBy}</span>
                <span>{c.lastEventAt ? `active ${relative(c.lastEventAt)}` : "no traffic yet"}</span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
