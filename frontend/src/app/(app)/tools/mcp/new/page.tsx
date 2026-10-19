"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, Check, Plug, Search, ShieldCheck } from "lucide-react";
import {
  Card, CardHeader, CardTitle, CardContent, Button, Field, Input, Select, Badge, StatusDot,
} from "@/components/ui";
import { Stepper } from "@/components/stepper";
import { addMcpServer, toast, genId, nowIso } from "@/lib/store";
import { MCP_AUTH_METHODS, MCP_AUTHS, mcpAuthLabel, type McpAuth } from "@/lib/mcp";
import { finalizeMcpCredentials, mcpCredentialsComplete } from "@/lib/mcp-credentials";
import { riskTone } from "@/lib/tools";
import type { McpTool } from "@/lib/data";

const STEPS = ["Connection", "Auth", "Discover", "Select", "Review"];

type Discovered = { name: string; description: string; riskTier: McpTool["riskTier"] };
const SAMPLE_TOOLS: Discovered[] = [
  { name: "search_issues", description: "Search issues with a query", riskTier: "read" },
  { name: "get_issue", description: "Fetch a single issue by key", riskTier: "read" },
  { name: "create_issue", description: "Create a new issue", riskTier: "write" },
  { name: "add_comment", description: "Comment on an issue", riskTier: "write" },
];

/* @backend integration points (this page):
 *  - @backend POST /v1/mcp-servers - connect server, triggers server-side tool discovery [via addMcpServer]
 *  - @backend GET /v1/mcp-servers/:id - discovered tool catalog after connect [via useMcpServer]
 *  - @backend auth methods + credential field specs come from GET /v1/mcp-auth-methods [via MCP_AUTH_METHODS]
 * See src/lib/store.ts markers + docs/backend-integration-map.md for the authoritative contracts.
 */
export default function NewMcpServerPage() {
  const router = useRouter();
  const [step, setStep] = React.useState(0);

  // Connection
  const [name, setName] = React.useState("");
  const [transport, setTransport] = React.useState<"http" | "stdio">("http");
  const [endpoint, setEndpoint] = React.useState("");
  const [command, setCommand] = React.useState("");

  // Auth — the raw secret(s) captured per credential field, masked into CredentialRefs on finish.
  const [auth, setAuth] = React.useState<McpAuth>("none");
  const [creds, setCreds] = React.useState<Record<string, string>>({});

  // Discover
  const [discovered, setDiscovered] = React.useState<Discovered[] | null>(null);

  // Select
  const [enabled, setEnabled] = React.useState<Record<string, boolean>>({});
  const [egress, setEgress] = React.useState("");
  const [approval, setApproval] = React.useState<"auto" | "ask">("auto");

  const selected = (discovered ?? []).filter((t) => enabled[t.name]);
  const selectedCount = selected.length;
  const credFields = MCP_AUTH_METHODS[auth].credentialFields;
  const authComplete = mcpCredentialsComplete(auth, creds);

  function changeAuth(next: McpAuth) {
    setAuth(next);
    setCreds({}); // clear captured secrets when the method changes
  }

  function discover() {
    setDiscovered(SAMPLE_TOOLS);
    setEnabled(Object.fromEntries(SAMPLE_TOOLS.map((t, i) => [t.name, i < 2])));
  }

  const connectionOk =
    name.trim().length > 0 &&
    (transport === "http" ? endpoint.trim().length > 0 : command.trim().length > 0);

  const canNext = (() => {
    if (step === 0) return connectionOk;
    if (step === 1) return authComplete; // require the credential when auth !== none
    if (step === 2) return !!discovered && discovered.length > 0; // discovery done before Select
    return true;
  })();

  function finish() {
    const egressList = egress.split(",").map((s) => s.trim()).filter(Boolean);
    addMcpServer({
      id: genId("mcp"),
      name: name.trim(),
      transport,
      endpoint: (transport === "http" ? endpoint : command).trim(),
      status: "connected",
      auth,
      toolCount: selectedCount,
      lastDiscovery: nowIso(),
      boundAgents: 0,
      tools: selected.map((t) => ({ name: t.name, description: t.description, enabled: true, riskTier: t.riskTier })),
      credentials: finalizeMcpCredentials(auth, creds),
      egress: egressList,
      approval,
    });
    router.push("/tools");
  }

  return (
    <div className="space-y-6">
      <div>
        <Button href="/tools" variant="ghost" size="sm"><ArrowLeft className="size-4" /> Back to Tools & MCP</Button>
        <h1 className="mt-2 text-xl font-semibold tracking-tight text-ink">Add MCP server</h1>
        <p className="mt-1 text-sm text-muted">Register a server, discover its tools, and pick an allow-list to expose to agents.</p>
      </div>

      <Card>
        <CardHeader><CardTitle className="flex items-center gap-2"><Plug className="size-4 text-muted" /> New connection</CardTitle></CardHeader>
        <CardContent className="space-y-6">
          <div className="overflow-x-auto pb-1"><Stepper steps={STEPS} current={step} /></div>

          {step === 0 && (
            <div className="space-y-4">
              <Field label="Name">
                <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Jira" />
              </Field>
              <Field label="Transport">
                <Select value={transport} onChange={(e) => setTransport(e.target.value as "http" | "stdio")}>
                  <option value="http">http</option>
                  <option value="stdio">stdio</option>
                </Select>
              </Field>
              {transport === "http" ? (
                <Field label="Endpoint" hint="HTTPS URL of the MCP server.">
                  <Input value={endpoint} onChange={(e) => setEndpoint(e.target.value)} placeholder="https://mcp.internal/jira" />
                </Field>
              ) : (
                <Field label="Command" hint="Executable and arguments to launch the server.">
                  <Input value={command} onChange={(e) => setCommand(e.target.value)} placeholder="jira-mcp --readonly" />
                </Field>
              )}
            </div>
          )}

          {step === 1 && (
            <div className="space-y-4">
              <Field label="Authentication">
                <Select value={auth} onChange={(e) => changeAuth(e.target.value as McpAuth)}>
                  {MCP_AUTHS.map((a) => <option key={a} value={a}>{mcpAuthLabel(a)}</option>)}
                </Select>
              </Field>
              {credFields.map((f) => (
                <Field key={f.key} label={f.label} hint={f.hint}>
                  <Input
                    type={f.type === "password" ? "password" : "text"}
                    value={creds[f.key] ?? ""}
                    onChange={(e) => setCreds((s) => ({ ...s, [f.key]: e.target.value }))}
                    placeholder={f.placeholder}
                  />
                </Field>
              ))}
              {MCP_AUTH_METHODS[auth].credentialHint && (
                <p className="text-xs text-muted">{MCP_AUTH_METHODS[auth].credentialHint}</p>
              )}
              <div>
                <Button variant="outline" size="sm" disabled={!authComplete} onClick={() => toast("Connection ok")}>
                  <ShieldCheck className="size-3.5" /> Test connection
                </Button>
              </div>
            </div>
          )}

          {step === 2 && (
            <div className="space-y-4">
              {!discovered ? (
                <>
                  <div className="rounded-lg border border-border bg-elevated p-3 text-xs text-muted">
                    Discovery lists the server&apos;s tools and caches their schemas. Nothing is exposed to agents yet.
                  </div>
                  <Button variant="primary" size="sm" onClick={discover}><Search className="size-4" /> Discover tools</Button>
                </>
              ) : (
                <div className="space-y-1.5">
                  <p className="text-xs text-muted">Found {discovered.length} tools.</p>
                  {discovered.map((t) => (
                    <div key={t.name} className="rounded-md border border-border bg-elevated px-3 py-2">
                      <div className="font-mono text-xs text-ink-2">{t.name}</div>
                      <div className="text-xs text-muted">{t.description}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {step === 3 && (
            <div className="space-y-5">
              <div className="space-y-1.5">
                {(discovered ?? []).map((t) => (
                  <label key={t.name} className="flex cursor-pointer items-center justify-between gap-3 rounded-md border border-border bg-elevated px-3 py-2">
                    <span>
                      <span className="font-mono text-xs text-ink-2">{t.name}</span>
                      <span className="block text-xs text-muted">{t.description}</span>
                    </span>
                    <span className="flex items-center gap-2">
                      {t.riskTier && <Badge tone={riskTone(t.riskTier)}>{t.riskTier}</Badge>}
                      <input
                        type="checkbox"
                        className="size-4 accent-accent"
                        checked={!!enabled[t.name]}
                        onChange={(e) => setEnabled((s) => ({ ...s, [t.name]: e.target.checked }))}
                      />
                    </span>
                  </label>
                ))}
              </div>
              <Field label="Egress allow-list" hint="Comma-separated domains this server may reach (deny-by-default).">
                <Input value={egress} onChange={(e) => setEgress(e.target.value)} placeholder="mcp.internal, api.atlassian.com" />
              </Field>
              <Field label="Approval policy">
                <Select value={approval} onChange={(e) => setApproval(e.target.value as "auto" | "ask")}>
                  <option value="auto">Auto (run without prompting)</option>
                  <option value="ask">Ask (require approval per call)</option>
                </Select>
              </Field>
            </div>
          )}

          {step === 4 && (
            <div className="space-y-3 text-sm">
              <Row k="Name" v={name || "Untitled server"} />
              <Row k="Transport" v={<Badge tone={transport === "http" ? "accent" : "violet"}>{transport}</Badge>} />
              <Row k={transport === "http" ? "Endpoint" : "Command"} v={<span className="font-mono text-xs">{(transport === "http" ? endpoint : command) || "-"}</span>} />
              <Row k="Authentication" v={mcpAuthLabel(auth)} />
              <Row k="Credentials" v={auth === "none" ? "none" : `${Object.keys(finalizeMcpCredentials(auth, creds)).length} vaulted`} />
              <Row k="Egress allow-list" v={egress.trim() || "none"} />
              <Row k="Approval policy" v={approval === "auto" ? "Auto" : "Ask"} />
              <Row k="Selected tools" v={<span className="inline-flex items-center gap-1.5"><StatusDot tone="good" />{selectedCount} of {(discovered ?? []).length}</span>} />
            </div>
          )}

          <div className="flex items-center justify-between border-t border-border pt-4">
            <Button
              variant="ghost"
              disabled={step === 0}
              onClick={() => setStep((s) => Math.max(0, s - 1))}
            >
              <ArrowLeft className="size-4" /> Back
            </Button>
            {step < STEPS.length - 1 ? (
              <Button variant="primary" disabled={!canNext} onClick={() => setStep((s) => Math.min(STEPS.length - 1, s + 1))}>
                Next
              </Button>
            ) : (
              <Button variant="primary" onClick={finish}><Check className="size-4" /> Finish</Button>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-border/70 pb-2.5 last:border-0 last:pb-0">
      <span className="text-muted">{k}</span>
      <span className="text-right font-medium text-ink-2">{v}</span>
    </div>
  );
}
