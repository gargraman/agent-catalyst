"use client";

import * as React from "react";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, Boxes, Lock, Pause, Play, Plug, RefreshCw, Trash2, Users } from "lucide-react";
import {
  Badge, Button, Card, CardContent, CardHeader, CardTitle, CardDescription,
  EmptyState, StatusDot, Table, TD, TH, THead, TR,
} from "@/components/ui";
import { StatTile } from "@/components/stat-tile";
import { ConfirmDialog } from "@/components/dialog";
import { AccessPolicyEditor } from "@/components/access-policy-editor";
import { GateButton } from "@/components/gate";
import { useAuth, usePrincipal } from "@/lib/auth";
import { canManage, canUse } from "@/lib/access-policy";
import { MCP_AUTH_METHODS, mcpAuthLabel } from "@/lib/mcp";
import { PER_USER_OAUTH_APP_FIELDS } from "@/lib/oauth-app";
import { riskTone } from "@/lib/tools";
import {
  deleteMcpServer, reconnectMcpServer, setMcpToolEnabled, updateMcpServer, useMcpServer,
} from "@/lib/store";
import type { McpServer } from "@/lib/data";
import { relative } from "@/lib/format";

const statusTone = (s: McpServer["status"]) => (s === "connected" ? "good" : s === "error" ? "critical" : "neutral");

/* @backend integration points (this page):
 *  - @backend GET /v1/mcp-servers/:id - server detail (discovered tools, masked creds, status) [via useMcpServer]
 *  - @backend PUT /v1/mcp-servers/:id - edit egress/approval + pause/resume [via updateMcpServer]
 *  - @backend POST /v1/mcp-servers/:id/discover - reconnect + re-discover [via reconnectMcpServer]
 *  - @backend PATCH /v1/mcp-servers/:id/tools/:tool - toggle the exposed allow-list [via setMcpToolEnabled]
 *  - @backend DELETE /v1/mcp-servers/:id - disconnect + revoke vaulted creds [via deleteMcpServer]
 *  - @backend PUT /v1/mcp/:id/access-policy - per-resource access edits [via setAccessPolicy]
 * See docs/backend-integration-map.md for the authoritative contracts.
 */
export default function McpServerDetailPage() {
  const params = useParams();
  const router = useRouter();
  const id = String(params.id);
  const server = useMcpServer(id);
  const { can } = useAuth();
  const principal = usePrincipal();
  const [confirmOpen, setConfirmOpen] = React.useState(false);

  if (!server) {
    return (
      <div className="space-y-6">
        <Button variant="ghost" size="sm" href="/tools" className="-ml-2"><ArrowLeft className="size-4" /> Tools & MCP</Button>
        <EmptyState icon={<Plug className="size-5" />} title="MCP server not found" hint="It may have been disconnected." />
      </div>
    );
  }
  if (!canUse(server.accessPolicy, principal)) {
    return (
      <div className="space-y-6">
        <Button variant="ghost" size="sm" href="/tools" className="-ml-2"><ArrowLeft className="size-4" /> Tools & MCP</Button>
        <EmptyState icon={<Lock className="size-5" />} title="No access" hint="This MCP server is restricted by its access policy." />
      </div>
    );
  }

  const manage = can("mcp.manage") && canManage(server.accessPolicy, principal);
  const manageTitle = manage ? undefined : "Restricted by this server's access policy";
  const credFields = MCP_AUTH_METHODS[server.auth]?.credentialFields ?? [];
  const perUser = server.authMode === "per_user";
  const credDisplayFields = perUser ? PER_USER_OAUTH_APP_FIELDS : credFields;
  const enabledCount = server.tools.filter((t) => t.enabled).length;

  function remove() {
    deleteMcpServer(id);
    router.push("/tools");
  }

  return (
    <div className="space-y-6">
      <div>
        <Button variant="ghost" size="sm" href="/tools" className="-ml-2 mb-3"><ArrowLeft className="size-4" /> Tools & MCP</Button>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <h1 className="flex items-center gap-2 text-xl font-semibold text-ink"><Plug className="size-5 text-muted" />{server.name}</h1>
              <Badge tone={statusTone(server.status)}><StatusDot tone={statusTone(server.status)} />{server.status}</Badge>
              <Badge tone={server.transport === "http" ? "accent" : "violet"}>{server.transport}</Badge>
            </div>
            <p className="mt-1 font-mono text-sm text-muted">{server.endpoint}</p>
          </div>
          <div className="flex items-center gap-2">
            <GateButton permission="mcp.manage" disabled={!manage} title={manageTitle} variant="secondary" size="sm" onClick={() => reconnectMcpServer(id)}>
              <RefreshCw className="size-4" /> Rediscover
            </GateButton>
            {(server.status === "connected" || server.status === "disconnected") && (
              <GateButton
                permission="mcp.manage" disabled={!manage} title={manageTitle} variant="outline" size="sm"
                onClick={() => updateMcpServer(id, { status: server.status === "connected" ? "disconnected" : "connected" })}
              >
                {server.status === "connected" ? <><Pause className="size-4" /> Pause</> : <><Play className="size-4" /> Resume</>}
              </GateButton>
            )}
            <GateButton permission="mcp.manage" disabled={!manage} title={manageTitle} variant="outline" size="sm" onClick={() => setConfirmOpen(true)}>
              <Trash2 className="size-4" /> Disconnect
            </GateButton>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatTile label="Discovered tools" value={String(server.toolCount)} icon={<Boxes className="size-4" />} />
        <StatTile label="Exposed (enabled)" value={String(enabledCount)} icon={<Plug className="size-4" />} />
        <StatTile label="Bound agents" value={String(server.boundAgents)} icon={<Users className="size-4" />} />
        <StatTile label="Last discovery" value={relative(server.lastDiscovery)} icon={<RefreshCw className="size-4" />} />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <Card>
            <CardHeader><CardTitle>Overview</CardTitle></CardHeader>
            <CardContent className="space-y-0 pt-0">
              <Row label="Transport" value={server.transport} />
              <Row label={server.transport === "http" ? "Endpoint" : "Command"} value={<span className="font-mono text-xs">{server.endpoint}</span>} />
              <Row label="Authentication" value={mcpAuthLabel(server.auth)} />
              <Row label="Credential mode" value={perUser ? "Per-user — each caller connects their own account" : "Shared service credential"} />
              {perUser && server.oauthScopes ? <Row label="OAuth scopes" value={<span className="font-mono text-xs">{server.oauthScopes}</span>} /> : null}
              <Row label="Egress allow-list" value={server.egress?.length ? server.egress.join(", ") : "none"} />
              <Row label="Approval policy" value={server.approval === "ask" ? "Ask (require approval per call)" : "Auto"} />
              <Row label="Last discovery" value={relative(server.lastDiscovery)} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <div><CardTitle>Discovered tools</CardTitle><CardDescription>Toggle which tools are exposed to agents (the allow-list).</CardDescription></div>
            </CardHeader>
            <CardContent className="pt-0">
              {server.tools.length === 0 ? (
                <EmptyState icon={<Boxes className="size-5" />} title="No tools discovered" hint="Reconnect to re-run discovery against the server." />
              ) : (
                <div className="divide-y divide-border">
                  {server.tools.map((t) => (
                    <label key={t.name} className="flex cursor-pointer items-center justify-between gap-3 py-3">
                      <span className="min-w-0">
                        <span className="block font-mono text-sm text-ink">{t.name}</span>
                        <span className="block truncate text-xs text-muted">{t.description}</span>
                      </span>
                      <span className="flex shrink-0 items-center gap-2">
                        {t.riskTier && <Badge tone={riskTone(t.riskTier)}>{t.riskTier}</Badge>}
                        <Badge tone={t.enabled ? "good" : "neutral"}>{t.enabled ? "enabled" : "disabled"}</Badge>
                        <input
                          type="checkbox"
                          className="size-4 accent-accent disabled:opacity-50"
                          checked={t.enabled}
                          disabled={!manage}
                          onChange={(e) => setMcpToolEnabled(id, t.name, e.target.checked)}
                        />
                      </span>
                    </label>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader><CardTitle>Credentials</CardTitle></CardHeader>
            <CardContent className="pt-0">
              {!perUser && server.auth === "none" ? (
                <p className="text-sm text-muted">No authentication — this server requires no vaulted credential.</p>
              ) : (
                <Table>
                  <THead><tr><TH>{perUser ? "OAuth app credential" : "Secret"}</TH><TH>Masked reference</TH></tr></THead>
                  <tbody>
                    {credDisplayFields.map((f) => {
                      const ref = server.credentials?.[f.key];
                      return (
                        <TR key={f.key}>
                          <TD className="text-ink-2">{f.label}</TD>
                          <TD className="font-mono text-xs text-muted">{ref?.masked || "— not set —"}</TD>
                        </TR>
                      );
                    })}
                  </tbody>
                </Table>
              )}
              {perUser && (
                <p className="mt-3 text-xs text-ink-2">
                  Per-user mode: these are the shared OAuth <strong>app</strong> credentials. Each caller connects
                  their own account at first use; per-user tokens are stored per connection, not here.
                </p>
              )}
              <p className="mt-3 flex items-center gap-1.5 text-xs text-muted">
                <Lock className="size-3.5" /> Raw secrets live in the vault; only masked references are kept.
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <div><CardTitle>Access</CardTitle><CardDescription>Who may use and manage this server.</CardDescription></div>
            </CardHeader>
            <CardContent className="pt-0">
              <AccessPolicyEditor kind="mcp" id={server.id} policy={server.accessPolicy} readOnly={!manage} />
            </CardContent>
          </Card>
        </div>
      </div>

      <ConfirmDialog
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        onConfirm={remove}
        title="Disconnect MCP server"
        confirmLabel="Disconnect"
        tone="danger"
        body={
          <>
            Disconnect <span className="font-medium text-ink">{server.name}</span> and revoke its vaulted credentials?
            {server.boundAgents > 0 && (
              <span className="mt-2 block text-critical">
                {server.boundAgents} {server.boundAgents === 1 ? "agent" : "agents"} still bind tools from this server — those bindings will stop resolving.
              </span>
            )}
          </>
        }
      />
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-border/70 py-2.5 last:border-0">
      <span className="text-sm text-muted">{label}</span>
      <span className="text-right text-sm font-medium text-ink-2">{value}</span>
    </div>
  );
}
