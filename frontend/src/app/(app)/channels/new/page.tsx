"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, Check, ShieldCheck } from "lucide-react";
import { Button, Card, CardContent, Field, Input, PageHeader, Select } from "@/components/ui";
import { useAgents } from "@/lib/store";
import { addChannel, genId, nowIso } from "@/lib/store";
import { useAuth, usePrincipal, useWorkspace } from "@/lib/auth";
import { canUse } from "@/lib/access-policy";
import { cn } from "@/lib/utils";
import { CHANNEL_DEFS, CHANNELS, type ChannelPlatform } from "@/lib/channels";
import {
  finalizeChannelCredentials, channelConfigComplete, channelCredentialsComplete,
} from "@/lib/channel-credentials";
import type { Channel } from "@/lib/data";

/* @backend integration points (this page):
 *  - @backend POST /v1/channels - connect a channel; raw credentials POSTed once to the vault, server
 *    registers the inbound webhook and returns its ingress URL [via addChannel]
 * See src/lib/store.ts markers + docs/backend/12-channels.md for the authoritative contracts.
 */
export default function NewChannelPage() {
  const router = useRouter();
  const { user } = useAuth();
  const principal = usePrincipal();
  const { workspace } = useWorkspace();
  const agents = useAgents().filter((a) => canUse(a.accessPolicy, principal) && a.status !== "archived");

  const [name, setName] = React.useState("");
  const [platform, setPlatform] = React.useState<ChannelPlatform>("slack");
  const [config, setConfig] = React.useState<Record<string, string>>({});
  const [creds, setCreds] = React.useState<Record<string, string>>({});
  const [identityMode, setIdentityMode] = React.useState<Channel["identityMode"]>("federated");
  const [oktaGroup, setOktaGroup] = React.useState("");
  const [routing, setRouting] = React.useState<Channel["routing"]>("default");
  const [agentIds, setAgentIds] = React.useState<string[]>([]);
  const [defaultAgentId, setDefaultAgentId] = React.useState<string>("");

  const def = CHANNEL_DEFS[platform];
  // config/credential field values are per-field maps; keep them when the platform is unchanged
  const changePlatform = (p: ChannelPlatform) => { setPlatform(p); setConfig({}); setCreds({}); };
  const toggleAgent = (id: string) =>
    setAgentIds((xs) => (xs.includes(id) ? xs.filter((x) => x !== id) : [...xs, id]));

  const complete =
    name.trim().length > 0 &&
    channelConfigComplete(platform, config) &&
    channelCredentialsComplete(platform, creds);

  function connect() {
    const id = genId(platform);
    const bound = agentIds.filter((a) => agents.some((x) => x.id === a));
    const channel: Channel = {
      id, name: name.trim(), platform, status: "connected",
      agentIds: bound,
      defaultAgentId: bound.includes(defaultAgentId) ? defaultAgentId : bound[0],
      routing, identityMode,
      oktaGroup: oktaGroup.trim() || undefined,
      credentials: finalizeChannelCredentials(platform, creds),
      webhookUrl: `https://agent-platform.internal/channels/${platform}/events/${id}`,
      installedBy: user.name,
      createdAt: nowIso(), updatedAt: nowIso(),
      workspaceId: workspace?.id,
    };
    addChannel(channel);
    router.push(`/channels/${id}`);
  }

  return (
    <div className="space-y-6">
      <div>
        <Button variant="ghost" size="sm" href="/channels" className="-ml-2 mb-3">
          <ArrowLeft className="size-4" /> Channels
        </Button>
        <PageHeader title="Connect a channel" description="Install a Slack, Teams or Google Chat app and route it to agents in this workspace." />
      </div>

      <Card>
        <CardContent className="space-y-5">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Name">
              <Input placeholder="SOC Slack" value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label="Platform">
              <Select value={platform} onChange={(e) => changePlatform(e.target.value as ChannelPlatform)}>
                {CHANNELS.map((p) => <option key={p} value={p}>{CHANNEL_DEFS[p].label}</option>)}
              </Select>
            </Field>
          </div>

          {/* non-secret config */}
          {def.configFields.length > 0 && (
            <div className="grid gap-4 sm:grid-cols-2">
              {def.configFields.map((f) => (
                <Field key={f.key} label={f.required ? `${f.label} *` : f.label} hint={f.hint}>
                  <Input
                    type={f.type === "number" ? "number" : "text"}
                    placeholder={f.placeholder}
                    value={config[f.key] ?? ""}
                    onChange={(e) => setConfig((c) => ({ ...c, [f.key]: e.target.value }))}
                  />
                </Field>
              ))}
            </div>
          )}

          {/* credentials — MULTI-SECRET: every password field is vaulted separately */}
          <div className="space-y-3 rounded-lg border border-border bg-elevated p-3">
            <div className="text-xs font-medium text-ink-2">Credentials</div>
            <div className="grid gap-4 sm:grid-cols-2">
              {def.credentialFields.map((f) => (
                <Field key={f.key} label={f.required ? `${f.label} *` : f.label} hint={f.hint}>
                  <Input
                    type={f.type === "password" ? "password" : "text"}
                    placeholder={f.placeholder}
                    value={creds[f.key] ?? ""}
                    onChange={(e) => setCreds((c) => ({ ...c, [f.key]: e.target.value }))}
                  />
                </Field>
              ))}
            </div>
            {def.credentialHint && <p className="text-xs text-muted">{def.credentialHint}</p>}
            <p className="flex items-center gap-1.5 text-xs text-muted">
              <ShieldCheck className="size-3.5" /> Verified inbound via {def.verification.replace(/_/g, " ")}. Secrets are stored in the vault; only masked references are kept.
            </p>
          </div>

          {/* identity + routing */}
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Identity" hint="How chat users are identified">
              <Select value={identityMode} onChange={(e) => setIdentityMode(e.target.value as Channel["identityMode"])}>
                <option value="federated">Federated (Okta SSO)</option>
                <option value="service">Service identity</option>
              </Select>
            </Field>
            <Field label="Okta group" hint="Members of this group may use the channel">
              <Input placeholder="okta-soc" value={oktaGroup} onChange={(e) => setOktaGroup(e.target.value)} />
            </Field>
          </div>

          <Field label="Routing" hint="How a message picks which agent answers">
            <Select value={routing} onChange={(e) => setRouting(e.target.value as Channel["routing"])}>
              <option value="default">Default agent only</option>
              <option value="slash">Slash command per agent</option>
              <option value="picker">In-chat picker</option>
            </Select>
          </Field>

          {/* bind agents */}
          <div className="space-y-2">
            <p className="text-sm font-medium text-ink">Agents reachable through this channel</p>
            {agents.length === 0 ? (
              <p className="text-xs text-muted">No agents available in this workspace.</p>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {agents.map((a) => {
                  const on = agentIds.includes(a.id);
                  return (
                    <button
                      key={a.id}
                      type="button"
                      onClick={() => toggleAgent(a.id)}
                      className={cn(
                        "rounded-md border px-2 py-1 text-xs font-medium transition-colors",
                        on ? "border-transparent bg-accent-soft text-accent" : "border-border bg-elevated text-ink-2 hover:bg-overlay",
                      )}
                    >
                      {a.name}
                    </button>
                  );
                })}
              </div>
            )}
            {agentIds.length > 1 && (
              <Field label="Default agent" hint="Answers when no agent is explicitly selected">
                <Select value={defaultAgentId} onChange={(e) => setDefaultAgentId(e.target.value)}>
                  <option value="">First selected</option>
                  {agentIds.map((id) => {
                    const a = agents.find((x) => x.id === id);
                    return a ? <option key={id} value={id}>{a.name}</option> : null;
                  })}
                </Select>
              </Field>
            )}
          </div>
        </CardContent>
      </Card>

      <div className="flex items-center justify-end">
        <Button variant="primary" onClick={connect} disabled={!complete}>
          <Check className="size-4" /> Connect channel
        </Button>
      </div>
    </div>
  );
}
