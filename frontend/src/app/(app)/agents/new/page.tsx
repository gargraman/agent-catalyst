"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, Bot, Check, Database, FileText, Headphones, MessageSquare, Plug, Rocket, Sparkles } from "lucide-react";
import {
  Card, CardHeader, CardTitle, CardContent, CardDescription, Badge, Button, Field, Input, Select, Textarea,
} from "@/components/ui";
import { Term } from "@/components/term";
import { MODELS, type Agent, type ReasoningEffort } from "@/lib/data";
import { addAgent, genId, nowIso, useKnowledgeBases, useTools } from "@/lib/store";
import { usePrincipal } from "@/lib/auth";
import { canUse } from "@/lib/access-policy";
import { usd } from "@/lib/format";

type Preset = {
  description: string;
  provider: Agent["provider"];
  model: string;
  reasoning: string;
  systemPrompt: string;
  outputFormat: string;
  tags: string[];
  showKnowledge: boolean;
  showTools: boolean;
  knowledgeBases: string[];
  tools: string[];
};

const TEMPLATES: { key: string; name: string; desc: string; icon: React.ComponentType<{ className?: string }>; preset: Preset }[] = [
  {
    key: "scratch", name: "Start from scratch", desc: "A blank chat agent you configure yourself.", icon: Sparkles,
    preset: {
      description: "", provider: "Anthropic", model: "claude-opus-5-5", reasoning: "medium",
      systemPrompt: "You are a helpful internal assistant. Be concise and accurate. If you are unsure, say so.",
      outputFormat: "Free text", tags: [], showKnowledge: false, showTools: false, knowledgeBases: [], tools: [],
    },
  },
  {
    key: "rag", name: "Knowledge Q&A", desc: "Grounded answers over a knowledge base, with citations.", icon: FileText,
    preset: {
      description: "Answers questions grounded in your documents, with citations.",
      provider: "Anthropic", model: "claude-sonnet-5-5", reasoning: "high",
      systemPrompt: "You are a knowledge assistant. Ground every answer in the retrieved context and cite sources. If the context is insufficient, say so plainly. Do not fabricate.",
      outputFormat: "Structured JSON + citations", tags: ["rag", "q&a"], showKnowledge: true, showTools: false,
      knowledgeBases: ["product-docs"], tools: [],
    },
  },
  {
    key: "support", name: "Customer support assistant", desc: "Answers customer questions from your help center and policies, with a friendly tone.", icon: Headphones,
    preset: {
      description: "Resolves customer questions grounded in the help center and policy docs, and escalates when it is unsure.",
      provider: "Anthropic", model: "claude-sonnet-5-5", reasoning: "medium",
      systemPrompt: "You are a customer support assistant. Answer using the help center and policy documents, in a warm, concise tone. If a question is outside the docs or needs an account change, say so and offer to hand off to a human. Never invent policies, prices, or account details.",
      outputFormat: "Free text", tags: ["support", "helpdesk"], showKnowledge: true, showTools: false,
      knowledgeBases: ["product-docs"], tools: [],
    },
  },
  {
    key: "triage", name: "Alert triage", desc: "Classify and summarize inbound signals with tool calls.", icon: Bot,
    preset: {
      description: "Triages inbound alerts, correlates signals, and drafts an analyst-ready verdict.",
      provider: "Anthropic", model: "claude-opus-5-5", reasoning: "high",
      systemPrompt: "You are a security triage assistant. Classify and correlate inbound signals, then draft an analyst-ready verdict with severity and next steps. Never fabricate identifiers or CVEs.",
      outputFormat: "Structured JSON", tags: ["security", "triage"], showKnowledge: false, showTools: true,
      knowledgeBases: [], tools: ["http-enrich-ip", "subagent-log-query"],
    },
  },
];

const OUTPUT_FORMATS = ["Free text", "Structured JSON", "Structured JSON + citations"];

/* @backend integration points (this page):
 *  - @backend POST /v1/agents - create agent from the form [via addAgent]
 *  - @backend GET /v1/knowledge-bases - knowledge-base picker list [via useKnowledgeBases]
 *  - @backend GET /v1/tools - tools picker list [via useTools]
 *  - @backend template presets (TEMPLATES) are client-side config, not a backend call
 * See src/lib/store.ts markers + docs/backend-integration-map.md for the authoritative contracts.
 */
export default function NewAgentPage() {
  const router = useRouter();
  const principal = usePrincipal();
  // Only bind resources the builder can actually use in the active workspace (Part B2).
  const kbOptions = useKnowledgeBases().filter((kb) => canUse(kb.accessPolicy, principal));
  const toolOptions = useTools().filter((t) => canUse(t.accessPolicy, principal));
  const [tplKey, setTplKey] = React.useState("rag");
  const initial = TEMPLATES.find((t) => t.key === "rag")!.preset;

  const [name, setName] = React.useState("");
  const [description, setDescription] = React.useState(initial.description);
  const [provider, setProvider] = React.useState<string>(initial.provider);
  const [model, setModel] = React.useState<string>(initial.model);
  const [reasoning, setReasoning] = React.useState(initial.reasoning);
  const [systemPrompt, setSystemPrompt] = React.useState(initial.systemPrompt);
  const [outputFormat, setOutputFormat] = React.useState(initial.outputFormat);
  // Templates hardcode example resource ids; only ever select ones that exist in the active workspace.
  const [knowledgeBases, setKnowledgeBases] = React.useState<string[]>(
    initial.knowledgeBases.filter((id) => kbOptions.some((kb) => kb.id === id)),
  );
  const [tools, setTools] = React.useState<string[]>(
    initial.tools.filter((n) => toolOptions.some((t) => t.name === n)),
  );
  const touched = React.useRef<Record<string, boolean>>({});

  const current = TEMPLATES.find((t) => t.key === tplKey)!;
  const providers = [...new Set(MODELS.map((m) => m.provider))];
  const modelsForProvider = MODELS.filter((m) => m.provider === provider);
  const selectedModel = MODELS.find((m) => m.model === model);

  function applyTemplate(key: string) {
    setTplKey(key);
    const p = TEMPLATES.find((t) => t.key === key)!.preset;
    const t = touched.current;
    if (!t.description) setDescription(p.description);
    if (!t.provider) setProvider(p.provider);
    if (!t.model) setModel(p.model);
    if (!t.reasoning) setReasoning(p.reasoning);
    if (!t.systemPrompt) setSystemPrompt(p.systemPrompt);
    if (!t.outputFormat) setOutputFormat(p.outputFormat);
    // template-structural picks always reset with the template, but only bind
    // resources that exist in the active workspace (presets hardcode example ids).
    setKnowledgeBases(p.knowledgeBases.filter((id) => kbOptions.some((kb) => kb.id === id)));
    setTools(p.tools.filter((n) => toolOptions.some((t) => t.name === n)));
  }

  function toggle(list: string[], v: string): string[] {
    return list.includes(v) ? list.filter((x) => x !== v) : [...list, v];
  }

  function create() {
    const slug = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || genId("agent");
    const agent: Agent = {
      id: slug, name: name.trim() || "Untitled agent",
      description: description || current.preset.description || "New agent.",
      status: "draft", provider: provider as Agent["provider"], model,
      owner: "Raman Garg", createdAt: nowIso(), updatedAt: nowIso(),
      activeVersion: "v1", versions: 1, tags: current.preset.tags,
      calls30d: 0, cost30d: 0, p95ms: 0, evalPass: 0, drift: "none",
      callsSpark: [0, 0, 0, 0, 0, 0, 0],
      knowledgeBases: current.preset.showKnowledge ? knowledgeBases.filter((id) => kbOptions.some((kb) => kb.id === id)) : [],
      tools: current.preset.showTools ? tools.filter((n) => toolOptions.some((t) => t.name === n)) : [],
      // Persist the config the form actually collects so it survives to the Configure tab
      // (resolveConfig fills the rest from DEFAULT_AGENT_CONFIG). Map the form's output-format
      // vocabulary to the config's { outputFormat, includeCitations } pair.
      config: {
        systemPrompt,
        reasoningEffort: reasoning as ReasoningEffort,
        outputFormat: outputFormat === "Free text" ? "text" : "json",
        includeCitations: outputFormat === "Structured JSON + citations",
      },
    };
    addAgent(agent);
    router.push(`/agents/${agent.id}`);
  }

  return (
    <div className="space-y-6">
      <div>
        <Button href="/agents" variant="ghost" size="sm"><ArrowLeft className="size-4" /> Back to agents</Button>
        <h1 className="mt-2 text-xl font-semibold tracking-tight text-ink">New agent</h1>
        <p className="mt-1 text-sm text-muted">Pick a starting point and base model. The template prefills sensible defaults you can change.</p>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <div>
            <h2 className="mb-3 text-sm font-semibold text-ink">Template</h2>
            <div className="grid gap-3 sm:grid-cols-3">
              {TEMPLATES.map((t) => {
                const Icon = t.icon;
                const on = tplKey === t.key;
                return (
                  <button
                    key={t.key}
                    onClick={() => applyTemplate(t.key)}
                    className={`lift rounded-xl border p-4 text-left ${on ? "border-accent bg-accent-soft" : "border-border bg-surface hover:border-border-strong"}`}
                  >
                    <div className="flex items-center justify-between">
                      <span className={`inline-flex size-9 items-center justify-center rounded-lg ${on ? "bg-accent text-accent-ink" : "bg-elevated text-ink-2"}`}><Icon className="size-4" /></span>
                      {on && <Check className="size-4 text-accent" />}
                    </div>
                    <div className="mt-3 text-sm font-medium text-ink">{t.name}</div>
                    <div className="mt-1 text-xs text-muted">{t.desc}</div>
                  </button>
                );
              })}
            </div>
          </div>

          <Card>
            <CardHeader><CardTitle>Details</CardTitle></CardHeader>
            <CardContent className="space-y-4">
              <Field label="Name">
                <Input value={name} onChange={(e) => { touched.current.name = true; setName(e.target.value); }} placeholder="e.g. SOC Triage Copilot" />
              </Field>
              <Field label="Description" hint="Shown on the dashboard and agent list.">
                <Textarea rows={2} value={description} onChange={(e) => { touched.current.description = true; setDescription(e.target.value); }} placeholder="What does this agent do, and who is it for?" />
              </Field>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Provider">
                  <Select value={provider} onChange={(e) => { touched.current.provider = true; setProvider(e.target.value); const first = MODELS.find((m) => m.provider === e.target.value); if (first) setModel(first.model); }}>
                    {providers.map((p) => <option key={p}>{p}</option>)}
                  </Select>
                </Field>
                <Field label="Model">
                  <Select value={model} onChange={(e) => { touched.current.model = true; setModel(e.target.value); }}>
                    {modelsForProvider.map((m) => <option key={m.model}>{m.model}</option>)}
                  </Select>
                </Field>
                <Field label={<Term id="reasoning-effort">Reasoning effort</Term>}>
                  <Select value={reasoning} onChange={(e) => { touched.current.reasoning = true; setReasoning(e.target.value); }}>
                    <option>low</option><option>medium</option><option>high</option><option>xhigh</option>
                  </Select>
                </Field>
                <Field label="Output format">
                  <Select value={outputFormat} onChange={(e) => { touched.current.outputFormat = true; setOutputFormat(e.target.value); }}>
                    {OUTPUT_FORMATS.map((o) => <option key={o}>{o}</option>)}
                  </Select>
                </Field>
              </div>
            </CardContent>
          </Card>

          {current.preset.showKnowledge && (
            <Card>
              <CardHeader>
                <div><CardTitle className="flex items-center gap-2"><Database className="size-4 text-muted" /> Knowledge</CardTitle>
                  <CardDescription>Bind knowledge bases to ground answers. Retrieval runs as a tool with citations.</CardDescription></div>
              </CardHeader>
              <CardContent className="grid gap-2 sm:grid-cols-2">
                {kbOptions.map((kb) => {
                  const on = knowledgeBases.includes(kb.id);
                  return (
                    <button key={kb.id} onClick={() => setKnowledgeBases((l) => toggle(l, kb.id))}
                      className={`flex items-center justify-between rounded-lg border px-3 py-2.5 text-left text-sm ${on ? "border-accent bg-accent-soft" : "border-border bg-elevated hover:border-border-strong"}`}>
                      <span className="min-w-0"><span className="block truncate font-medium text-ink">{kb.name}</span>
                        <span className="block truncate text-xs text-muted">{kb.docs} docs</span></span>
                      {on ? <Check className="size-4 text-accent" /> : <span className="size-4 rounded border border-border" />}
                    </button>
                  );
                })}
              </CardContent>
            </Card>
          )}

          {current.preset.showTools && (
            <Card>
              <CardHeader>
                <div><CardTitle className="flex items-center gap-2"><Plug className="size-4 text-muted" /> Tools</CardTitle>
                  <CardDescription>Tools this agent can call. Schemas are snapshotted into each version.</CardDescription></div>
              </CardHeader>
              <CardContent className="grid gap-2 sm:grid-cols-2">
                {toolOptions.map((t) => {
                  const on = tools.includes(t.name);
                  return (
                    <button key={t.name} onClick={() => setTools((l) => toggle(l, t.name))}
                      className={`flex items-center justify-between rounded-lg border px-3 py-2.5 text-left text-sm ${on ? "border-accent bg-accent-soft" : "border-border bg-elevated hover:border-border-strong"}`}>
                      <span className="min-w-0"><span className="block truncate font-mono text-xs font-medium text-ink">{t.name}</span>
                        <span className="block truncate text-xs text-muted">{t.kind}</span></span>
                      {on ? <Check className="size-4 text-accent" /> : <span className="size-4 rounded border border-border" />}
                    </button>
                  );
                })}
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader><CardTitle><Term id="system-prompt">System prompt</Term></CardTitle><CardDescription>Prefilled for the chosen template. Edit freely.</CardDescription></CardHeader>
            <CardContent>
              <Textarea rows={6} value={systemPrompt} onChange={(e) => { touched.current.systemPrompt = true; setSystemPrompt(e.target.value); }} className="font-mono text-xs leading-relaxed" />
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>Modality</CardTitle></CardHeader>
            <CardContent className="grid gap-3 sm:grid-cols-2">
              <div className="flex items-center gap-3 rounded-xl border border-accent bg-accent-soft p-4">
                <span className="inline-flex size-9 items-center justify-center rounded-lg bg-accent text-accent-ink"><MessageSquare className="size-4" /></span>
                <div><div className="text-sm font-medium text-ink">Chat</div><div className="text-xs text-muted">Text in, text or JSON out.</div></div>
                <Check className="ml-auto size-4 text-accent" />
              </div>
              <div className="flex items-center gap-3 rounded-xl border border-border bg-surface p-4 opacity-60">
                <span className="inline-flex size-9 items-center justify-center rounded-lg bg-elevated text-muted"><Headphones className="size-4" /></span>
                <div><div className="text-sm font-medium text-ink-2">Voice</div><div className="text-xs text-muted">Planned for a later release.</div></div>
                <Badge tone="neutral" className="ml-auto">Soon</Badge>
              </div>
            </CardContent>
          </Card>
        </div>

        <div className="space-y-4">
          <Card className="sticky top-20">
            <CardHeader><CardTitle>Summary</CardTitle><CardDescription>Review before creating.</CardDescription></CardHeader>
            <CardContent className="space-y-3 pt-0 text-sm">
              <Row k="Name" v={name || "Untitled agent"} />
              <Row k="Template" v={current.name} />
              <Row k="Provider" v={provider} />
              <Row k="Model" v={<span className="font-mono text-xs">{model}</span>} />
              <Row k="Output" v={outputFormat} />
              {current.preset.showKnowledge && <Row k="Knowledge" v={`${knowledgeBases.length} base${knowledgeBases.length === 1 ? "" : "s"}`} />}
              {current.preset.showTools && <Row k="Tools" v={`${tools.length} tool${tools.length === 1 ? "" : "s"}`} />}
              <Row k="Modality" v="Chat" />
              {selectedModel && <Row k="Pricing" v={<span className="tnum">{usd(selectedModel.inUsd)} / {usd(selectedModel.outUsd)} per 1M</span>} />}
              <div className="pt-2">
                <Button variant="primary" className="w-full" onClick={create}><Rocket className="size-4" /> Create agent</Button>
                <p className="mt-2 text-center text-xs text-muted">Created as a draft. Publish after a passing eval.</p>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
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
