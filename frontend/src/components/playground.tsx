"use client";

/* Shared Playground: chat against an agent with a run-context (my session vs a provided
   caller token) and a live view of which bound tools/knowledge the caller is granted vs
   denied. Used by the top-level /playground route.
   Live agents (feature 026 US2): each send streams POST /api/v1/agents/:id/invoke through a turn state machine
     streaming → complete | incomplete (interrupted / in-stream error — partial kept, Retry) | refused (pre-stream
     refusal — refusals.ts message, no partial).
   Mock agents keep the sample send (FR-021). */

import * as React from "react";
import { CircleCheck, Database, FileText, KeyRound, Lock, RotateCcw, Send, Sparkles, Square, TriangleAlert, UserRound, Wrench } from "lucide-react";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, Field, Input } from "@/components/ui";
import { Segmented } from "@/components/tabs";
import { useAuth, useWorkspace } from "@/lib/auth";
import { ORG } from "@/lib/org";
import { DEFAULT_WORKSPACE_ID } from "@/lib/iam";
import { resolveRun, statusLabel as runStatusLabel, type RunCtx } from "@/lib/runtime-access";
import { invokeAgentStream, isLiveAgent, useMcpServers, useSkills } from "@/lib/store";
import { getKb, resolveConfig, type Agent } from "@/lib/data";
import { usd } from "@/lib/format";
import { ApiError } from "@/lib/api/client";
import { refusalMessage, type Refusal } from "@/lib/api/refusals";
import type { Citation, InvokeFrame } from "@/lib/api/stream";

/** What a live turn runs (feature 026 D3) — `label` is what the console shows before `run_start` names it. */
export type RunTarget = { target: "active" | "draft" | "version"; versionId?: string; label: string };

type TurnState = "streaming" | "complete" | "incomplete" | "refused";
type Msg = {
  role: "user" | "assistant";
  text: string;
  /** Live turns only (mock turns have no state and keep the sample footer). */
  live?: {
    state: TurnState;
    input: string;                 // the user message this answers — Retry re-sends it
    ran?: string;                  // "draft" or the version label, from run_start
    refusal?: Refusal;
    citations?: Citation[];
    tokens?: number;
    costUsd?: number | null;
    latencyMs?: number | null;
    runId?: string;
  };
};
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

/** The io_contract field the chat message goes into: `message` when declared, else the first string input. */
function messageField(agent: Agent): string {
  const vars = resolveConfig(agent).inputVariables;
  return vars.find((v) => v.name === "message")?.name ?? vars.find((v) => v.type === "string")?.name ?? "message";
}

export function Playground({ agent, version, run }: { agent: Agent; version?: string; run?: RunTarget }) {
  const { role } = useAuth();
  const { workspace, teams, active } = useWorkspace();
  const suspended = !active; // inactive workspace = read-only freeze: endpoints suspended, no invoke
  const live = isLiveAgent(agent.id);
  const abortRef = React.useRef<AbortController | null>(null);
  React.useEffect(() => () => abortRef.current?.abort(), []);   // unmount (agent/version switch) cancels the stream
  const [messages, setMessages] = React.useState<Msg[]>(live ? [] : [
    { role: "user", text: "Give me a one-paragraph summary of the latest changes, grounded in the knowledge base." },
    { role: "assistant", text: "Based on the retrieved context, the latest release focuses on performance and reliability: faster cold starts, clearer error states, and a handful of fixes to the ingestion pipeline. I cited the three source pages the retrieval returned. (Sample response in the playground.)" },
  ]);
  const [input, setInput] = React.useState("");
  const [runMode, setRunMode] = React.useState<"session" | "token">("session");
  const [token, setToken] = React.useState("");

  const mcpServers = useMcpServers();
  const skills = useSkills();
  const session = { tenant: workspace?.id ?? agent.workspaceId ?? DEFAULT_WORKSPACE_ID, role, teams };
  const ctx: RunCtx = runMode === "session" ? { mode: "session" } : { mode: "token", token };
  // resolveRun is pure and cheap; compute it per render (no memo deps to keep in sync).
  const res = resolveRun(ctx, agent, session, mcpServers, skills);
  const blocked = res.status !== "ok";

  const streaming = messages.some((m) => m.live?.state === "streaming");

  /** Patch the assistant turn at `idx` (functional — frames arrive faster than renders). */
  const patchTurn = (idx: number, f: (m: Msg) => Msg) =>
    setMessages((ms) => ms.map((m, i) => (i === idx ? f(m) : m)));

  async function runLive(text: string, idx: number) {
    const ctl = new AbortController();
    abortRef.current = ctl;
    const onFrame = (fr: InvokeFrame) => patchTurn(idx, (m) => {
      const l = m.live!;
      switch (fr.event) {
        case "run_start": return { ...m, live: { ...l, ran: String((fr.data as { version?: unknown }).version ?? "") } };
        case "text_delta": return { ...m, text: m.text + String((fr.data as { text?: unknown }).text ?? "") };
        case "citations": return { ...m, live: { ...l, citations: (fr.data as { citations?: Citation[] }).citations ?? [] } };
        case "usage": {
          const u = fr.data as { input_tokens?: number; output_tokens?: number; cost_usd?: number | null };
          return { ...m, live: { ...l, tokens: (u.input_tokens ?? 0) + (u.output_tokens ?? 0), costUsd: u.cost_usd ?? l.costUsd } };
        }
        case "error": {
          const e = fr.data as { code?: string; message?: string };
          return { ...m, live: { ...l, refusal: refusalMessage({ status: 0, code: e.code ?? "error", message: e.message ?? "", detail: undefined, retryAfter: undefined }) } };
        }
        case "done": {
          const d = fr.data as { run_id?: string; status?: string; latency_ms?: number | null; cost_usd?: number | null };
          return { ...m, live: { ...l, runId: d.run_id, latencyMs: d.latency_ms ?? null, costUsd: d.cost_usd ?? l.costUsd,
                                 state: d.status === "success" && !l.refusal ? "complete" : "incomplete" } };
        }
        default: return m;
      }
    });
    try {
      const body = run?.target === "version"
        ? { inputs: { [messageField(agent)]: text }, target: "version" as const, version_id: run.versionId }
        : { inputs: { [messageField(agent)]: text }, target: run?.target ?? ("active" as const) };
      const res = await invokeAgentStream(agent.id, body, { signal: ctl.signal, onFrame });
      if (res.interrupted) {
        patchTurn(idx, (m) => ({ ...m, live: { ...m.live!, state: "incomplete",
          refusal: m.live!.refusal ?? { title: "Connection lost", body: "The answer stopped before it finished. The partial answer is kept.", retryable: true } } }));
      }
    } catch (e) {
      if ((e as { name?: string })?.name === "AbortError") {
        patchTurn(idx, (m) => ({ ...m, live: { ...m.live!, state: "incomplete", refusal: { title: "Stopped", body: "You stopped this answer.", retryable: true } } }));
      } else if (e instanceof ApiError) {
        // Pre-stream refusal: nothing ran, so there is no partial to keep.
        patchTurn(idx, (m) => ({ ...m, text: "", live: { ...m.live!, state: "refused", refusal: refusalMessage(e) } }));
      } else {
        patchTurn(idx, (m) => ({ ...m, live: { ...m.live!, state: "incomplete",
          refusal: { title: "Couldn't reach the server", body: "The request failed before an answer arrived.", retryable: true } } }));
      }
    } finally {
      if (abortRef.current === ctl) abortRef.current = null;
    }
  }

  function sendLive(text: string) {
    // Sends are serialized (blocked while a turn streams), so the rendered list is current: the new assistant turn
    // lands at messages.length + 1. Turns are only ever appended, so the index stays valid for the whole stream.
    const idx = messages.length + 1;
    setMessages((ms) => [...ms, { role: "user", text }, { role: "assistant", text: "", live: { state: "streaming", input: text } }]);
    void runLive(text, idx);
  }

  function retry(idx: number) {
    const m = messages[idx];
    if (!m?.live || streaming) return;
    patchTurn(idx, () => ({ role: "assistant", text: "", live: { state: "streaming", input: m.live!.input } }));
    void runLive(m.live.input, idx);
  }

  function send() {
    if (suspended || !input.trim()) return;
    if (live) {
      if (streaming) return;
      sendLive(input.trim());
      setInput("");
      return;
    }
    const reply = blocked
      ? `Run blocked: ${runStatusLabel[res.status]}. The caller token does not satisfy this agent's access requirements, so nothing was executed.`
      : res.deniedTools.length || res.deniedKbs.length || res.deniedSkills.length
        ? `Answered with the resources the caller token grants. Skipped for access: ${[...res.deniedTools, ...res.deniedKbs, ...res.deniedSkills].map((d) => d.name).join(", ")}.`
        : "Grounded in 3 sources and 1 tool call. (Sample response in the playground.)";
    setMessages((m) => [...m, { role: "user", text: input }, { role: "assistant", text: reply }]);
    setInput("");
  }

  const subjectIcon = res.subjectType === "service" ? <KeyRound className="size-3" /> : <UserRound className="size-3" />;

  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <Card className="flex min-h-[520px] flex-col lg:col-span-2">
        <CardHeader><CardTitle>Playground</CardTitle><Badge tone="neutral">{live && run ? run.label : (version ?? agent.activeVersion)} · {agent.model}</Badge></CardHeader>
        <CardContent className="flex-1 space-y-4 overflow-y-auto pt-2">
          {suspended && (
            <div className="flex items-start gap-2 rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning">
              <TriangleAlert className="mt-0.5 size-3.5 shrink-0" />
              <span><span className="font-medium">Workspace inactive.</span> Endpoints are suspended — reactivate the workspace in Settings to run.</span>
            </div>
          )}
          {blocked && (
            <div className="flex items-start gap-2 rounded-lg border border-critical/40 bg-critical/10 px-3 py-2 text-xs text-critical">
              <TriangleAlert className="mt-0.5 size-3.5 shrink-0" />
              <span><span className="font-medium">{runStatusLabel[res.status]}.</span> With this token the run would be rejected before any tool or knowledge call.</span>
            </div>
          )}
          {live && messages.length === 0 && (
            <p className="text-center text-xs text-muted">Send a message to run {run?.target === "draft" ? "the draft" : run?.label ?? "the active version"}. Answers stream from the agent; cost and latency are the recorded run&apos;s.</p>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-[80%] rounded-xl px-4 py-2.5 text-sm leading-relaxed ${m.role === "user" ? "bg-accent text-accent-ink" : "border border-border bg-elevated text-ink-2"}`}>
                {m.live ? <LiveTurn m={m} onRetry={() => retry(i)} canRetry={!streaming} /> : m.text}
                {m.role === "assistant" && !m.live && <div className="mt-2 flex gap-3 text-[11px] text-muted"><span>1.8s</span><span>9,120 tokens</span><span>{usd(0.034)}</span><span className="text-good">guardrails passed</span></div>}
              </div>
            </div>
          ))}
        </CardContent>
        <div className="border-t border-border p-3">
          <div className="flex items-center gap-2">
            <Input value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()} placeholder={suspended ? "Workspace inactive — read-only" : "Send a test message"} disabled={suspended} />
            {streaming
              ? <Button variant="secondary" onClick={() => abortRef.current?.abort()} aria-label="Stop"><Square className="size-4" /></Button>
              : <Button variant="primary" onClick={send} disabled={suspended} aria-label="Send"><Send className="size-4" /></Button>}
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
              connect={res.connectTools}
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
            <ResourceGroup
              icon={<Sparkles className="size-3.5 text-s2" />}
              title="Skills"
              granted={res.grantedSkills}
              denied={res.deniedSkills}
              empty="No skills bound"
              blocked={blocked}
            />
          </div>
        </Section>
      </div>
    </div>
  );
}

/** One live assistant turn: streamed text + state + citations + the recorded run's cost/latency. */
function LiveTurn({ m, onRetry, canRetry }: { m: Msg; onRetry: () => void; canRetry: boolean }) {
  const l = m.live!;
  const ranLabel = l.ran ? (l.ran === "draft" ? "draft" : l.ran) : null;
  return (
    <div data-turn-state={l.state}>
      {l.state === "streaming" && !m.text && <span className="text-muted">Thinking…</span>}
      {m.text && <p className="whitespace-pre-wrap">{m.text}{l.state === "streaming" && <span className="ml-0.5 animate-pulse">▍</span>}</p>}
      {l.refusal && (
        <div className={`${m.text ? "mt-2" : ""} flex items-start gap-2 rounded-lg border px-2.5 py-2 text-xs ${l.state === "refused" ? "border-critical/40 bg-critical/10 text-critical" : "border-warning/40 bg-warning/10 text-warning"}`}>
          <TriangleAlert className="mt-0.5 size-3.5 shrink-0" />
          <span><span className="font-medium">{l.refusal.title}.</span> {l.refusal.body}{l.state === "incomplete" && m.text ? " (Partial answer — incomplete.)" : ""}</span>
        </div>
      )}
      {!!l.citations?.length && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {l.citations.map((c, k) => (
            <span key={`${c.document_id ?? c.chunk_id ?? k}`} className="inline-flex items-center gap-1 rounded-md border border-border px-1.5 py-0.5 text-[11px] text-ink-2">
              <FileText className="size-3" />{c.document_name ?? c.document_id ?? "source"}
            </span>
          ))}
        </div>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-3 text-[11px] text-muted">
        {ranLabel && <Badge tone="neutral">{ranLabel === "draft" ? "ran: draft" : `ran: ${ranLabel}`}</Badge>}
        {l.latencyMs != null && <span>{(l.latencyMs / 1000).toFixed(2)}s</span>}
        {l.tokens != null && <span>{l.tokens.toLocaleString()} tokens</span>}
        {l.costUsd != null && <span>{usd(l.costUsd)}</span>}
        {l.state === "streaming" && <span>streaming…</span>}
        {(l.state === "incomplete" || (l.state === "refused" && l.refusal?.retryable)) && (
          <button type="button" onClick={onRetry} disabled={!canRetry} className="inline-flex items-center gap-1 text-accent hover:underline disabled:opacity-50">
            <RotateCcw className="size-3" /> Retry
          </button>
        )}
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
  icon, title, granted, denied, empty, blocked, connect = [],
}: {
  icon: React.ReactNode;
  title: string;
  granted: string[];
  denied: { name: string; reason: string }[];
  empty: string;
  blocked: boolean;
  /** per_user tools the caller may use only after connecting their own account (doc 17). */
  connect?: { name: string; reason: string }[];
}) {
  if (granted.length === 0 && denied.length === 0 && connect.length === 0) {
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
        {!blocked && connect.map((c) => (
          <li key={c.name} className="flex items-center gap-2 text-xs text-accent" title={c.reason}>
            <KeyRound className="size-3.5 shrink-0" /> <span>{c.name}</span>
            <span className="ml-auto text-[10px] normal-case text-ink-2">{c.reason}</span>
          </li>
        ))}
        {(blocked ? [...granted.map((name) => ({ name, reason: "run blocked" })), ...connect, ...denied] : denied).map((d) => (
          <li key={d.name} className="flex items-center gap-2 text-xs text-muted line-through decoration-muted/50" title={d.reason}>
            <Lock className="size-3.5 shrink-0 no-underline" /> <span className="no-underline">{d.name}</span>
            <span className="ml-auto text-[10px] normal-case no-underline">{d.reason}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
