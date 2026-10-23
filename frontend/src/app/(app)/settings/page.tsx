"use client";

import * as React from "react";
import {
  Building2, KeyRound, ShieldCheck, SlidersHorizontal, Users, Check, Plus, Lock, Search,
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
import { Member, type WorkspaceSettings, type Limits, type EvalPolicy } from "@/lib/data";
import {
  useMembers, useProviderKeys, useTokens,
  useAgents, useKnowledgeBases, useMcpServers, useTools, useChannels, useSkills,
  useWorkspaceSettings, useLimits, useEvalPolicy, useActiveWorkspaceId,
  addProviderKey, rotateProviderKey, revokeProviderKey,
  addToken, revokeToken,
  updateWorkspaceSettings, updateWorkspace, setWorkspaceActive, deleteWorkspace, updateLimits, setEvalPolicy,
  genId, nowIso,
} from "@/lib/store";
import { ROLE_ORDER, ROLE_LABEL, ROLE_PERMISSIONS, type Permission } from "@/lib/permissions";
import { useWorkspace, useAuth } from "@/lib/auth";
import { AccessPolicyEditor } from "@/components/access-policy-editor";
import { ORG } from "@/lib/org";
import { maskSecret } from "@/lib/secrets";
import { policySummary, type AccessPolicy } from "@/lib/access-policy";
import { dateShort, relative } from "@/lib/format";

/** Access-policy editor kinds (mirrors the local Kind in access-policy-editor.tsx). */
type EditorKind = "agent" | "knowledge" | "tool" | "mcp" | "channel" | "skill";

const roleTone: Record<Member["role"], "violet" | "accent" | "neutral"> = {
  Admin: "violet", Publisher: "accent", Builder: "neutral", Viewer: "neutral",
};

const CURL = `curl -N https://agent-platform.internal/v1/agents/soc-triage-copilot/invoke \\
  -H "Authorization: Bearer $AGENT_PLATFORM_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"messages":[{"role":"user","content":"Triage alert 48213"}],"stream":true}'`;

function Workspace() {
  const { workspace, active, isPlatformAdmin } = useWorkspace();
  const settings = useWorkspaceSettings();
  const { can } = useAuth();
  // Single source of truth: settings are selected by activeWorkspaceId, so save/remount off the same id.
  const wsId = useActiveWorkspaceId();
  // Identity/provider edits obey the read-only freeze: can("settings.manage") is true only for a platform
  // admin AND an active workspace. Activate/Delete must BYPASS the freeze, so they gate on isPlatformAdmin
  // (freeze-independent) — otherwise an inactive space becomes a trap.
  // Keyed by wsId so a tenant switch remounts the form (re-seeds state) without a setState-in-effect.
  return (
    <WorkspaceForm
      key={wsId}
      wsId={wsId}
      wsName={workspace?.name ?? ""}
      wsGroupPrefix={workspace?.groupPrefix ?? ""}
      settings={settings}
      active={active}
      canEdit={can("settings.manage")}
      canManageRaw={isPlatformAdmin}
    />
  );
}

function WorkspaceForm({ wsId, wsName, wsGroupPrefix, settings, active, canEdit, canManageRaw }: {
  wsId: string; wsName: string; wsGroupPrefix: string;
  settings: WorkspaceSettings; active: boolean; canEdit: boolean; canManageRaw: boolean;
}) {
  const [name, setName] = React.useState(wsName);
  const [groupPrefix, setGroupPrefix] = React.useState(wsGroupPrefix);
  const [provider, setProvider] = React.useState(settings.defaultProvider);
  const [model, setModel] = React.useState(settings.defaultModel);
  const [deleteOpen, setDeleteOpen] = React.useState(false);
  const [confirmText, setConfirmText] = React.useState("");

  // Resource counts for the delete confirmation. These hooks are scoped to the active workspace (= wsId).
  const agentCount = useAgents().length;
  const kbCount = useKnowledgeBases().length;
  const toolCount = useTools().length;
  const mcpCount = useMcpServers().length;
  const channelCount = useChannels().length;
  const skillCount = useSkills().length;

  const identityDirty = name.trim() !== wsName || groupPrefix.trim() !== wsGroupPrefix;
  // Both identity fields are required — don't let a cleared name/prefix silently no-op (store keeps the old value).
  const identityValid = name.trim().length > 0 && groupPrefix.trim().length > 0;
  const settingsDirty = provider !== settings.defaultProvider || model !== settings.defaultModel;
  // Settings-only changes can save even while the name/prefix is mid-edit; identity saves only when valid.
  const canSave = settingsDirty || (identityDirty && identityValid);
  const editable = canEdit; // platform-admin AND active (false when frozen)

  const save = () => {
    if (identityDirty && identityValid) updateWorkspace(wsId, { name: name.trim(), groupPrefix });
    if (settingsDirty) updateWorkspaceSettings(wsId, { defaultProvider: provider, defaultModel: model });
  };
  const closeDelete = () => { setDeleteOpen(false); setConfirmText(""); };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Workspace</CardTitle>
            <CardDescription>Identity and defaults for this workspace. Applies to every agent inside it.</CardDescription>
          </div>
          <Building2 className="size-4 text-muted" />
        </CardHeader>
        <CardContent className="grid gap-5 sm:grid-cols-2">
          <Field label="Workspace name" hint="Display name (platform-admin editable).">
            <Input value={name} onChange={(e) => setName(e.target.value)} disabled={!editable} />
          </Field>
          <Field label="Slug" hint="Published endpoint URL segment. Immutable — set at creation.">
            <Input value={wsId} readOnly />
          </Field>
          <Field label="IdP group prefix" hint="Per-role access claims are derived from this prefix (e.g. the Okta adapter expands it to ap-<prefix>-viewer/-builder/-publisher). Membership is managed in the IdP.">
            <Input value={groupPrefix} onChange={(e) => setGroupPrefix(e.target.value)} disabled={!editable} placeholder="e.g. acme-soc" />
          </Field>
          <Field label="Default provider">
            <Select value={provider} onChange={(e) => setProvider(e.target.value)} disabled={!editable}>
              <option>Anthropic</option><option>OpenAI</option><option>Google</option><option>Mistral</option>
            </Select>
          </Field>
          <Field label="Default model">
            <Select value={model} onChange={(e) => setModel(e.target.value)} disabled={!editable}>
              <option>claude-opus-5-5</option><option>claude-sonnet-5-5</option><option>gpt-5</option>
            </Select>
          </Field>
          <div className="flex items-end sm:col-span-2">
            <GateButton permission="settings.manage" variant="primary" disabled={!canSave} onClick={save}>
              Save changes
            </GateButton>
          </div>
        </CardContent>
      </Card>

      <Card className="border-critical/30">
        <CardHeader>
          <div>
            <CardTitle className="text-critical">Status &amp; danger zone</CardTitle>
            <CardDescription>
              Deactivating makes the workspace read-only and suspends its endpoints — reversible. Deleting
              removes the workspace and everything in it — permanent.
            </CardDescription>
          </div>
        </CardHeader>
        <CardContent className="space-y-5">
          {/* Activate / Deactivate — bypasses the read-only freeze (raw admin check) so it's always reachable. */}
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="text-sm font-medium text-ink">
                {active ? "Active" : <>Inactive <span className="text-muted">since {dateShort(settings.archivedAt!)}</span></>}
              </p>
              <p className="text-xs text-muted">
                {active ? "The workspace is serving and editable." : "Read-only — all edits and endpoints are suspended."}
              </p>
            </div>
            {active ? (
              <Button variant="outline" disabled={!canManageRaw} onClick={() => setWorkspaceActive(wsId, false)}>Deactivate</Button>
            ) : (
              <Button variant="primary" disabled={!canManageRaw} onClick={() => setWorkspaceActive(wsId, true)}>Activate</Button>
            )}
          </div>
          <Separator />
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="text-sm font-medium text-ink">Delete this workspace</p>
              <p className="text-xs text-muted">Permanently removes the workspace and all of its resources.</p>
            </div>
            <Button variant="danger" disabled={!canManageRaw} onClick={() => setDeleteOpen(true)}>Delete workspace</Button>
          </div>
        </CardContent>
      </Card>

      <Dialog
        open={deleteOpen}
        onClose={closeDelete}
        title="Delete workspace"
        description="This permanently deletes the workspace and everything inside it. This cannot be undone."
        footer={
          <>
            <Button variant="ghost" onClick={closeDelete}>Cancel</Button>
            <Button variant="danger" disabled={confirmText.trim() !== wsName} onClick={() => { deleteWorkspace(wsId); closeDelete(); }}>
              Delete workspace
            </Button>
          </>
        }
      >
        <div className="space-y-4 text-sm text-ink-2">
          <p>Deleting <span className="font-medium text-ink">{wsName}</span> will permanently remove:</p>
          <ul className="list-disc space-y-0.5 pl-5">
            <li>{agentCount} agent(s), {kbCount} knowledge base(s), {toolCount} tool(s), {mcpCount} MCP server(s)</li>
            <li>{channelCount} channel(s), {skillCount} skill(s), and all eval suites, runs, and tokens</li>
          </ul>
          <p className="text-xs text-muted">Provider keys (org-level) and the member roster are not affected.</p>
          <Field label={`Type "${wsName}" to confirm`}>
            <Input value={confirmText} onChange={(e) => setConfirmText(e.target.value)} placeholder={wsName} autoFocus />
          </Field>
        </div>
      </Dialog>
    </div>
  );
}

function Members() {
  const members = useMembers();
  const { workspace } = useWorkspace();
  // Claim-based roster (backend/18 §4): each member's role is the per-workspace role claim THEY hold, so render
  // each row's own role — not the signed-in viewer's resolved role. (A real roster projects each row's role from
  // its own claims; the seeded `Member.role` stands in for that here. Platform admins show as Admin everywhere.)

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Sign-in &amp; identity</CardTitle>
            <CardDescription>
              Everyone signs in through {ORG.idpName} (org-level SSO). Access to <em>this</em> space is granted
              by its per-role access claims, derived from the group prefix
              {workspace?.groupPrefix && <> (<code className="rounded bg-elevated px-1 py-0.5 text-xs text-ink-2">{workspace.groupPrefix}</code>)</>}
              {" "}— add or remove people in {ORG.idpName}, not here.
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
              <Badge tone="neutral">Managed in {ORG.idpName}</Badge>
            </div>
            <CardDescription>
              Read-only roster projected from the workspace&apos;s per-role access claims. Each person&apos;s
              role comes from the claims they hold; platform admins are admins everywhere.
            </CardDescription>
          </div>
        </CardHeader>
        <CardContent className="pt-0">
          <Table>
            <THead>
              <TR><TH>Member</TH><TH>Email</TH><TH>Role</TH><TH>Last active</TH></TR>
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
    addProviderKey({
      id: genId("pk"),
      provider,
      label: trimmedLabel,
      masked: maskSecret(trimmedKey),
      region: region.trim() || undefined,
      added: nowIso(),
      status: "active",
    });
    closeAdd();
  }

  const targetKey = keys.find((k) => k.id === target);

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
              <TR><TH>Provider</TH><TH>Label</TH><TH>Key</TH><TH>Region</TH><TH>Added</TH><TH>Status</TH><TH className="text-right">Actions</TH></TR>
            </THead>
            <tbody>
              {keys.map((k) => {
                const tone = k.status === "active" ? "good" : k.status === "expiring" ? "warning" : "neutral";
                const statusLabel = k.status === "active" ? "Active" : k.status === "expiring" ? "Expiring soon" : "Revoked";
                const revoked = k.status === "revoked";
                return (
                  <TR key={k.id}>
                    <TD className="font-medium text-ink">{k.provider}</TD>
                    <TD className="font-mono text-xs text-ink-2">{k.label}</TD>
                    <TD className="font-mono text-xs text-muted">{k.masked}</TD>
                    <TD className="text-muted tnum">{k.region ?? "-"}</TD>
                    <TD className="text-muted tnum">{dateShort(k.added)}</TD>
                    <TD><Badge tone={tone}>{statusLabel}</Badge></TD>
                    <TD className="text-right">
                      {revoked ? (
                        <span className="text-xs text-muted">-</span>
                      ) : (
                        <div className="flex justify-end gap-1">
                          <GateButton
                            permission="key.manage"
                            variant="ghost"
                            size="sm"
                            onClick={() => { setTarget(k.id); setRotateOpen(true); }}
                          >
                            Rotate
                          </GateButton>
                          <GateButton
                            permission="key.manage"
                            variant="ghost"
                            size="sm"
                            className="text-critical hover:text-critical"
                            onClick={() => { setTarget(k.id); setRevokeOpen(true); }}
                          >
                            Revoke
                          </GateButton>
                        </div>
                      )}
                    </TD>
                  </TR>
                );
              })}
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
        onConfirm={() => { if (target) rotateProviderKey(target); }}
        title="Rotate provider key"
        body={<>Rotate the key <span className="font-mono text-ink">{targetKey?.label}</span>? The old secret stops working immediately.</>}
        confirmLabel="Rotate key"
        tone="primary"
      />

      <ConfirmDialog
        open={revokeOpen}
        onClose={() => setRevokeOpen(false)}
        onConfirm={() => { if (target) revokeProviderKey(target); }}
        title="Revoke provider key"
        body={<>Revoke the key <span className="font-mono text-ink">{targetKey?.label}</span>? Agents using it will fail until a new key is added.</>}
        confirmLabel="Revoke key"
      />
    </>
  );
}

function tokenExpiryIso(v: string): string | undefined {
  if (v === "none") return undefined;
  return new Date(Date.now() + Number(v) * 86_400_000).toISOString();
}

function Tokens() {
  const tokens = useTokens();
  const publishedAgents = useAgents().filter((a) => a.status === "published");
  const [createOpen, setCreateOpen] = React.useState(false);
  const [name, setName] = React.useState("");
  const [endpoint, setEndpoint] = React.useState("");
  const [expiry, setExpiry] = React.useState("none");
  const [fullToken, setFullToken] = React.useState<string | null>(null);
  const [revokeTarget, setRevokeTarget] = React.useState<string | null>(null);

  function closeCreate() {
    setCreateOpen(false);
    setName("");
    setEndpoint("");
    setExpiry("none");
    setFullToken(null);
  }
  function submitCreate() {
    const trimmed = name.trim();
    if (!trimmed || !endpoint) return;
    const full = `ac_live_${genId("")}${genId("")}`.replace(/-/g, "");
    addToken({
      id: genId("tok"),
      name: trimmed,
      agent: endpoint,
      prefix: full.slice(0, 14) + "…",
      created: nowIso(),
      lastUsed: "-",
      scopes: "invoke",
      expires: tokenExpiryIso(expiry),
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
              <TR><TH>Name</TH><TH>Endpoint</TH><TH>Prefix</TH><TH>Scopes</TH><TH>Created</TH><TH>Expires</TH><TH>Last used</TH><TH className="text-right">Actions</TH></TR>
            </THead>
            <tbody>
              {tokens.map((t) => (
                <TR key={t.id}>
                  <TD className="font-medium text-ink">{t.name}</TD>
                  <TD className="text-muted">{t.agent}</TD>
                  <TD className="font-mono text-xs text-ink-2">{t.prefix}</TD>
                  <TD><Badge tone="neutral">{t.scopes}</Badge></TD>
                  <TD className="text-muted tnum">{dateShort(t.created)}</TD>
                  <TD className="text-muted tnum">{t.expires ? dateShort(t.expires) : "Never"}</TD>
                  <TD className="text-muted tnum">{t.lastUsed === "-" ? "-" : relative(t.lastUsed)}</TD>
                  <TD className="text-right">
                    <GateButton
                      permission="agent.publish"
                      variant="ghost"
                      size="sm"
                      className="text-critical hover:text-critical"
                      onClick={() => setRevokeTarget(t.id)}
                    >
                      Revoke
                    </GateButton>
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
              <Button variant="primary" onClick={submitCreate} disabled={!name.trim() || !endpoint}>Create token</Button>
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
            <Field label="Endpoint" hint="The published agent this token can invoke.">
              <Select value={endpoint} onChange={(e) => setEndpoint(e.target.value)}>
                <option value="" disabled>Select a published agent…</option>
                {publishedAgents.map((a) => <option key={a.id} value={a.name}>{a.name}</option>)}
              </Select>
            </Field>
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
            {publishedAgents.length === 0 && (
              <p className="text-xs text-warning">No published agents yet. Publish an agent first to mint a token.</p>
            )}
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
  const limits = useLimits();
  const policy = useEvalPolicy();
  const { can } = useAuth();
  const wsId = useActiveWorkspaceId();
  // Keyed by wsId so a tenant switch remounts the forms (re-seeds state) without a setState-in-effect.
  return (
    <div className="space-y-6">
      <LimitsForm key={wsId} wsId={wsId} limits={limits} canEdit={can("settings.manage")} />
      <EvalPolicyCard key={`ep-${wsId}`} wsId={wsId} policy={policy} canEdit={can("eval.govern")} />
    </div>
  );
}

function EvalPolicyCard({ wsId, policy, canEdit }: { wsId: string; policy: EvalPolicy; canEdit: boolean }) {
  const [minCases, setMinCases] = React.useState(String(policy.minCases));
  const [quality, setQuality] = React.useState(String(policy.qualityThreshold));
  const [budget, setBudget] = React.useState(String(policy.efficiencyBudgetPct));
  const [override, setOverride] = React.useState(policy.allowLowConfidenceOverride);

  const nMin = Number(minCases), nQ = Number(quality), nB = Number(budget);
  const valid =
    Number.isFinite(nMin) && nMin > 0 &&
    Number.isFinite(nQ) && nQ > 0 && nQ <= 100 &&
    Number.isFinite(nB) && nB >= 0;
  const dirty =
    nMin !== policy.minCases || nQ !== policy.qualityThreshold ||
    nB !== policy.efficiencyBudgetPct || override !== policy.allowLowConfidenceOverride;

  function save() {
    if (!valid) return;
    setEvalPolicy(wsId, { minCases: nMin, qualityThreshold: nQ, efficiencyBudgetPct: nB, allowLowConfidenceOverride: override });
  }

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Eval policy</CardTitle>
          <CardDescription>Thresholds the drift verdict and publish gate use across this workspace. The judge is fixed &amp; versioned and is not configurable.</CardDescription>
        </div>
        <SlidersHorizontal className="size-4 text-muted" />
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="grid gap-5 sm:grid-cols-3">
          <Field label="Min cases for a confident verdict" hint="Below this → low-confidence.">
            <Input inputMode="numeric" value={minCases} onChange={(e) => setMinCases(e.target.value)} disabled={!canEdit} />
          </Field>
          <Field label="Quality threshold (%)" hint="Pass requires quality ≥ this.">
            <Input inputMode="numeric" value={quality} onChange={(e) => setQuality(e.target.value)} disabled={!canEdit} />
          </Field>
          <Field label="Efficiency budget (cost % over baseline)" hint="Fail if cost regresses beyond this.">
            <Input inputMode="numeric" value={budget} onChange={(e) => setBudget(e.target.value)} disabled={!canEdit} />
          </Field>
        </div>
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-sm font-medium text-ink">Allow low-confidence publish override</p>
            <p className="mt-0.5 text-xs text-muted">When off, a low-confidence eval blocks publish until the suite is expanded past the min-case floor.</p>
          </div>
          <button
            type="button"
            disabled={!canEdit}
            onClick={() => setOverride((v) => !v)}
            className={`rounded-md border px-2.5 py-1 text-xs font-medium transition-colors disabled:pointer-events-none disabled:opacity-60 ${override ? "border-transparent bg-accent-soft text-accent" : "border-border bg-elevated text-ink-2"}`}
          >
            {override ? "Allowed" : "Blocked"}
          </button>
        </div>
        <div className="flex items-center justify-between gap-3">
          <p className={`text-xs ${dirty && !valid ? "text-warning" : "text-muted"}`}>
            {dirty && !valid
              ? "Enter positive numbers (quality ≤ 100)."
              : "Governs the drift verdict and the publish low-confidence gate."}
          </p>
          <GateButton permission="eval.govern" variant="primary" disabled={!dirty || !valid} onClick={save}>Save policy</GateButton>
        </div>
      </CardContent>
    </Card>
  );
}

function LimitsForm({ wsId, limits, canEdit }: { wsId: string; limits: Limits; canEdit: boolean }) {
  const [spend, setSpend] = React.useState(String(limits.monthlySpendUsd));
  const [concurrency, setConcurrency] = React.useState(String(limits.maxConcurrent));
  const [timeoutS, setTimeoutS] = React.useState(String(limits.requestTimeoutS));

  const nSpend = Number(spend);
  const nConcurrency = Number(concurrency);
  const nTimeout = Number(timeoutS);
  // All three are ceilings; a 0 or NaN value would (once enforced in P2) block every request, so require > 0.
  const valid = [nSpend, nConcurrency, nTimeout].every((n) => Number.isFinite(n) && n > 0);
  const dirty =
    nSpend !== limits.monthlySpendUsd ||
    nConcurrency !== limits.maxConcurrent ||
    nTimeout !== limits.requestTimeoutS;

  function save() {
    if (!valid) return;
    updateLimits(wsId, { monthlySpendUsd: nSpend, maxConcurrent: nConcurrency, requestTimeoutS: nTimeout });
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Spend &amp; concurrency</CardTitle>
            <CardDescription>Per-workspace ceilings across every published endpoint in this workspace.</CardDescription>
          </div>
          <SlidersHorizontal className="size-4 text-muted" />
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="grid gap-5 sm:grid-cols-3">
            <Field label="Monthly spend ceiling (USD)">
              <Input inputMode="numeric" value={spend} onChange={(e) => setSpend(e.target.value)} disabled={!canEdit} />
            </Field>
            <Field label="Max concurrent requests" hint="Workspace-wide.">
              <Input inputMode="numeric" value={concurrency} onChange={(e) => setConcurrency(e.target.value)} disabled={!canEdit} />
            </Field>
            <Field label="Request timeout (seconds)">
              <Input inputMode="numeric" value={timeoutS} onChange={(e) => setTimeoutS(e.target.value)} disabled={!canEdit} />
            </Field>
          </div>
          <div className="flex items-center justify-between gap-3">
            <p className={`text-xs ${dirty && !valid ? "text-warning" : "text-muted"}`}>
              {dirty && !valid
                ? "Enter positive numbers for all three ceilings."
                : "Stored now; enforced at runtime in P2 (budget reservation + concurrency semaphores)."}
            </p>
            <GateButton permission="settings.manage" variant="primary" disabled={!dirty || !valid} onClick={save}>Save limits</GateButton>
          </div>
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
  { label: "Author eval cases & suites", permission: "eval.author" },
  { label: "Manage knowledge/tools/MCP", permission: "knowledge.manage" },
  { label: "Manage channels", permission: "channel.manage" },
  { label: "Publish agents", permission: "agent.publish" },
  { label: "Delete agents", permission: "agent.delete" },
  { label: "Manage keys & members", permission: "member.manage" },
  { label: "Configure access policies", permission: "access.manage" },
  { label: "Govern eval policy & baselines", permission: "eval.govern" },
  { label: "Edit workspace settings & limits", permission: "settings.manage" },
];

const kindTone: Record<string, "accent" | "violet" | "neutral" | "serious" | "good" | "warning"> = {
  Agent: "accent", Knowledge: "violet", Tool: "neutral", MCP: "serious", Channel: "good", Skill: "warning",
};

type AccessRow = { key: string; id: string; name: string; kindLabel: string; editorKind: EditorKind; policy: AccessPolicy | undefined };

function RolesAccess() {
  const agents = useAgents();
  const knowledgeBases = useKnowledgeBases();
  const mcpServers = useMcpServers();
  const tools = useTools();
  const channels = useChannels();
  const skills = useSkills();
  const { can } = useAuth();
  const canManage = can("access.manage");

  const [query, setQuery] = React.useState("");
  const [kindFilter, setKindFilter] = React.useState<"all" | EditorKind>("all");
  const [visFilter, setVisFilter] = React.useState<"all" | "restricted" | "workspace">("restricted");
  const [capsOpen, setCapsOpen] = React.useState(false);
  const [target, setTarget] = React.useState<{ editorKind: EditorKind; id: string } | null>(null);

  // Tools have no id — the store keys them by name (setAccessPolicy keyOf = id ?? name), so pass name.
  const all: AccessRow[] = [
    ...agents.map((a) => ({ key: `agent-${a.id}`, id: a.id, name: a.name, kindLabel: "Agent", editorKind: "agent" as const, policy: a.accessPolicy })),
    ...knowledgeBases.map((k) => ({ key: `kb-${k.id}`, id: k.id, name: k.name, kindLabel: "Knowledge", editorKind: "knowledge" as const, policy: k.accessPolicy })),
    ...tools.map((t) => ({ key: `tool-${t.name}`, id: t.name, name: t.name, kindLabel: "Tool", editorKind: "tool" as const, policy: t.accessPolicy })),
    ...mcpServers.map((m) => ({ key: `mcp-${m.id}`, id: m.id, name: m.name, kindLabel: "MCP", editorKind: "mcp" as const, policy: m.accessPolicy })),
    ...channels.map((c) => ({ key: `channel-${c.id}`, id: c.id, name: c.name, kindLabel: "Channel", editorKind: "channel" as const, policy: c.accessPolicy })),
    ...skills.map((s) => ({ key: `skill-${s.id}`, id: s.id, name: s.name, kindLabel: "Skill", editorKind: "skill" as const, policy: s.accessPolicy })),
  ];
  // Search matches name, kind, and the roles/teams named in the policy (policySummary spells those out).
  const q = query.trim().toLowerCase();
  const rows = all.filter((r) => {
    if (kindFilter !== "all" && r.editorKind !== kindFilter) return false;
    const isRestricted = r.policy?.visibility === "restricted";
    if (visFilter === "restricted" && !isRestricted) return false;
    if (visFilter === "workspace" && isRestricted) return false;
    if (q && !`${r.name} ${r.kindLabel} ${policySummary(r.policy)}`.toLowerCase().includes(q)) return false;
    return true;
  });
  const filtering = q !== "" || kindFilter !== "all" || visFilter !== "all";
  const targetRow = target ? all.find((r) => r.editorKind === target.editorKind && r.id === target.id) : undefined;

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Resource access</CardTitle>
            <CardDescription>
              Per-resource access policies across this workspace. {canManage ? "Open one to restrict it or adjust who can use and manage it." : "Read-only — ask a workspace admin to change a policy."}
            </CardDescription>
          </div>
          <Button variant="outline" size="sm" onClick={() => setCapsOpen(true)}>
            <Lock className="size-3.5" /> Capabilities by role
          </Button>
        </CardHeader>
        <CardContent className="pt-0">
          {/* Search + parameter filters: free text (name / kind / roles / teams), kind, and visibility. */}
          <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-center">
            <div className="relative flex-1">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted" />
              <Input
                className="pl-8"
                placeholder="Search by name, kind, role or team…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
            <Select className="sm:w-40" value={kindFilter} onChange={(e) => setKindFilter(e.target.value as typeof kindFilter)}>
              <option value="all">All kinds</option>
              <option value="agent">Agents</option>
              <option value="knowledge">Knowledge</option>
              <option value="tool">Tools</option>
              <option value="mcp">MCP</option>
              <option value="channel">Channels</option>
              <option value="skill">Skills</option>
            </Select>
            <Select className="sm:w-44" value={visFilter} onChange={(e) => setVisFilter(e.target.value as typeof visFilter)}>
              <option value="restricted">Restricted only</option>
              <option value="workspace">Workspace-wide</option>
              <option value="all">Any visibility</option>
            </Select>
          </div>
          {rows.length ? (
            <div className="space-y-2">
              {rows.map((r) => (
                <div
                  key={r.key}
                  className="flex flex-wrap items-center gap-2.5 rounded-xl border border-border bg-elevated/60 px-3 py-2.5"
                >
                  <span className="font-medium text-ink">{r.name}</span>
                  <Badge tone={kindTone[r.kindLabel]}>{r.kindLabel}</Badge>
                  <span className="text-xs text-muted">{policySummary(r.policy)}</span>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="ml-auto"
                    onClick={() => setTarget({ editorKind: r.editorKind, id: r.id })}
                  >
                    {canManage ? "Manage access" : "View access"}
                  </Button>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted">
              {filtering ? "No resources match these filters." : "No resources in this workspace."}
            </p>
          )}
          <Separator className="my-4" />
          <p className="text-xs text-muted">
            Role definitions and assignments live in {ORG.idpName}. Policies here are the same per-resource policies editable on each resource&apos;s own Access tab.
          </p>
        </CardContent>
      </Card>

      <Dialog
        open={target !== null}
        onClose={() => setTarget(null)}
        title={targetRow ? `Access — ${targetRow.name}` : "Access"}
        description="Who can use (invoke/bind) and who can manage (edit/publish) this resource, by role and IAM team."
        footer={<Button variant="ghost" onClick={() => setTarget(null)}>Close</Button>}
      >
        {targetRow && (
          <AccessPolicyEditor
            key={`${targetRow.editorKind}-${targetRow.id}`}
            kind={targetRow.editorKind}
            id={targetRow.id}
            policy={targetRow.policy}
            readOnly={!canManage}
          />
        )}
      </Dialog>

      <Dialog
        open={capsOpen}
        onClose={() => setCapsOpen(false)}
        title="Capabilities by role"
        description={`What each workspace role can do. Read-only; role definitions live in ${ORG.idpName}.`}
        size="lg"
        footer={<Button variant="ghost" onClick={() => setCapsOpen(false)}>Close</Button>}
      >
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
      </Dialog>
    </div>
  );
}

/* @backend integration points (this page):
 *  - @backend GET /v1/workspaces/:id - workspace config (slug read-only; name/access/provider/model editable) [via useWorkspace + useWorkspaceSettings]
 *  - @backend PATCH /v1/workspaces/:id - save identity name/idp_binding group prefix (admin, backend/18 §3) [via updateWorkspace] + default provider/model [via updateWorkspaceSettings]
 *  - @backend POST /v1/workspaces/:id/archive (deactivate) + NEW reactivate endpoint - reversible Inactive toggle (admin, bypasses the read-only freeze) [via setWorkspaceActive]
 *  - @backend NO hard-delete endpoint (backend archives) - mock-only cascade removes the space + all its resources (admin) [via deleteWorkspace]
 *  - @backend GET /v1/workspaces/:id/limits - per-workspace spend/concurrency/timeout [via useLimits]
 *  - @backend PUT /v1/workspaces/:id/limits - save limits (admin; enforced at runtime in P2) [via updateLimits]
 *  - @backend GET /v1/provider-keys - provider keys list [via useProviderKeys]
 *  - @backend POST /v1/provider-keys - add key to vault [via addProviderKey]
 *  - @backend POST /v1/provider-keys/:id/rotate|revoke - rotate/revoke a key (admin) [via rotateProviderKey/revokeProviderKey]
 *  - @backend GET /v1/agents/:id/tokens - API tokens list [via useTokens]
 *  - @backend POST /v1/agents/:id/tokens - create token bound to a published endpoint, shown once (publisher+) [via addToken]
 *  - @backend DELETE /v1/tokens/:id - revoke token (publisher+) [via revokeToken]
 *  - @backend GET /v1/members?workspace=:id - READ-ONLY roster [via useMembers]; membership is CLAIM-driven
 *    (each person's role comes from their per-workspace role claims, adapter projection backend/18 §4). No
 *    in-app invite/remove — managed in the IdP.
 *  - @backend PUT /v1/{agents|knowledge|tools|mcp|channels|skills}/:id/access-policy - Roles & access now edits per-resource policies across all 6 kinds (admin) [via setAccessPolicy / AccessPolicyEditor]
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
