"use client";

import * as React from "react";
import {
  Building2, KeyRound, ShieldCheck, SlidersHorizontal, Users, Check, Plus, Lock,
} from "lucide-react";
import {
  Avatar, Badge, Button, Card, CardContent, CardHeader, CardTitle, CardDescription,
  CodeBlock, Field, Input, PageHeader, Select, Separator, StatusDot,
  Table, TD, TH, THead, TR,
} from "@/components/ui";
import { Tabs } from "@/components/tabs";
import { Dialog, ConfirmDialog } from "@/components/dialog";
import { CopyButton } from "@/components/copy-button";
import { GateButton } from "@/components/gate";
import { Member } from "@/lib/data";
import {
  useMembers, useProviderKeys, useTokens,
  useAgents, useKnowledgeBases, useMcpServers, useTools,
  addProviderKey, addToken, revokeToken, toast, genId, nowIso,
} from "@/lib/store";
import { ROLE_ORDER, ROLE_LABEL, ROLE_PERMISSIONS, type Permission } from "@/lib/permissions";
import { maskSecret } from "@/lib/secrets";
import { policySummary } from "@/lib/access-policy";
import { dateShort, relative } from "@/lib/format";

const roleTone: Record<Member["role"], "violet" | "accent" | "neutral"> = {
  Admin: "violet", Publisher: "accent", Builder: "neutral", Viewer: "neutral",
};

const CURL = `curl -N https://catalyst.internal/v1/agents/soc-triage-copilot/invoke \\
  -H "Authorization: Bearer $AGENT_CATALYST_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"messages":[{"role":"user","content":"Triage alert 48213"}],"stream":true}'`;

function Workspace() {
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Workspace</CardTitle>
            <CardDescription>General settings for this workspace. Applies to every agent inside it.</CardDescription>
          </div>
          <Building2 className="size-4 text-muted" />
        </CardHeader>
        <CardContent className="grid gap-5 sm:grid-cols-2">
          <Field label="Workspace name"><Input defaultValue="Trellix XDR" /></Field>
          <Field label="Slug" hint="Used in published endpoint URLs."><Input defaultValue="trellix-xdr" /></Field>
          <Field label="Default provider">
            <Select defaultValue="Anthropic">
              <option>Anthropic</option><option>OpenAI</option><option>Google</option><option>Mistral</option>
            </Select>
          </Field>
          <Field label="Default model">
            <Select defaultValue="claude-opus-5-5">
              <option>claude-opus-5-5</option><option>claude-sonnet-5-5</option><option>gpt-5</option>
            </Select>
          </Field>
          <Field label="Deployment" hint="Portable containers, no cloud lock-in.">
            <Input defaultValue="Portable / self-hosted" readOnly />
          </Field>
          <div className="flex items-end">
            <Button variant="primary">Save changes</Button>
          </div>
        </CardContent>
      </Card>

      <Card className="border-critical/30">
        <CardHeader>
          <div>
            <CardTitle className="text-critical">Danger zone</CardTitle>
            <CardDescription>Archiving unpublishes every endpoint in this workspace. This cannot be undone quickly.</CardDescription>
          </div>
        </CardHeader>
        <CardContent>
          <Button variant="danger">Archive workspace</Button>
        </CardContent>
      </Card>
    </div>
  );
}

function Members() {
  const members = useMembers();

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Sign-in &amp; roles</CardTitle>
            <CardDescription>
              Console sign-in and roles are managed in Trellix IAM. Agent Catalyst is a registered product; your role is assigned per workspace in IAM.
            </CardDescription>
          </div>
          <ShieldCheck className="size-4 text-accent" />
        </CardHeader>
      </Card>

      <Card>
        <CardHeader>
          <div>
            <div className="flex items-center gap-2">
              <CardTitle>Members &amp; roles</CardTitle>
              <Badge tone="neutral">Managed in IAM</Badge>
            </div>
            <CardDescription>
              Members and workspace roles are sourced from Trellix IAM and shown here read-only. Viewer reads only. Builder creates and tests agents. Publisher can publish and manage keys. Admin manages the workspace.
            </CardDescription>
          </div>
          <GateButton permission="member.manage" variant="primary" size="sm" href="#">
            <Users className="size-4" /> Manage in Trellix IAM
          </GateButton>
        </CardHeader>
        <CardContent className="pt-0">
          <Table>
            <THead>
              <TR>
                <TH>Member</TH><TH>Email</TH><TH>Role</TH><TH>Last active</TH>
              </TR>
            </THead>
            <tbody>
              {members.map((m) => (
                <TR key={m.email}>
                  <TD>
                    <div className="flex items-center gap-2.5">
                      <Avatar name={m.name} />
                      <span className="font-medium text-ink">{m.name}</span>
                    </div>
                  </TD>
                  <TD className="text-muted">{m.email}</TD>
                  <TD><Badge tone={roleTone[m.role]}>{m.role}</Badge></TD>
                  <TD className="text-muted tnum">{relative(m.lastActive)}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}

function ProviderKeys() {
  const keys = useProviderKeys();
  const [addOpen, setAddOpen] = React.useState(false);
  const [provider, setProvider] = React.useState("Anthropic");
  const [label, setLabel] = React.useState("");
  const [key, setKey] = React.useState("");
  const [region, setRegion] = React.useState("");
  const [rotateOpen, setRotateOpen] = React.useState(false);
  const [revokeOpen, setRevokeOpen] = React.useState(false);
  const [target, setTarget] = React.useState<string | null>(null);

  function resetAdd() {
    setProvider("Anthropic");
    setLabel("");
    setKey("");
    setRegion("");
  }
  function closeAdd() {
    setAddOpen(false);
    resetAdd();
  }
  function submitAdd() {
    const trimmedLabel = label.trim();
    const trimmedKey = key.trim();
    if (!trimmedLabel || !trimmedKey) return;
    addProviderKey({ provider, label: trimmedLabel, masked: maskSecret(trimmedKey), added: nowIso(), status: "active" });
    closeAdd();
  }

  return (
    <>
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Provider keys</CardTitle>
            <CardDescription className="flex items-center gap-1.5">
              <ShieldCheck className="size-3.5 text-accent" />
              Envelope-encrypted in Vault. Plaintext is used only in memory at call time, never in configs, traces or exports.
            </CardDescription>
          </div>
          <GateButton permission="key.manage" variant="primary" size="sm" onClick={() => setAddOpen(true)}>
            <KeyRound className="size-4" /> Add provider key
          </GateButton>
        </CardHeader>
        <CardContent className="pt-0">
          <Table>
            <THead>
              <TR><TH>Provider</TH><TH>Label</TH><TH>Key</TH><TH>Added</TH><TH>Status</TH><TH className="text-right">Actions</TH></TR>
            </THead>
            <tbody>
              {keys.map((k) => (
                <TR key={k.label}>
                  <TD className="font-medium text-ink">{k.provider}</TD>
                  <TD className="font-mono text-xs text-ink-2">{k.label}</TD>
                  <TD className="font-mono text-xs text-muted">{k.masked}</TD>
                  <TD className="text-muted tnum">{dateShort(k.added)}</TD>
                  <TD>
                    <Badge tone={k.status === "active" ? "good" : "warning"}>
                      {k.status === "active" ? "Active" : "Expiring soon"}
                    </Badge>
                  </TD>
                  <TD className="text-right">
                    <div className="flex justify-end gap-1">
                      <GateButton
                        permission="key.manage"
                        variant="ghost"
                        size="sm"
                        onClick={() => { setTarget(k.label); setRotateOpen(true); }}
                      >
                        Rotate
                      </GateButton>
                      <GateButton
                        permission="key.manage"
                        variant="ghost"
                        size="sm"
                        className="text-critical hover:text-critical"
                        onClick={() => { setTarget(k.label); setRevokeOpen(true); }}
                      >
                        Revoke
                      </GateButton>
                    </div>
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
          <p className="mt-3 text-xs text-warning">
            The Vertex service account key expires in 9 days. Rotate it to avoid published-agent failures.
          </p>
        </CardContent>
      </Card>

      <Dialog
        open={addOpen}
        onClose={closeAdd}
        title="Add provider key"
        description="Keys are envelope-encrypted and only decrypted in memory at call time."
        footer={
          <>
            <Button variant="ghost" onClick={closeAdd}>Cancel</Button>
            <Button variant="primary" onClick={submitAdd} disabled={!label.trim() || !key.trim()}>
              Add key
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Provider">
            <Select value={provider} onChange={(e) => setProvider(e.target.value)}>
              <option value="Anthropic">Anthropic</option>
              <option value="OpenAI">OpenAI</option>
              <option value="Google">Google</option>
              <option value="Mistral">Mistral</option>
            </Select>
          </Field>
          <Field label="Label" hint="A name to recognize this key by.">
            <Input
              placeholder="anthropic-prod"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
            />
          </Field>
          <Field label="API key">
            <Input
              type="password"
              placeholder="sk-..."
              value={key}
              onChange={(e) => setKey(e.target.value)}
            />
          </Field>
          <Field label="Region" hint="Optional. Leave blank for the provider default.">
            <Input
              placeholder="us-east-1"
              value={region}
              onChange={(e) => setRegion(e.target.value)}
            />
          </Field>
        </div>
      </Dialog>

      <ConfirmDialog
        open={rotateOpen}
        onClose={() => setRotateOpen(false)}
        onConfirm={() => toast("Key rotated")}
        title="Rotate provider key"
        body={<>Rotate the key <span className="font-mono text-ink">{target}</span>? The old secret stops working immediately.</>}
        confirmLabel="Rotate key"
        tone="primary"
      />

      <ConfirmDialog
        open={revokeOpen}
        onClose={() => setRevokeOpen(false)}
        onConfirm={() => toast("Key revoked", "neutral")}
        title="Revoke provider key"
        body={<>Revoke the key <span className="font-mono text-ink">{target}</span>? Agents using it will fail until a new key is added.</>}
        confirmLabel="Revoke key"
      />
    </>
  );
}

function Tokens() {
  const tokens = useTokens();
  const [createOpen, setCreateOpen] = React.useState(false);
  const [name, setName] = React.useState("");
  const [expiry, setExpiry] = React.useState("none");
  const [fullToken, setFullToken] = React.useState<string | null>(null);
  const [revokeTarget, setRevokeTarget] = React.useState<string | null>(null);

  function closeCreate() {
    setCreateOpen(false);
    setName("");
    setExpiry("none");
    setFullToken(null);
  }
  function submitCreate() {
    const trimmed = name.trim();
    if (!trimmed) return;
    const full = `ac_live_${genId("")}${genId("")}`.replace(/-/g, "");
    addToken({
      id: genId("tok"),
      name: trimmed,
      agent: "-",
      prefix: full.slice(0, 14) + "…",
      created: nowIso(),
      lastUsed: "-",
      scopes: "invoke",
    });
    setFullToken(full);
  }

  const revokeName = tokens.find((t) => t.id === revokeTarget)?.name ?? "";

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>API tokens</CardTitle>
            <CardDescription>Scoped bearer tokens for published endpoints. Each token is shown once at creation and maps to a single endpoint.</CardDescription>
          </div>
          <GateButton permission="agent.publish" variant="primary" size="sm" onClick={() => setCreateOpen(true)}>
            <Plus className="size-4" /> Create token
          </GateButton>
        </CardHeader>
        <CardContent className="pt-0">
          <Table>
            <THead>
              <TR><TH>Name</TH><TH>Agent</TH><TH>Prefix</TH><TH>Scopes</TH><TH>Created</TH><TH>Last used</TH><TH className="text-right">Actions</TH></TR>
            </THead>
            <tbody>
              {tokens.map((t) => (
                <TR key={t.id}>
                  <TD className="font-medium text-ink">{t.name}</TD>
                  <TD className="text-muted">{t.agent}</TD>
                  <TD className="font-mono text-xs text-ink-2">{t.prefix}</TD>
                  <TD><Badge tone="neutral">{t.scopes}</Badge></TD>
                  <TD className="text-muted tnum">{dateShort(t.created)}</TD>
                  <TD className="text-muted tnum">{t.lastUsed === "-" ? "-" : relative(t.lastUsed)}</TD>
                  <TD className="text-right">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-critical hover:text-critical"
                      onClick={() => setRevokeTarget(t.id)}
                    >
                      Revoke
                    </Button>
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Sample request</CardTitle>
            <CardDescription>Published endpoints accept the scoped token only. Provider keys never leave the platform.</CardDescription>
          </div>
          <CopyButton value={CURL} label="Copy curl" />
        </CardHeader>
        <CardContent>
          <CodeBlock code={CURL} />
        </CardContent>
      </Card>

      <Dialog
        open={createOpen}
        onClose={closeCreate}
        title="Create API token"
        description={fullToken ? undefined : "Scoped bearer token for a published endpoint."}
        footer={
          fullToken ? (
            <Button variant="primary" onClick={closeCreate}>Done</Button>
          ) : (
            <>
              <Button variant="ghost" onClick={closeCreate}>Cancel</Button>
              <Button variant="primary" onClick={submitCreate} disabled={!name.trim()}>Create token</Button>
            </>
          )
        }
      >
        {fullToken ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between gap-3 rounded-xl border border-border bg-elevated px-3 py-2.5">
              <code className="break-all font-mono text-xs text-ink">{fullToken}</code>
              <CopyButton value={fullToken} label="Copy" />
            </div>
            <p className="text-xs text-warning">Copy this now - it is shown only once.</p>
          </div>
        ) : (
          <div className="space-y-4">
            <Field label="Name">
              <Input
                placeholder="soc-dashboard-prod"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </Field>
            <Field label="Scope">
              <Input value="invoke" readOnly />
            </Field>
            <Field label="Expiry">
              <Select value={expiry} onChange={(e) => setExpiry(e.target.value)}>
                <option value="none">No expiry</option>
                <option value="30">30 days</option>
                <option value="90">90 days</option>
              </Select>
            </Field>
          </div>
        )}
      </Dialog>

      <ConfirmDialog
        open={revokeTarget !== null}
        onClose={() => setRevokeTarget(null)}
        onConfirm={() => { if (revokeTarget) revokeToken(revokeTarget); }}
        title="Revoke token"
        body={<>Revoke the token <span className="font-medium text-ink">{revokeName}</span>? Any client using it will stop working immediately.</>}
        confirmLabel="Revoke token"
      />
    </div>
  );
}

function Limits() {
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Spend &amp; concurrency</CardTitle>
            <CardDescription>Ceilings enforced across published endpoints in this workspace.</CardDescription>
          </div>
          <SlidersHorizontal className="size-4 text-muted" />
        </CardHeader>
        <CardContent className="grid gap-5 sm:grid-cols-3">
          <Field label="Monthly spend ceiling"><Input defaultValue="$2,000" /></Field>
          <Field label="Per-endpoint concurrency"><Input defaultValue="25" /></Field>
          <Field label="Request timeout"><Input defaultValue="30s" /></Field>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium text-ink">Block publish without a passing eval</p>
            <p className="mt-0.5 text-xs text-muted">A version cannot be published or re-pointed unless it has a fresh drift eval with a pass verdict.</p>
          </div>
          <span className="inline-flex items-center gap-1.5 rounded-full bg-good/12 px-2.5 py-1 text-xs font-medium text-good">
            <Check className="size-3.5" /> Enabled
          </span>
        </CardContent>
      </Card>
    </div>
  );
}

const CAPABILITIES: { label: string; permission: Permission }[] = [
  { label: "View & playground", permission: "playground.use" },
  { label: "Create & edit agents", permission: "agent.edit" },
  { label: "Run evaluations", permission: "eval.run" },
  { label: "Manage knowledge/tools/MCP", permission: "knowledge.manage" },
  { label: "Manage channels", permission: "channel.manage" },
  { label: "Publish agents", permission: "agent.publish" },
  { label: "Delete agents", permission: "agent.delete" },
  { label: "Manage keys & members", permission: "member.manage" },
  { label: "Configure access policies", permission: "access.manage" },
];

const kindTone: Record<string, "accent" | "violet" | "neutral" | "serious"> = {
  Agent: "accent", Knowledge: "violet", Tool: "neutral", MCP: "serious",
};

function RolesAccess() {
  const agents = useAgents();
  const knowledgeBases = useKnowledgeBases();
  const mcpServers = useMcpServers();
  const tools = useTools();

  const restricted = [
    ...agents.map((a) => ({ key: `agent-${a.id}`, name: a.name, kind: "Agent", policy: a.accessPolicy })),
    ...knowledgeBases.map((k) => ({ key: `kb-${k.id}`, name: k.name, kind: "Knowledge", policy: k.accessPolicy })),
    ...tools.map((t) => ({ key: `tool-${t.name}`, name: t.name, kind: "Tool", policy: t.accessPolicy })),
    ...mcpServers.map((m) => ({ key: `mcp-${m.id}`, name: m.name, kind: "MCP", policy: m.accessPolicy })),
  ].filter((r) => r.policy?.visibility === "restricted");

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Capabilities by role</CardTitle>
            <CardDescription>What each workspace role can do. Read-only; role definitions live in Trellix IAM.</CardDescription>
          </div>
          <Lock className="size-4 text-muted" />
        </CardHeader>
        <CardContent className="pt-0">
          <Table>
            <THead>
              <TR>
                <TH>Capability</TH>
                {ROLE_ORDER.map((r) => (
                  <TH key={r} className="text-center">{ROLE_LABEL[r]}</TH>
                ))}
              </TR>
            </THead>
            <tbody>
              {CAPABILITIES.map((cap) => (
                <TR key={cap.permission}>
                  <TD className="font-medium text-ink">{cap.label}</TD>
                  {ROLE_ORDER.map((r) => (
                    <TD key={r} className="text-center">
                      {ROLE_PERMISSIONS[r].includes(cap.permission)
                        ? <StatusDot tone="good" />
                        : <span className="text-muted">-</span>}
                    </TD>
                  ))}
                </TR>
              ))}
            </tbody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Restricted resources</CardTitle>
            <CardDescription>Resources in this workspace with a per-resource access policy tighter than workspace-wide.</CardDescription>
          </div>
        </CardHeader>
        <CardContent className="pt-0">
          {restricted.length ? (
            <div className="space-y-2">
              {restricted.map((r) => (
                <div
                  key={r.key}
                  className="flex flex-wrap items-center gap-2.5 rounded-xl border border-border bg-elevated/60 px-3 py-2.5"
                >
                  <span className="font-medium text-ink">{r.name}</span>
                  <Badge tone={kindTone[r.kind]}>{r.kind}</Badge>
                  <span className="text-xs text-muted">{policySummary(r.policy)}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted">No restricted resources in this workspace.</p>
          )}
          <Separator className="my-4" />
          <p className="text-xs text-muted">
            Role definitions and assignments live in Trellix IAM. Per-resource policies are configured on each resource&apos;s Access tab.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

/* @backend integration points (this page):
 *  - @backend GET /v1/provider-keys - provider keys list [via useProviderKeys]
 *  - @backend POST /v1/provider-keys - add key to vault, plus rotate/revoke [via addProviderKey]
 *  - @backend GET /v1/agents/:id/tokens - API tokens list [via useTokens]
 *  - @backend POST /v1/agents/:id/tokens - create token (shown once) [via addToken]
 *  - @backend DELETE /v1/tokens/:id - revoke token [via revokeToken]
 *  - @backend GET /v1/members?workspace=:id - IAM-sourced members (read-only) [via useMembers]; Invite is a deep link to Trellix IAM (no POST)
 *  - @backend Roles & access view - from IAM memberships + the access-policy store (per-resource policies)
 *  - @backend the cURL sample is illustrative; the Authentication note describes the IAM/OIDC integration
 * See src/lib/store.ts markers + docs/backend-integration-map.md for the authoritative contracts.
 */
export default function SettingsPage() {
  return (
    <div className="space-y-6">
      <PageHeader title="Settings" description="Workspace configuration, access, credentials and limits." />
      <Tabs
        tabs={[
          { key: "workspace", label: "Workspace", icon: <Building2 className="size-4" />, content: <Workspace /> },
          { key: "members", label: "Members & roles", icon: <Users className="size-4" />, content: <Members /> },
          { key: "roles", label: "Roles & access", icon: <Lock className="size-4" />, content: <RolesAccess /> },
          { key: "keys", label: "Provider keys", icon: <ShieldCheck className="size-4" />, content: <ProviderKeys /> },
          { key: "tokens", label: "API tokens", icon: <KeyRound className="size-4" />, content: <Tokens /> },
          { key: "limits", label: "Limits", icon: <SlidersHorizontal className="size-4" />, content: <Limits /> },
        ]}
      />
    </div>
  );
}
