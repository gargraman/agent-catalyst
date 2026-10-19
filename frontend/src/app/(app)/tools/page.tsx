"use client";

import * as React from "react";
import Link from "next/link";
import { Boxes, Plug, Plus, RefreshCw, ShieldCheck, Trash2, Wrench } from "lucide-react";
import {
  Card, CardHeader, CardTitle, CardContent, CardDescription, Badge, StatusDot, Button, Field, Input, Select, Textarea,
  Table, THead, TH, TR, TD,
} from "@/components/ui";
import { Dialog, ConfirmDialog } from "@/components/dialog";
import { Tabs } from "@/components/tabs";
import { StatTile } from "@/components/stat-tile";
import { GateButton } from "@/components/gate";
import { AccessPolicyEditor } from "@/components/access-policy-editor";
import { useAuth, usePrincipal } from "@/lib/auth";
import { canUse, policySummary, type AccessPolicy } from "@/lib/access-policy";
import { useMcpServers, useTools, useAgents, addTool, updateTool, deleteTool, reconnectMcpServer, genId } from "@/lib/store";
import { mcpAuthLabel } from "@/lib/mcp";
import { riskTone } from "@/lib/tools";
import { maskSecret, vaultRef } from "@/lib/secrets";
import type { HttpToolConfig, McpServer, RepoTool } from "@/lib/data";
import { relative } from "@/lib/format";

function AccessControl({ kind, id, policy }: { kind: "tool" | "mcp"; id: string; policy?: AccessPolicy }) {
  const { can } = useAuth();
  const admin = can("access.manage");
  const [open, setOpen] = React.useState(false);
  return (
    <div className="space-y-2 border-t border-border pt-2.5">
      <div className="flex items-center justify-between gap-2 text-xs">
        <span className="text-muted">Access: <span className="text-ink-2">{policySummary(policy)}</span></span>
        {admin && (
          <Button variant="ghost" size="sm" onClick={() => setOpen((o) => !o)}>{open ? "Hide access" : "Edit access"}</Button>
        )}
      </div>
      {admin && open && <AccessPolicyEditor kind={kind} id={id} policy={policy} readOnly={false} />}
    </div>
  );
}

/* @backend integration points (this page):
 *  - @backend GET /v1/tools - tool repository list [via useTools]
 *  - @backend POST /v1/tools - add a repository tool [via addTool]
 *  - @backend PUT/DELETE /v1/tools/:id - edit/remove a repository tool [via updateTool/deleteTool]
 *  - @backend GET /v1/mcp-servers - connected MCP servers list [via useMcpServers]
 *  - @backend POST /v1/mcp-servers/:id/discover - reconnect + re-discover [via reconnectMcpServer]
 *  - @backend PUT /v1/{tools|mcp}/:id/access-policy - per-resource access edits [via setAccessPolicy]
 * See src/lib/store.ts markers + docs/backend-integration-map.md for the authoritative contracts.
 */
export default function ToolsPage() {
  const principal = usePrincipal();
  const servers = useMcpServers().filter((s) => canUse(s.accessPolicy, principal));
  const tools = useTools().filter((t) => canUse(t.accessPolicy, principal));
  const connected = servers.filter((s) => s.status === "connected").length;
  const discovered = servers.reduce((s, m) => s + m.toolCount, 0);
  const errored = servers.filter((s) => s.status === "error").length;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-ink">Tools & MCP</h1>
          <p className="mt-1 text-sm text-muted">Connect external systems over MCP and manage the shared tool repository.</p>
        </div>
        <GateButton permission="mcp.manage" variant="primary" href="/tools/mcp/new"><Plus className="size-4" /> Add MCP server</GateButton>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile label="Connected servers" value={String(connected)} hint="MCP" icon={<Plug className="size-4" />} />
        <StatTile label="Discovered tools" value={String(discovered)} hint="across servers" icon={<Boxes className="size-4" />} />
        <StatTile label="Repository tools" value={String(tools.length)} hint="HTTP, built-in, sub-agent" icon={<Wrench className="size-4" />} />
        <StatTile label="Servers in error" value={String(errored)} hint="needs attention" icon={<RefreshCw className="size-4" />} />
      </div>

      <Tabs
        tabs={[
          { key: "mcp", label: "MCP servers", icon: <Plug className="size-4" />, content: <McpTab servers={servers} /> },
          { key: "repo", label: "Tool repository", icon: <Wrench className="size-4" />, content: <RepoTab tools={tools} /> },
        ]}
      />
    </div>
  );
}

function McpTab({ servers }: { servers: McpServer[] }) {
  const [reconnect, setReconnect] = React.useState<McpServer | null>(null);

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="grid gap-4 md:grid-cols-2 lg:col-span-2">
        {servers.map((s) => {
          const tone = s.status === "connected" ? "good" : s.status === "error" ? "critical" : "neutral";
          return (
            <Card key={s.id}>
              <CardHeader>
                <div>
                  <CardTitle className="flex items-center gap-2">
                    <Plug className="size-4 text-muted" />
                    <Link href={`/tools/mcp/${s.id}`} className="hover:underline">{s.name}</Link>
                  </CardTitle>
                  <CardDescription className="font-mono">{s.endpoint}</CardDescription>
                </div>
                <Badge tone={s.transport === "http" ? "accent" : "violet"}>{s.transport}</Badge>
              </CardHeader>
              <CardContent className="space-y-3 pt-0">
                <div className="flex items-center justify-between text-xs">
                  <span className="inline-flex items-center gap-1.5 capitalize text-ink-2"><StatusDot tone={tone} />{s.status}</span>
                  <span className="text-muted">{mcpAuthLabel(s.auth)} · {s.toolCount} tools · {s.boundAgents} agents</span>
                </div>
                {s.status === "error" ? (
                  <div className="space-y-2 rounded-lg border border-critical/30 bg-critical/10 p-3">
                    <p className="text-xs text-ink-2">Discovery failed. Reconnect to refresh the tool catalog.</p>
                    <GateButton permission="mcp.manage" variant="outline" size="sm" onClick={() => setReconnect(s)}><RefreshCw className="size-3.5" /> Reconnect</GateButton>
                  </div>
                ) : (
                  <div className="space-y-1.5">
                    {s.tools.slice(0, 3).map((t) => (
                      <div key={t.name} className="flex items-center justify-between rounded-md border border-border bg-elevated px-2.5 py-1.5">
                        <span className="font-mono text-xs text-ink-2">{t.name}</span>
                        <Badge tone={t.enabled ? "good" : "neutral"}>{t.enabled ? "enabled" : "disabled"}</Badge>
                      </div>
                    ))}
                    <Link href={`/tools/mcp/${s.id}`} className="inline-block text-xs text-accent hover:underline">
                      {s.toolCount > 3 ? `View all ${s.toolCount} tools` : "Manage server"}
                    </Link>
                  </div>
                )}
                <div className="text-[11px] text-muted">Last discovery {relative(s.lastDiscovery)}</div>
                <AccessControl kind="mcp" id={s.id} policy={s.accessPolicy} />
              </CardContent>
            </Card>
          );
        })}
      </div>

      <Card className="h-fit">
        <CardHeader><CardTitle>How MCP works</CardTitle></CardHeader>
        <CardContent className="space-y-4 pt-0">
          {[
            { n: "1", t: "Register", d: "Add a stdio or HTTP server. Credentials are vaulted." },
            { n: "2", t: "Discover", d: "We list the server's tools and cache their schemas." },
            { n: "3", t: "Select", d: "Pick an allow-list of tools to expose to agents." },
            { n: "4", t: "Bind", d: "Attach selected tools to a version. Schemas are snapshotted." },
          ].map((s) => (
            <div key={s.n} className="flex gap-3">
              <span className="inline-flex size-6 shrink-0 items-center justify-center rounded-full bg-accent-soft text-xs font-semibold text-accent">{s.n}</span>
              <div>
                <div className="text-sm font-medium text-ink">{s.t}</div>
                <div className="text-xs text-muted">{s.d}</div>
              </div>
            </div>
          ))}
          <div className="flex items-start gap-2 rounded-lg border border-border bg-elevated p-3 text-xs text-muted">
            <ShieldCheck className="mt-0.5 size-3.5 shrink-0 text-good" />
            MCP runs client-side in an egress-restricted worker, so agents stay provider-neutral.
          </div>
        </CardContent>
      </Card>

      <ConfirmDialog
        open={!!reconnect}
        onClose={() => setReconnect(null)}
        onConfirm={() => { if (reconnect) reconnectMcpServer(reconnect.id); }}
        title="Reconnect MCP server"
        confirmLabel="Reconnect"
        tone="primary"
        body={<>Re-authenticate <span className="font-medium text-ink">{reconnect?.name}</span> and re-discover its tool catalog?</>}
      />
    </div>
  );
}

function RepoTab({ tools }: { tools: RepoTool[] }) {
  const [editing, setEditing] = React.useState<RepoTool | null | undefined>(undefined); // undefined = closed, null = new
  const [del, setDel] = React.useState<RepoTool | null>(null);
  const { can } = useAuth();
  const admin = can("access.manage");
  const [openAccess, setOpenAccess] = React.useState<string | null>(null);

  return (
    <Card>
      <CardHeader>
        <div><CardTitle>Tool repository</CardTitle><CardDescription>Reusable tools that any agent version can bind.</CardDescription></div>
        <GateButton permission="tool.manage" variant="outline" size="sm" onClick={() => setEditing(null)}><Plus className="size-3.5" /> New tool</GateButton>
      </CardHeader>
      <CardContent className="pt-0">
        <Table>
          <THead><TR><TH>Tool</TH><TH>Kind</TH><TH>Risk</TH><TH>Description</TH><TH>Access</TH><TH className="text-right">Used by</TH><TH></TH></TR></THead>
          <tbody>
            {tools.map((t) => (
              <React.Fragment key={t.name}>
                <TR>
                  <TD><span className="font-mono text-xs text-ink-2">{t.name}</span></TD>
                  <TD><Badge tone={t.kind === "HTTP" ? "accent" : t.kind === "Sub-agent" ? "violet" : "neutral"}>{t.kind}</Badge></TD>
                  <TD>{t.riskTier ? <Badge tone={riskTone(t.riskTier)}>{t.riskTier}</Badge> : <span className="text-xs text-muted">—</span>}</TD>
                  <TD className="text-ink-2">
                    {t.description}
                    {t.httpConfig && <span className="mt-0.5 block font-mono text-xs text-muted">{t.httpConfig.method} {t.httpConfig.url}</span>}
                    {t.kind === "Sub-agent" && t.subagentTarget && <span className="mt-0.5 block text-xs text-muted">→ {t.subagentTarget} · depth {t.maxDepth ?? 2} · fan-out {t.maxFanout ?? 3}</span>}
                    {t.kind === "Built-in" && t.builtinId && <span className="mt-0.5 block font-mono text-xs text-muted">{t.builtinId}</span>}
                  </TD>
                  <TD className="text-xs text-muted">{policySummary(t.accessPolicy)}</TD>
                  <TD className="text-right tnum text-ink-2">{t.usedBy} {t.usedBy === 1 ? "agent" : "agents"}</TD>
                  <TD className="text-right">
                    <div className="flex items-center justify-end gap-1">
                      {admin && (
                        <Button variant="ghost" size="sm" onClick={() => setOpenAccess((v) => (v === t.name ? null : t.name))}>
                          {openAccess === t.name ? "Hide access" : "Edit access"}
                        </Button>
                      )}
                      <GateButton permission="tool.manage" variant="ghost" size="sm" onClick={() => setEditing(t)}>Edit</GateButton>
                      <GateButton permission="tool.manage" variant="ghost" size="sm" onClick={() => setDel(t)} aria-label="Delete tool"><Trash2 className="size-4" /></GateButton>
                    </div>
                  </TD>
                </TR>
                {admin && openAccess === t.name && (
                  <TR>
                    <TD colSpan={7}>
                      <AccessPolicyEditor kind="tool" id={t.name} policy={t.accessPolicy} readOnly={false} />
                    </TD>
                  </TR>
                )}
              </React.Fragment>
            ))}
          </tbody>
        </Table>
      </CardContent>
      <ToolDialog key={editing === undefined ? "closed" : (editing?.name ?? "new")} open={editing !== undefined} editing={editing ?? null} onClose={() => setEditing(undefined)} />
      <ConfirmDialog
        open={!!del}
        onClose={() => setDel(null)}
        onConfirm={() => { if (del) deleteTool(del.name); }}
        title="Delete tool"
        confirmLabel="Delete"
        tone="danger"
        body={
          <>
            Remove <span className="font-mono text-ink">{del?.name}</span> from the repository?
            {del && del.usedBy > 0 && (
              <span className="mt-2 block text-critical">{del.usedBy} {del.usedBy === 1 ? "agent" : "agents"} still bind this tool — those bindings will stop resolving.</span>
            )}
          </>
        }
      />
    </Card>
  );
}

const BUILTIN_CATALOG = ["code-exec-python", "web-search"];
const RISK_OPTIONS: { value: NonNullable<RepoTool["riskTier"]>; label: string }[] = [
  { value: "read", label: "Read (no side effects)" },
  { value: "write", label: "Write (may change state)" },
  { value: "destructive", label: "Destructive (requires approval)" },
];

const AUTH_OPTIONS: { value: HttpToolConfig["auth"]; label: string }[] = [
  { value: "none", label: "None" },
  { value: "bearer", label: "Bearer token" },
  { value: "api_key_header", label: "API key header" },
  { value: "basic", label: "Basic auth" },
];

// Keyed by the tool name (or "new") in RepoTab so it remounts with fresh initial state per open —
// initializing from `editing` props directly, no setState-in-effect.
function ToolDialog({ open, editing, onClose }: { open: boolean; editing: RepoTool | null; onClose: () => void }) {
  const agents = useAgents();
  const tools = useTools();
  const isEdit = !!editing;
  const [kind, setKind] = React.useState<RepoTool["kind"]>(editing?.kind ?? "HTTP");
  const [name, setName] = React.useState(editing?.name ?? "");
  const [description, setDescription] = React.useState(editing?.description ?? "");
  const [riskTier, setRiskTier] = React.useState<NonNullable<RepoTool["riskTier"]>>(editing?.riskTier ?? "read");
  const [method, setMethod] = React.useState<HttpToolConfig["method"]>(editing?.httpConfig?.method ?? "GET");
  const [url, setUrl] = React.useState(editing?.httpConfig?.url ?? "");
  const [auth, setAuth] = React.useState<HttpToolConfig["auth"]>(editing?.httpConfig?.auth ?? "none");
  const [secret, setSecret] = React.useState("");
  const [headers, setHeaders] = React.useState(editing?.httpConfig?.headers ?? "");
  const [argsSchema, setArgsSchema] = React.useState(editing?.httpConfig?.argsSchema ?? "");
  const [egress, setEgress] = React.useState(editing?.httpConfig?.egress?.join(", ") ?? "");
  const [agent, setAgent] = React.useState(editing?.subagentTarget ?? agents[0]?.name ?? "");
  const [catalog, setCatalog] = React.useState(editing?.builtinId ?? BUILTIN_CATALOG[0]);
  const [maxDepth, setMaxDepth] = React.useState(String(editing?.maxDepth ?? 2));
  const [maxFanout, setMaxFanout] = React.useState(String(editing?.maxFanout ?? 3));

  const trimmedName = name.trim();
  const nameTaken = !isEdit && tools.some((t) => t.name === trimmedName);
  // An edited HTTP tool keeps its existing vaulted secret if the auth method is unchanged and no new one typed.
  const keepExistingSecret = !!editing?.httpConfig?.authRef && editing.httpConfig.auth === auth;
  const needsSecret = kind === "HTTP" && auth !== "none" && !keepExistingSecret;
  const argsSchemaError = (() => {
    const v = argsSchema.trim();
    if (!v) return "";
    try { JSON.parse(v); return ""; } catch { return "Arguments schema must be valid JSON."; }
  })();
  const headersError = headers
    .split("\n").map((l) => l.trim()).filter(Boolean)
    .some((l) => !/^[^:]+:.+/.test(l)) ? "Each header line must be in \"Key: Value\" form." : "";

  const valid =
    trimmedName.length > 0 && !nameTaken && description.trim().length > 0 &&
    (kind !== "HTTP" || url.trim().length > 0) &&
    (!needsSecret || secret.trim().length > 0) &&
    !argsSchemaError && !headersError;

  function submit() {
    if (!valid) return;
    const base = { name: trimmedName, kind, description: description.trim(), riskTier } as Partial<RepoTool>;
    let patch: Partial<RepoTool>;
    if (kind === "HTTP") {
      const authRef =
        auth === "none" ? undefined
          : secret.trim() ? { masked: maskSecret(secret), vaultPath: vaultRef("tool", genId("tool")) }
          : editing?.httpConfig?.authRef; // preserve existing vaulted secret on edit
      const egressList = egress.split(",").map((s) => s.trim()).filter(Boolean);
      const httpConfig: HttpToolConfig = {
        method, url: url.trim(), auth,
        ...(authRef ? { authRef } : {}),
        ...(headers.trim() ? { headers: headers.trim() } : {}),
        ...(argsSchema.trim() ? { argsSchema: argsSchema.trim() } : {}),
        ...(egressList.length ? { egress: egressList } : {}),
      };
      patch = { ...base, httpConfig, subagentTarget: undefined, builtinId: undefined };
    } else if (kind === "Sub-agent") {
      patch = {
        ...base, subagentTarget: agent,
        maxDepth: Math.max(1, parseInt(maxDepth, 10) || 2),
        maxFanout: Math.max(1, parseInt(maxFanout, 10) || 3),
        httpConfig: undefined, builtinId: undefined,
      };
    } else {
      patch = { ...base, builtinId: catalog, httpConfig: undefined, subagentTarget: undefined };
    }

    if (isEdit && editing) updateTool(editing.name, patch);
    else addTool({ name: trimmedName, kind, description: description.trim(), usedBy: 0, riskTier, ...patch });
    onClose();
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={isEdit ? "Edit tool" : "New tool"}
      description={isEdit ? "Update this repository tool." : "Add a reusable tool to the repository."}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!valid} onClick={submit}><Plus className="size-4" /> {isEdit ? "Save tool" : "Add tool"}</Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Kind">
          <Select value={kind} onChange={(e) => setKind(e.target.value as RepoTool["kind"])} disabled={isEdit}>
            <option value="HTTP">HTTP</option>
            <option value="Built-in">Built-in</option>
            <option value="Sub-agent">Sub-agent</option>
          </Select>
        </Field>
        <Field label="Name">
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. http-enrich-ip" disabled={isEdit} />
        </Field>
        {nameTaken && <p className="-mt-2 text-xs text-critical">A tool named &quot;{trimmedName}&quot; already exists.</p>}
        <Field label="Description">
          <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What does this tool do?" />
        </Field>
        <Field label="Risk tier" hint="Drives the runtime approval gate; destructive tools require human approval.">
          <Select value={riskTier} onChange={(e) => setRiskTier(e.target.value as NonNullable<RepoTool["riskTier"]>)}>
            {RISK_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </Select>
        </Field>

        {kind === "HTTP" && (
          <div className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-3">
              <Field label="Method">
                <Select value={method} onChange={(e) => setMethod(e.target.value as HttpToolConfig["method"])}>
                  <option value="GET">GET</option>
                  <option value="POST">POST</option>
                  <option value="PUT">PUT</option>
                  <option value="DELETE">DELETE</option>
                </Select>
              </Field>
              <div className="sm:col-span-2">
                <Field label="URL">
                  <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://api.internal/endpoint" />
                </Field>
              </div>
            </div>
            <div className="grid gap-4 sm:grid-cols-3">
              <Field label="Auth">
                <Select value={auth} onChange={(e) => setAuth(e.target.value as HttpToolConfig["auth"])}>
                  {AUTH_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </Select>
              </Field>
              {auth !== "none" && (
                <div className="sm:col-span-2">
                  <Field label="Credential" hint={keepExistingSecret ? "Leave blank to keep the current vaulted secret." : "Stored in the vault; only a masked reference is kept."}>
                    <Input type="password" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder={keepExistingSecret ? "•••••••• (unchanged)" : "Paste secret"} />
                  </Field>
                </div>
              )}
            </div>
            <Field label="Egress allow-list" hint="Optional. Comma-separated domains this tool may reach (deny-by-default).">
              <Input value={egress} onChange={(e) => setEgress(e.target.value)} placeholder="api.internal, example.com" />
            </Field>
            <Field label="Headers" hint="Optional. One per line, e.g. X-Env: prod">
              <Textarea rows={2} value={headers} onChange={(e) => setHeaders(e.target.value)} placeholder="Accept: application/json" />
            </Field>
            {headersError && <p className="-mt-2 text-xs text-critical">{headersError}</p>}
            <Field label="Arguments schema" hint="Optional JSON schema for the tool's input arguments.">
              <Textarea rows={4} value={argsSchema} onChange={(e) => setArgsSchema(e.target.value)} placeholder={'{ "type": "object", "properties": {} }'} />
            </Field>
            {argsSchemaError && <p className="-mt-2 text-xs text-critical">{argsSchemaError}</p>}
          </div>
        )}

        {kind === "Sub-agent" && (
          <div className="space-y-4">
            <Field label="Agent">
              <Select value={agent} onChange={(e) => setAgent(e.target.value)}>
                {agents.map((a) => <option key={a.id} value={a.name}>{a.name}</option>)}
              </Select>
            </Field>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Max depth" hint="Delegation depth cap.">
                <Input type="number" min={1} value={maxDepth} onChange={(e) => setMaxDepth(e.target.value)} />
              </Field>
              <Field label="Max fan-out" hint="Concurrent children per turn.">
                <Input type="number" min={1} value={maxFanout} onChange={(e) => setMaxFanout(e.target.value)} />
              </Field>
            </div>
          </div>
        )}

        {kind === "Built-in" && (
          <Field label="Catalog">
            <Select value={catalog} onChange={(e) => setCatalog(e.target.value)}>
              {BUILTIN_CATALOG.map((c) => <option key={c} value={c}>{c}</option>)}
            </Select>
          </Field>
        )}
      </div>
    </Dialog>
  );
}
