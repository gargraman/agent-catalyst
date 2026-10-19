"use client";

/* Shared Playground: chat against an agent with a run-context (my session vs a provided
   caller token) and a live view of which bound tools/knowledge the caller is granted vs
   denied. Used by the top-level /playground route. Mock send; real invoke is POST
   /v1/agents/:id/invoke (streaming). */

import * as React from "react";
import { CircleCheck, Database, KeyRound, Lock, Send, TriangleAlert, UserRound, Wrench } from "lucide-react";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, Field, Input } from "@/components/ui";
import { Segmented } from "@/components/tabs";
import { useAuth, useWorkspace } from "@/lib/auth";
import { ORG } from "@/lib/org";
import { DEFAULT_WORKSPACE_ID } from "@/lib/iam";
import { resolveRun, statusLabel as runStatusLabel, type RunCtx } from "@/lib/runtime-access";
import { useMcpServers } from "@/lib/store";
import { getKb, type Agent } from "@/lib/data";
import { usd } from "@/lib/format";

type Msg = { role: "user" | "assistant"; text: string };
const kbName = (id: string) => getKb(id)?.name ?? id;

function Section({ title, desc, children }: { title: string; desc?: string; children: React.ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <div><CardTitle>{title}</CardTitle>{desc && <p className="mt-0.5 text-xs text-muted">{desc}</p>}</div>
      </CardHeader>
      <CardContent className="pt-4">{children}</CardContent>
    </Card>
  );
}

export function Playground({ agent, version }: { agent: Agent; version?: string }) {
  const { role } = useAuth();
  const { workspace, teams } = useWorkspace();
  const [messages, setMessages] = React.useState<Msg[]>([
    { role: "user", text: "Give me a one-paragraph summary of the latest changes, grounded in the knowledge base." },
    { role: "assistant", text: "Based on the retrieved context, the latest release focuses on performance and reliability: faster cold starts, clearer error states, and a handful of fixes to the ingestion pipeline. I cited the three source pages the retrieval returned. (Sample response in the playground.)" },
  ]);
  const [input, setInput] = React.useState("");
  const [runMode, setRunMode] = React.useState<"session" | "token">("session");
  const [token, setToken] = React.useState("");

  const mcpServers = useMcpServers();
  const session = { tenant: workspace?.id ?? agent.workspaceId ?? DEFAULT_WORKSPACE_ID, role, teams };
  const ctx: RunCtx = runMode === "session" ? { mode: "session" } : { mode: "token", token };
  // resolveRun is pure and cheap; compute it per render (no memo deps to keep in sync).
  const res = resolveRun(ctx, agent, session, mcpServers);
  const blocked = res.status !== "ok";

  function send() {
    if (!input.trim()) return;
    const reply = blocked
      ? `Run blocked: ${runStatusLabel[res.status]}. The caller token does not satisfy this agent's access requirements, so nothing was executed.`
      : res.deniedTools.length || res.deniedKbs.length
        ? `Answered with the resources the caller token grants. Skipped for access: ${[...res.deniedTools, ...res.deniedKbs].map((d) => d.name).join(", ")}.`
        : "Grounded in 3 sources and 1 tool call. (Sample response in the playground.)";
    setMessages((m) => [...m, { role: "user", text: input }, { role: "assistant", text: reply }]);
    setInput("");
  }

  const subjectIcon = res.subjectType === "service" ? <KeyRound className="size-3" /> : <UserRound className="size-3" />;

  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <Card className="flex min-h-[520px] flex-col lg:col-span-2">
        <CardHeader><CardTitle>Playground</CardTitle><Badge tone="neutral">{version ?? agent.activeVersion} · {agent.model}</Badge></CardHeader>
        <CardContent className="flex-1 space-y-4 overflow-y-auto pt-2">
          {blocked && (
            <div className="flex items-start gap-2 rounded-lg border border-critical/40 bg-critical/10 px-3 py-2 text-xs text-critical">
              <TriangleAlert className="mt-0.5 size-3.5 shrink-0" />
              <span><span className="font-medium">{runStatusLabel[res.status]}.</span> With this token the run would be rejected before any tool or knowledge call.</span>
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-[80%] rounded-xl px-4 py-2.5 text-sm leading-relaxed ${m.role === "user" ? "bg-accent text-accent-ink" : "border border-border bg-elevated text-ink-2"}`}>
                {m.text}
                {m.role === "assistant" && <div className="mt-2 flex gap-3 text-[11px] text-muted"><span>1.8s</span><span>9,120 tokens</span><span>{usd(0.034)}</span><span className="text-good">guardrails passed</span></div>}
              </div>
            </div>
          ))}
        </CardContent>
        <div className="border-t border-border p-3">
          <div className="flex items-center gap-2">
            <Input value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()} placeholder="Send a test message" />
            <Button variant="primary" onClick={send}><Send className="size-4" /></Button>
          </div>
        </div>
      </Card>

      <div className="space-y-6">
        <Section title="Run context" desc="Who the agent runs as. Resource access is governed by the running user's token.">
          <div className="space-y-3">
            <Segmented
              options={[{ key: "session", label: "My session" }, { key: "token", label: "Provide token" }]}
              value={runMode}
              onChange={(v) => setRunMode(v as "session" | "token")}
            />
            {runMode === "token" && (
              <Field label="Caller token" hint="Try: user-soc-full, svc-grc, publisher-partial, expired, revoked">
                <Input value={token} onChange={(e) => setToken(e.target.value)} placeholder={`Paste a ${ORG.idpName} token`} />
              </Field>
            )}
            <div className="grid grid-cols-2 gap-2 text-xs">
              <Chip label="Tenant" value={res.tenant} />
              <Chip label="Subject" value={<span className="inline-flex items-center gap-1">{subjectIcon}{res.subjectType === "service" ? "Service" : "User"}</span>} />
              <Chip label="Role" value={res.role} />
              <Chip label="Scopes" value={res.scopes.includes("*") ? "all" : res.scopes.join(", ")} />
              <Chip label="Expiry" value={res.expiry} />
              <Chip label="Status" value={<span className={blocked ? "text-critical" : "text-good"}>{runStatusLabel[res.status]}</span>} />
            </div>
          </div>
        </Section>

        <Section title="Bound resources" desc="Granted vs denied for this caller.">
          <div className="space-y-3">
            <ResourceGroup
              icon={<Wrench className="size-3.5 text-s3" />}
              title="Tools"
              granted={res.grantedTools}
              denied={res.deniedTools}
              empty="No tools bound"
              blocked={blocked}
            />
            <ResourceGroup
              icon={<Database className="size-3.5 text-s1" />}
              title="Knowledge"
              granted={res.grantedKbs.map(kbName)}
              denied={res.deniedKbs.map((d) => ({ name: kbName(d.name), reason: d.reason }))}
              empty="No knowledge bound"
              blocked={blocked}
            />
          </div>
        </Section>
      </div>
    </div>
  );
}

function Chip({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-border bg-elevated px-2.5 py-1.5">
      <p className="text-[10px] uppercase tracking-wide text-muted">{label}</p>
      <p className="truncate font-medium text-ink">{value}</p>
    </div>
  );
}

function ResourceGroup({
  icon, title, granted, denied, empty, blocked,
}: {
  icon: React.ReactNode;
  title: string;
  granted: string[];
  denied: { name: string; reason: string }[];
  empty: string;
  blocked: boolean;
}) {
  if (granted.length === 0 && denied.length === 0) {
    return <p className="text-xs text-muted">{empty}</p>;
  }
  return (
    <div className="space-y-1.5">
      <p className="flex items-center gap-1.5 text-xs font-medium text-ink-2">{icon}{title}</p>
      <ul className="space-y-1">
        {!blocked && granted.map((name) => (
          <li key={name} className="flex items-center gap-2 text-xs text-ink-2">
            <CircleCheck className="size-3.5 shrink-0 text-good" /> {name}
          </li>
        ))}
        {(blocked ? [...granted.map((name) => ({ name, reason: "run blocked" })), ...denied] : denied).map((d) => (
          <li key={d.name} className="flex items-center gap-2 text-xs text-muted line-through decoration-muted/50" title={d.reason}>
            <Lock className="size-3.5 shrink-0 no-underline" /> <span className="no-underline">{d.name}</span>
            <span className="ml-auto text-[10px] normal-case no-underline">{d.reason}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
