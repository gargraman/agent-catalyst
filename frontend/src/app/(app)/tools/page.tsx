"use client";

import * as React from "react";
import { Boxes, Plug, Plus, RefreshCw, ShieldCheck, Wrench } from "lucide-react";
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
import { useMcpServers, useTools, useAgents, addTool, genId, toast } from "@/lib/store";
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
 *  - @backend GET /v1/mcp-servers - connected MCP servers list [via useMcpServers]
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
                  <CardTitle className="flex items-center gap-2"><Plug className="size-4 text-muted" />{s.name}</CardTitle>
                  <CardDescription className="font-mono">{s.endpoint}</CardDescription>
                </div>
                <Badge tone={s.transport === "http" ? "accent" : "violet"}>{s.transport}</Badge>
              </CardHeader>
              <CardContent className="space-y-3 pt-0">
                <div className="flex items-center justify-between text-xs">
                  <span className="inline-flex items-center gap-1.5 capitalize text-ink-2"><StatusDot tone={tone} />{s.status}</span>
                  <span className="text-muted">{s.auth} · {s.toolCount} tools · {s.boundAgents} agents</span>
                </div>
                {s.status === "error" ? (
                  <div className="space-y-2 rounded-lg border border-critical/30 bg-critical/10 p-3">
                    <p className="text-xs text-ink-2">Discovery failed: token expired. Reconnect to refresh the tool catalog.</p>
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
                    {s.toolCount > 3 && <button className="text-xs text-accent hover:underline">View all {s.toolCount} tools</button>}
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
        onConfirm={() => toast("Reconnected and re-discovered tools")}
        title="Reconnect MCP server"
        confirmLabel="Reconnect"
        tone="primary"
        body={<>Re-authenticate <span className="font-medium text-ink">{reconnect?.name}</span> and re-discover its tool catalog?</>}
      />
    </div>
  );
}

function RepoTab({ tools }: { tools: RepoTool[] }) {
  const [open, setOpen] = React.useState(false);
  const { can } = useAuth();
  const admin = can("access.manage");
  const [openAccess, setOpenAccess] = React.useState<string | null>(null);

  return (
    <Card>
      <CardHeader>
        <div><CardTitle>Tool repository</CardTitle><CardDescription>Reusable tools that any agent version can bind.</CardDescription></div>
        <GateButton permission="tool.manage" variant="outline" size="sm" onClick={() => setOpen(true)}><Plus className="size-3.5" /> New tool</GateButton>
      </CardHeader>
      <CardContent className="pt-0">
        <Table>
          <THead><TR><TH>Tool</TH><TH>Kind</TH><TH>Description</TH><TH>Access</TH><TH className="text-right">Used by</TH><TH></TH></TR></THead>
          <tbody>
            {tools.map((t) => (
              <React.Fragment key={t.name}>
                <TR>
                  <TD><span className="font-mono text-xs text-ink-2">{t.name}</span></TD>
                  <TD><Badge tone={t.kind === "HTTP" ? "accent" : t.kind === "Sub-agent" ? "violet" : "neutral"}>{t.kind}</Badge></TD>
                  <TD className="text-ink-2">
                    {t.description}
                    {t.httpConfig && <span className="mt-0.5 block font-mono text-xs text-muted">{t.httpConfig.method} {t.httpConfig.url}</span>}
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
                      <GateButton permission="tool.manage" variant="ghost" size="sm" onClick={() => toast("Edit tool (coming soon)", "neutral")}>Edit</GateButton>
                    </div>
                  </TD>
                </TR>
                {admin && openAccess === t.name && (
                  <TR>
                    <TD colSpan={6}>
                      <AccessPolicyEditor kind="tool" id={t.name} policy={t.accessPolicy} readOnly={false} />
                    </TD>
                  </TR>
                )}
              </React.Fragment>
            ))}
          </tbody>
        </Table>
      </CardContent>
      <NewToolDialog open={open} onClose={() => setOpen(false)} />
    </Card>
  );
}

const BUILTIN_CATALOG = ["code-exec-python", "web-search"];

const AUTH_OPTIONS: { value: HttpToolConfig["auth"]; label: string }[] = [
  { value: "none", label: "None" },
  { value: "bearer", label: "Bearer token" },
  { value: "api_key_header", label: "API key header" },
  { value: "basic", label: "Basic auth" },
];

function NewToolDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const agents = useAgents();
  const tools = useTools();
  const [kind, setKind] = React.useState<RepoTool["kind"]>("HTTP");
  const [name, setName] = React.useState("");
  const [description, setDescription] = React.useState("");
  const [method, setMethod] = React.useState<HttpToolConfig["method"]>("GET");
  const [url, setUrl] = React.useState("");
  const [auth, setAuth] = React.useState<HttpToolConfig["auth"]>("none");
  const [secret, setSecret] = React.useState("");
  const [headers, setHeaders] = React.useState("");
  const [argsSchema, setArgsSchema] = React.useState("");
  const [agent, setAgent] = React.useState(agents[0]?.name ?? "");
  const [catalog, setCatalog] = React.useState(BUILTIN_CATALOG[0]);

  function reset() {
    setKind("HTTP"); setName(""); setDescription("");
    setMethod("GET"); setUrl(""); setAuth("none"); setSecret(""); setHeaders(""); setArgsSchema("");
    setAgent(agents[0]?.name ?? ""); setCatalog(BUILTIN_CATALOG[0]);
  }
  function close() { reset(); onClose(); }

  const trimmedName = name.trim();
  const nameTaken = tools.some((t) => t.name === trimmedName);
  const valid =
    trimmedName.length > 0 && !nameTaken && description.trim().length > 0 &&
    (kind !== "HTTP" || url.trim().length > 0) &&
    (kind !== "HTTP" || auth === "none" || secret.trim().length > 0);

  function submit() {
    if (!valid) return;
    const base = { name: trimmedName, kind, description: description.trim(), usedBy: 0 };
    if (kind === "HTTP") {
      const httpConfig: HttpToolConfig = {
        method,
        url: url.trim(),
        auth,
        ...(auth !== "none" && secret.trim()
          ? { authRef: { masked: maskSecret(secret), vaultPath: vaultRef("tool", genId("tool")) } }
          : {}),
        ...(headers.trim() ? { headers: headers.trim() } : {}),
        ...(argsSchema.trim() ? { argsSchema: argsSchema.trim() } : {}),
      };
      addTool({ ...base, httpConfig });
    } else {
      addTool(base);
    }
    close();
  }

  return (
    <Dialog
      open={open}
      onClose={close}
      title="New tool"
      description="Add a reusable tool to the repository."
      footer={
        <>
          <Button variant="ghost" onClick={close}>Cancel</Button>
          <Button variant="primary" disabled={!valid} onClick={submit}><Plus className="size-4" /> Add tool</Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Kind">
          <Select value={kind} onChange={(e) => setKind(e.target.value as RepoTool["kind"])}>
            <option value="HTTP">HTTP</option>
            <option value="Built-in">Built-in</option>
            <option value="Sub-agent">Sub-agent</option>
          </Select>
        </Field>
        <Field label="Name">
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. http-enrich-ip" />
        </Field>
        {nameTaken && <p className="-mt-2 text-xs text-critical">A tool named &quot;{trimmedName}&quot; already exists.</p>}
        <Field label="Description">
          <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What does this tool do?" />
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
                  <Field label="Credential" hint="Stored in the vault; only a masked reference is kept.">
                    <Input type="password" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder="Paste secret" />
                  </Field>
                </div>
              )}
            </div>
            <Field label="Headers" hint="Optional. One per line, e.g. X-Env: prod">
              <Textarea rows={2} value={headers} onChange={(e) => setHeaders(e.target.value)} placeholder="Accept: application/json" />
            </Field>
            <Field label="Arguments schema" hint="Optional JSON schema for the tool's input arguments.">
              <Textarea rows={4} value={argsSchema} onChange={(e) => setArgsSchema(e.target.value)} placeholder={'{ "type": "object", "properties": {} }'} />
            </Field>
          </div>
        )}

        {kind === "Sub-agent" && (
          <Field label="Agent">
            <Select value={agent} onChange={(e) => setAgent(e.target.value)}>
              {agents.map((a) => <option key={a.id} value={a.name}>{a.name}</option>)}
            </Select>
          </Field>
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
