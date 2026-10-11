"use client";

import * as React from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  Archive, ArrowLeft, Blocks, ChevronDown, ChevronRight, Copy,
  Database, FlaskConical, GitBranch, History, Lock, MessageSquare, MoreHorizontal,
  Plug, Plus, Rocket, Settings2, Shield, ShieldCheck, Sparkles, Trash2, TriangleAlert,
  Wrench, X,
} from "lucide-react";
import {
  Badge, Button, Card, CardContent, CardHeader, CardTitle, CodeBlock, EmptyState,
  Field, Input, Progress, Select, Separator, StatusDot, Table, TD, TH, THead, TR, Textarea,
} from "@/components/ui";
import { Tabs, Segmented } from "@/components/tabs";
import { CopyButton } from "@/components/copy-button";
import { Dialog, ConfirmDialog } from "@/components/dialog";
import {
  getAgent, getKb, getModel, getVersions, resolveConfig, parseNum,
  MODELS, driftTone, statusLabel, statusTone, verdictLabel, verdictTone,
  type Agent, type AgentStatus, type EvalRun,
  type ReasoningEffort, type OutputFormat, type TruncationStrategy, type InjectionStrategy, type Reranker, type GuardrailStage,
} from "@/lib/data";
import {
  useAgent, useTokens, useTools, useKnowledgeBases, useMcpServers, useEvalRunsForAgent,
  useChannelsForAgent, addEvalRun, addToken, revokeToken,
  updateAgent, updateAgentConfig, deleteAgent, duplicateAgent, toast, genId, nowIso,
} from "@/lib/store";
import { useAuth, usePrincipal } from "@/lib/auth";
import { GateButton } from "@/components/gate";
import { Term } from "@/components/term";
import { EvalExplainer, EvalFailingBanner, EvalFailureSummary, PublishBlockedDialog } from "@/components/eval-explainer";
import { canUse, canManage } from "@/lib/access-policy";
import { AccessNote } from "@/components/access-note";
import { AccessPolicyEditor } from "@/components/access-policy-editor";
import type { Permission } from "@/lib/permissions";
import { dateShort, ms, num, pct, relative, usd } from "@/lib/format";

/* -------- local helpers -------- */
function Toggle({ on }: { on: boolean }) {
  return (
    <span className={`inline-flex h-5 w-9 items-center rounded-full p-0.5 transition-colors ${on ? "bg-accent" : "bg-border-strong"}`}>
      <span className={`size-4 rounded-full bg-white transition-transform ${on ? "translate-x-4" : ""}`} />
    </span>
  );
}
function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 py-3">
      <span className="text-sm text-ink-2">{label}</span>
      <span className="text-sm font-medium text-ink">{children}</span>
    </div>
  );
}
function SectionCard({ title, desc, children, action }: { title: React.ReactNode; desc?: string; children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <div><CardTitle>{title}</CardTitle>{desc && <p className="mt-0.5 text-xs text-muted">{desc}</p>}</div>
        {action}
      </CardHeader>
      <CardContent className="pt-4">{children}</CardContent>
    </Card>
  );
}

/* @backend integration points (this page - the agent hub, listed per tab):
 *  - @backend GET /v1/agents/:id - load the agent [via useAgent]
 *  - @backend Header: POST /v1/agents/:id/duplicate (duplicateAgent); POST /v1/agents/:id/evals async run eval (addEvalRun); PATCH /v1/agents/:id publish/unpublish/archive (updateAgent); DELETE /v1/agents/:id delete (deleteAgent)
 *  - @backend Configure: PATCH /v1/agents/:id - general + model config [via updateAgent]; GET /v1/models - provider/model list (MODELS is local today)
 *  - @backend Knowledge tab: PATCH /v1/agents/:id - bind/detach knowledgeBases [via updateAgent]; GET /v1/knowledge-bases picker [via useKnowledgeBases]
 *  - @backend Tools tab: PATCH /v1/agents/:id - bind/detach tools [via updateAgent]; GET /v1/tools picker [via useTools]; GET /v1/mcp-servers picker [via useMcpServers]
 *  - @backend Guardrails tab: GET/PUT /v1/agents/:id/guardrails - per-version config (LOCAL React state only today)
 *  - @backend Access tab: PUT /v1/agents/:id/access-policy - access policy [via setAccessPolicy]
 *  - @backend Versions tab: GET versions; PATCH /v1/agents/:id activeVersion for rollback [via updateAgent]; "Save as new version" -> POST version and "Re-freeze baseline" -> PATCH baseline (mock toasts today)
 *  - @backend Playground moved to its own route (/playground); header "Open in Playground" deep-links there. Invoke contract lives on that page and in src/components/playground.tsx.
 *  - @backend Evals tab: GET /v1/agents/:id/evals [via useEvalRunsForAgent]; POST /v1/agents/:id/evals async run [via addEvalRun]
 *  - @backend Publish & Export: endpoint URL + cURL/TS/Python snippets + OpenAPI are GET; POST /v1/agents/:id/tokens (addToken); DELETE /v1/tokens/:id (revokeToken); GET /v1/agents/:id/tokens (useTokens)
 * See src/lib/store.ts markers + docs/backend-integration-map.md for the authoritative contracts.
 */
export default function AgentDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { can } = useAuth();
  const principal = usePrincipal();
  const agent = useAgent(params.id);
  const [runEvalOpen, setRunEvalOpen] = React.useState(false);
  const [publishOpen, setPublishOpen] = React.useState(false);
  const [publishBlockedOpen, setPublishBlockedOpen] = React.useState(false);
  const [deleteOpen, setDeleteOpen] = React.useState(false);

  if (!agent) {
    return (
      <div className="space-y-6">
        <Link href="/agents" className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-ink"><ArrowLeft className="size-4" /> Back to agents</Link>
        <EmptyState icon={<TriangleAlert className="size-6" />} title="Agent not found" hint="This agent may have been archived or the link is out of date." />
      </div>
    );
  }

  const versions = getVersions(agent.id);
  const model = getModel(agent.model);
  const manage = canManage(agent.accessPolicy, principal);
  const manageTitle = manage ? undefined : "Restricted by this agent's access policy";

  return (
    <div className="space-y-6">
      <div>
        <Link href="/agents" className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-ink"><ArrowLeft className="size-4" /> Agents</Link>
        <div className="mt-3 flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-xl font-semibold tracking-tight text-ink">{agent.name}</h1>
              <Badge tone={statusTone[agent.status]}><StatusDot tone={statusTone[agent.status]} />{statusLabel[agent.status]}</Badge>
              <Badge tone="neutral">{agent.activeVersion} active</Badge>
            </div>
            <p className="mt-1.5 max-w-2xl text-sm text-muted">{agent.description}</p>
          </div>
          <div className="flex items-center gap-2">
            <GateButton permission="agent.edit" variant="outline" onClick={() => { const id = duplicateAgent(agent.id); if (id) router.push(`/agents/${id}`); }}><Copy className="size-4" /> Duplicate</GateButton>
            <GateButton permission="eval.run" variant="outline" onClick={() => setRunEvalOpen(true)}><FlaskConical className="size-4" /> Run eval</GateButton>
            {agent.status !== "archived" && (
              <GateButton permission="playground.use" variant="outline" href={`/playground?agent=${agent.id}`}><MessageSquare className="size-4" /> Open in Playground</GateButton>
            )}
            {agent.status === "published"
              ? <GateButton permission="agent.publish" disabled={!manage} title={manageTitle} variant="outline" onClick={() => { updateAgent(agent.id, { status: "draft" }, false); toast("Endpoint unpublished", "neutral"); }}><Rocket className="size-4" /> Unpublish</GateButton>
              : (
                <GateButton
                  permission="agent.publish"
                  disabled={!manage}
                  title={manageTitle}
                  variant="primary"
                  onClick={() => {
                    if (agent.status === "eval_failing") {
                      setPublishBlockedOpen(true);
                    } else {
                      setPublishOpen(true);
                    }
                  }}
                >
                  <Rocket className="size-4" /> Publish {agent.activeVersion}
                </GateButton>
              )}
            <HeaderMenu agent={agent} can={can} onDelete={() => setDeleteOpen(true)} />
          </div>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-muted">
          <span className="inline-flex items-center gap-1.5"><Sparkles className="size-3.5" /> {agent.provider} <span className="font-mono text-ink-2">{agent.model}</span></span>
          <span>Owner <span className="text-ink-2">{agent.owner}</span></span>
          <span>Created {dateShort(agent.createdAt)}</span>
          <span>Updated {relative(agent.updatedAt)}</span>
        </div>
      </div>

      {agent.status === "eval_failing" && (
        <EvalFailingBanner agent={agent} onRunEval={() => setRunEvalOpen(true)} />
      )}

      {agent.status !== "archived" && (
        <div className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-border bg-border sm:grid-cols-4 lg:grid-cols-5">
          {[
            { k: "Calls (30d)", v: num(agent.calls30d, { compact: true }) },
            { k: "Spend (30d)", v: usd(agent.cost30d, { compact: true }) },
            { k: "p95 latency", term: "p95-latency", v: ms(agent.p95ms) },
            { k: "Eval pass", term: "eval-pass", v: pct(agent.evalPass) },
            { k: "Drift", term: "drift", v: agent.drift, badge: true as const },
          ].map((s) => (
            <div key={s.k} className="bg-surface px-4 py-3">
              <p className="text-[11px] text-muted">{s.term ? <Term id={s.term}>{s.k}</Term> : s.k}</p>
              {"badge" in s ? <Badge tone={driftTone[agent.drift]} className="mt-1">{agent.drift}</Badge> : <p className="mt-0.5 text-lg font-semibold tnum text-ink">{s.v}</p>}
            </div>
          ))}
        </div>
      )}

      <Tabs
        tabs={[
          { key: "configure", label: "Configure", icon: <Settings2 className="size-4" />, content: <Configure agent={agent} model={model} /> },
          { key: "knowledge", label: "Knowledge", icon: <Database className="size-4" />, content: <Knowledge agent={agent} /> },
          { key: "tools", label: "Tools & MCP", icon: <Plug className="size-4" />, content: <ToolsTab agent={agent} /> },
          { key: "guardrails", label: "Guardrails", icon: <Shield className="size-4" />, content: <Guardrails agent={agent} /> },
          { key: "access", label: "Access", icon: <Lock className="size-4" />, content: <AccessTab agent={agent} canManage={can("access.manage")} /> },
          { key: "versions", label: "Versions", icon: <History className="size-4" />, content: <Versions agent={agent} versions={versions} /> },
          { key: "evals", label: "Evaluations", icon: <FlaskConical className="size-4" />, content: <Evals agent={agent} /> },
          { key: "publish", label: "Publish & Export", icon: <Rocket className="size-4" />, content: <Publish agent={agent} /> },
        ]}
      />

      <RunEvalDialog agent={agent} open={runEvalOpen} onClose={() => setRunEvalOpen(false)} />
      <PublishBlockedDialog
        agent={agent}
        open={publishBlockedOpen}
        onClose={() => setPublishBlockedOpen(false)}
        onRunEval={() => { setPublishBlockedOpen(false); setRunEvalOpen(true); }}
      />
      <ConfirmDialog
        open={publishOpen}
        onClose={() => setPublishOpen(false)}
        onConfirm={() => { updateAgent(agent.id, { status: "published" }, false); toast(`Published ${agent.activeVersion}`); }}
        title="Publish version"
        confirmLabel="Publish"
        tone="primary"
        body={<>Publish {agent.activeVersion}? This pins the endpoint to this version.</>}
      />
      <ConfirmDialog
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        onConfirm={() => { deleteAgent(agent.id); router.push("/agents"); }}
        title="Delete agent"
        confirmLabel="Delete"
        body={<>Delete <span className="font-medium text-ink">{agent.name}</span> and all its versions? This cannot be undone.</>}
      />
    </div>
  );
}

function HeaderMenu({ agent, can, onDelete }: { agent: Agent; can: (p: Permission) => boolean; onDelete: () => void }) {
  const [open, setOpen] = React.useState(false);
  const item = "flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-ink-2 hover:bg-elevated hover:text-ink";
  const archived = agent.status === "archived";
  // Archive + Delete both require lifecycle management; hide the menu if neither is allowed.
  if (!can("agent.delete")) return null;
  return (
    <div className="relative inline-block text-left">
      <button onClick={() => setOpen((o) => !o)} aria-label="More actions" className="inline-flex size-9 items-center justify-center rounded-lg border border-border text-ink-2 transition-colors hover:bg-elevated hover:text-ink">
        <MoreHorizontal className="size-4" />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute right-0 z-20 mt-1 w-44 overflow-hidden rounded-lg border border-border bg-surface py-1 card-elev">
            <button className={item} onClick={() => { setOpen(false); updateAgent(agent.id, { status: archived ? "draft" : "archived" }); }}>
              <Archive className="size-4" /> {archived ? "Restore" : "Archive"}
            </button>
            <div className="my-1 h-px bg-border" />
            <button className={`${item} text-critical hover:text-critical`} onClick={() => { setOpen(false); onDelete(); }}>
              <Trash2 className="size-4" /> Delete
            </button>
          </div>
        </>
      )}
    </div>
  );
}

/* ================================================================= Configure */
function Configure({ agent, model }: { agent: ReturnType<typeof getAgent> & {}; model: ReturnType<typeof getModel> }) {
  const principal = usePrincipal();
  const manage = canManage(agent.accessPolicy, principal);
  const manageTitle = manage ? undefined : "Restricted by this agent's access policy";
  const providers = [...new Set(MODELS.map((m) => m.provider))];
  const base = resolveConfig(agent);
  const [provider, setProvider] = React.useState<string>(agent.provider);
  const [modelId, setModelId] = React.useState<string>(agent.model);
  const [publishOpen, setPublishOpen] = React.useState(false);
  const [publishBlockedOpen, setPublishBlockedOpen] = React.useState(false);
  const [runEvalOpen, setRunEvalOpen] = React.useState(false);
  const [saveOpen, setSaveOpen] = React.useState(false);
  const [deleteOpen, setDeleteOpen] = React.useState(false);
  const router = useRouter();
  const [name, setName] = React.useState(agent.name);
  const [lifecycle, setLifecycle] = React.useState<AgentStatus>(agent.status);
  const [description, setDescription] = React.useState(agent.description);
  // Model params
  const [temperature, setTemperature] = React.useState(String(base.temperature));
  const [maxTokens, setMaxTokens] = React.useState(String(base.maxOutputTokens));
  const [reasoning, setReasoning] = React.useState<ReasoningEffort>(base.reasoningEffort);
  const [fallback, setFallback] = React.useState(base.fallbackModel);
  // System prompt
  const [systemPrompt, setSystemPrompt] = React.useState(base.systemPrompt);
  // I/O contract
  const [outputFormat, setOutputFormat] = React.useState<OutputFormat>(base.outputFormat);
  const [includeCitations, setIncludeCitations] = React.useState(base.includeCitations);
  // Context & memory (planned — captured, not enforced at runtime)
  const [truncation, setTruncation] = React.useState<TruncationStrategy>(base.truncationStrategy);
  const [sessionMemory, setSessionMemory] = React.useState(base.sessionMemory);
  const [maxTurns, setMaxTurns] = React.useState(String(base.maxTurnsRetained));
  const [longTerm, setLongTerm] = React.useState(base.longTermMemory);
  // Spend controls
  const [monthlyCap, setMonthlyCap] = React.useState(String(base.spend.monthlyCapUsd));
  const [concurrency, setConcurrency] = React.useState(String(base.spend.maxConcurrency));
  const [timeout, setTimeoutSec] = React.useState(String(base.spend.timeoutSec));


  const generalDirty = name !== agent.name || lifecycle !== agent.status || description !== agent.description;
  const modelDirty = provider !== agent.provider || modelId !== agent.model || parseNum(temperature, base.temperature) !== base.temperature || parseNum(maxTokens, base.maxOutputTokens) !== base.maxOutputTokens || reasoning !== base.reasoningEffort || fallback !== base.fallbackModel;
  const promptDirty = systemPrompt !== base.systemPrompt;
  const ioDirty = outputFormat !== base.outputFormat || includeCitations !== base.includeCitations;
  const memoryDirty = truncation !== base.truncationStrategy || sessionMemory !== base.sessionMemory || parseNum(maxTurns, base.maxTurnsRetained) !== base.maxTurnsRetained || longTerm !== base.longTermMemory;
  const spendDirty = parseNum(monthlyCap, base.spend.monthlyCapUsd) !== base.spend.monthlyCapUsd || parseNum(concurrency, base.spend.maxConcurrency) !== base.spend.maxConcurrency || parseNum(timeout, base.spend.timeoutSec) !== base.spend.timeoutSec;

  const curModel = getModel(modelId) ?? model;
  const saveBtn = (dirty: boolean, onClick: () => void) => (
    <GateButton permission="agent.edit" variant="primary" size="sm" disabled={!dirty || !manage} title={manageTitle} onClick={onClick}>Save changes</GateButton>
  );
  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <div className="space-y-6 lg:col-span-2">
        <SectionCard title="General" action={saveBtn(generalDirty, () => updateAgent(agent.id, { name: name.trim() || agent.name, status: lifecycle, description }))}>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Agent name"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
            <Field label="Lifecycle state">
              <Select value={lifecycle} onChange={(e) => setLifecycle(e.target.value as AgentStatus)}>
                <option value="draft">Draft</option><option value="published">Published</option><option value="eval_failing">Eval failing</option><option value="archived">Archived</option>
              </Select>
            </Field>
          </div>
          <div className="mt-4"><Field label="Description"><Textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} /></Field></div>
        </SectionCard>

        <SectionCard title="Model" desc="Model-agnostic. Edits save to the agent's draft config; a provider or model swap should be re-evaluated before you publish." action={saveBtn(modelDirty, () => { updateAgent(agent.id, { provider: provider as Agent["provider"], model: modelId }, false); updateAgentConfig(agent.id, { temperature: parseNum(temperature, base.temperature), maxOutputTokens: parseNum(maxTokens, base.maxOutputTokens), reasoningEffort: reasoning, fallbackModel: fallback }); })}>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Provider">
              <Select value={provider} onChange={(e) => { setProvider(e.target.value); const first = MODELS.find((m) => m.provider === e.target.value); if (first) setModelId(first.model); }}>{providers.map((p) => <option key={p}>{p}</option>)}</Select>
            </Field>
            <Field label="Model">
              <Select value={modelId} onChange={(e) => setModelId(e.target.value)}>{MODELS.filter((m) => m.provider === provider).map((m) => <option key={m.model}>{m.model}</option>)}</Select>
            </Field>
            <Field label={<Term id="temperature">Temperature</Term>}><Input value={temperature} onChange={(e) => setTemperature(e.target.value)} inputMode="decimal" /></Field>
            <Field label={<>Max output <Term id="token">tokens</Term></>}><Input value={maxTokens} onChange={(e) => setMaxTokens(e.target.value)} inputMode="numeric" /></Field>
            <Field label={<Term id="reasoning-effort">Reasoning effort</Term>}>
              <Select value={reasoning} onChange={(e) => setReasoning(e.target.value as ReasoningEffort)}><option value="low">low</option><option value="medium">medium</option><option value="high">high</option><option value="xhigh">xhigh</option><option value="max">max</option></Select>
            </Field>
            <Field label="Fallback model" hint="Used on provider refusal or error">
              <Select value={fallback} onChange={(e) => setFallback(e.target.value)}>{MODELS.map((m) => <option key={m.model}>{m.model}</option>)}</Select>
            </Field>
          </div>
          {curModel && (
            <div className="mt-4 flex flex-wrap gap-2 text-xs text-muted">
              <Badge tone="neutral">Context {curModel.context}</Badge>
              <Badge tone="neutral">In {usd(curModel.inUsd)}/1M</Badge>
              <Badge tone="neutral">Out {usd(curModel.outUsd)}/1M</Badge>
            </div>
          )}
        </SectionCard>

        <SectionCard title={<Term id="system-prompt">System prompt</Term>} action={saveBtn(promptDirty, () => updateAgentConfig(agent.id, { systemPrompt }))}>
          <Textarea value={systemPrompt} onChange={(e) => setSystemPrompt(e.target.value)} rows={6} className="font-mono text-xs leading-relaxed" />
        </SectionCard>

        <SectionCard title="Input / output contract" desc="Typed inputs become the published API request schema." action={saveBtn(ioDirty, () => updateAgentConfig(agent.id, { outputFormat, includeCitations }))}>
          <p className="mb-2 text-xs font-medium text-ink-2">Input variables</p>
          <Table>
            <THead><TR><TH>Name</TH><TH>Type</TH><TH>Required</TH><TH>Default</TH></TR></THead>
            <tbody>
              <TR><TD className="font-mono text-xs text-ink">query</TD><TD>string</TD><TD><Badge tone="good">required</Badge></TD><TD className="text-muted">-</TD></TR>
              <TR><TD className="font-mono text-xs text-ink">severity_hint</TD><TD>enum</TD><TD><Badge tone="neutral">optional</Badge></TD><TD className="text-muted">auto</TD></TR>
            </tbody>
          </Table>
          <p className="mt-2 text-xs text-muted">The input-variable schema is seeded for preview; an editable schema builder is a separate follow-up.</p>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <Field label="Output format"><Select value={outputFormat} onChange={(e) => setOutputFormat(e.target.value as OutputFormat)}><option value="text">Free text</option><option value="json">Structured output (JSON schema)</option></Select></Field>
            <button type="button" onClick={() => setIncludeCitations((v) => !v)} className="flex items-center justify-between rounded-lg border border-border bg-elevated px-3 py-2 text-left">
              <span className="text-sm text-ink-2">Include citations</span><Toggle on={includeCitations} />
            </button>
          </div>
        </SectionCard>

        <SectionCard title="Context & memory" action={saveBtn(memoryDirty, () => updateAgentConfig(agent.id, { truncationStrategy: truncation, sessionMemory, maxTurnsRetained: parseNum(maxTurns, base.maxTurnsRetained), longTermMemory: longTerm }))}>
          <div className="mb-4 flex items-start gap-2 rounded-lg border border-warning/30 bg-warning-soft px-3 py-2">
            <Badge tone="warning">Planned</Badge>
            <p className="text-xs text-ink-2">These settings are captured as config but <span className="font-medium">not enforced at runtime yet</span> — session/long-term memory is designed but unimplemented. See <span className="font-mono">docs/session-memory-status.md</span>.</p>
          </div>
          <div className="divide-y divide-border">
            <Row label="Context window">{curModel?.context ?? "1M"} tokens</Row>
            <div className="py-3"><Field label="Truncation strategy"><Select value={truncation} onChange={(e) => setTruncation(e.target.value as TruncationStrategy)}><option value="summarize">Summarize older turns</option><option value="drop_oldest">Drop oldest turns</option></Select></Field></div>
            <button type="button" onClick={() => setSessionMemory((v) => !v)} className="flex w-full items-center justify-between py-3 text-left"><span className="text-sm text-ink-2">Session memory</span><Toggle on={sessionMemory} /></button>
            <div className="py-3"><Field label="Max turns retained"><Input value={maxTurns} onChange={(e) => setMaxTurns(e.target.value)} inputMode="numeric" /></Field></div>
            <button type="button" onClick={() => setLongTerm((v) => !v)} className="flex w-full items-center justify-between py-3 text-left"><span className="text-sm text-ink-2">Long-term memory (persistent)</span><Toggle on={longTerm} /></button>
          </div>
        </SectionCard>
      </div>

      <aside className="space-y-6">
        <SectionCard title="Lifecycle">
          <div className="divide-y divide-border">
            <Row label="Status"><Badge tone={statusTone[agent.status]}>{statusLabel[agent.status]}</Badge></Row>
            <Row label="Active version">{agent.activeVersion}</Row>
            <Row label="Total versions">{agent.versions}</Row>
            <Row label="Owner">{agent.owner}</Row>
          </div>
          <Button
            variant="primary"
            className="mt-4 w-full"
            onClick={() => {
              if (agent.status === "eval_failing") {
                setPublishBlockedOpen(true);
              } else {
                setPublishOpen(true);
              }
            }}
          >
            <Rocket className="size-4" /> Publish {agent.activeVersion}
          </Button>
          <Button variant="outline" className="mt-2 w-full" onClick={() => setSaveOpen(true)}>Save as new version</Button>
        </SectionCard>
        <SectionCard title="Spend controls" action={saveBtn(spendDirty, () => updateAgentConfig(agent.id, { spend: { monthlyCapUsd: parseNum(monthlyCap, base.spend.monthlyCapUsd), maxConcurrency: parseNum(concurrency, base.spend.maxConcurrency), timeoutSec: parseNum(timeout, base.spend.timeoutSec) } }))}>
          <div className="space-y-4">
            <Field label="Monthly spend cap (USD)"><Input value={monthlyCap} onChange={(e) => setMonthlyCap(e.target.value)} inputMode="numeric" /></Field>
            <Field label={<Term id="concurrency">Max concurrency</Term>}><Input value={concurrency} onChange={(e) => setConcurrency(e.target.value)} inputMode="numeric" /></Field>
            <Field label="Per-request timeout (seconds)"><Input value={timeout} onChange={(e) => setTimeoutSec(e.target.value)} inputMode="numeric" /></Field>
          </div>
        </SectionCard>
        <SectionCard title="Danger zone">
          <div className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-sm font-medium text-ink">{agent.status === "archived" ? "Restore agent" : "Archive agent"}</p>
                <p className="text-xs text-muted">{agent.status === "archived" ? "Make this agent active again." : "Hide from active lists. Reversible."}</p>
              </div>
              <Button variant="outline" size="sm" onClick={() => updateAgent(agent.id, { status: agent.status === "archived" ? "draft" : "archived" })}>
                {agent.status === "archived" ? "Restore" : "Archive"}
              </Button>
            </div>
            <Separator />
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-sm font-medium text-ink">Delete agent</p>
                <p className="text-xs text-muted">Remove the agent and all versions. Cannot be undone.</p>
              </div>
              <Button variant="danger" size="sm" onClick={() => setDeleteOpen(true)}>Delete</Button>
            </div>
          </div>
        </SectionCard>
      </aside>

      <ConfirmDialog
        open={publishOpen}
        onClose={() => setPublishOpen(false)}
        onConfirm={() => toast("Published")}
        title="Publish version"
        confirmLabel="Publish"
        tone="primary"
        body={<>Publish {agent.activeVersion}? This pins the endpoint to this version.</>}
      />
      <SaveVersionDialog agent={agent} open={saveOpen} onClose={() => setSaveOpen(false)} />
      <RunEvalDialog agent={agent} open={runEvalOpen} onClose={() => setRunEvalOpen(false)} />
      <PublishBlockedDialog
        agent={agent}
        open={publishBlockedOpen}
        onClose={() => setPublishBlockedOpen(false)}
        onRunEval={() => { setPublishBlockedOpen(false); setRunEvalOpen(true); }}
      />
      <ConfirmDialog
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        onConfirm={() => { deleteAgent(agent.id); router.push("/agents"); }}
        title="Delete agent"
        confirmLabel="Delete"
        body={<>Delete <span className="font-medium text-ink">{agent.name}</span> and all its versions? This cannot be undone.</>}
      />
    </div>
  );
}

/* ================================================================= Knowledge */
function Knowledge({ agent }: { agent: ReturnType<typeof getAgent> & {} }) {
  const { can } = useAuth();
  const principal = usePrincipal();
  const manage = canManage(agent.accessPolicy, principal);
  const canEdit = can("agent.edit") && manage;
  const [attachOpen, setAttachOpen] = React.useState(false);
  const kbs = agent.knowledgeBases.map(getKb).filter(Boolean) as NonNullable<ReturnType<typeof getKb>>[];
  const base = resolveConfig(agent).retrieval;
  const [injection, setInjection] = React.useState<InjectionStrategy>(base.injection);
  const [topK, setTopK] = React.useState(String(base.topK));
  const [threshold, setThreshold] = React.useState(String(base.similarityThreshold));
  const [reranker, setReranker] = React.useState<Reranker>(base.reranker);
  const [maxInjected, setMaxInjected] = React.useState(String(base.maxInjectedTokens));
  const [hybrid, setHybrid] = React.useState(base.hybrid);
  const retrievalDirty = injection !== base.injection || parseNum(topK, base.topK) !== base.topK || parseNum(threshold, base.similarityThreshold) !== base.similarityThreshold || reranker !== base.reranker || parseNum(maxInjected, base.maxInjectedTokens) !== base.maxInjectedTokens || hybrid !== base.hybrid;
  function detach(id: string) {
    updateAgent(agent.id, { knowledgeBases: agent.knowledgeBases.filter((k) => k !== id) }, false);
    toast("Knowledge base detached", "neutral");
  }
  return (
    <div className="space-y-6">
      {kbs.length > 0 && (
        <SectionCard title="Retrieval configuration" desc="How this version retrieves context. Changing the embedding model triggers a re-eval." action={<GateButton permission="agent.edit" variant="primary" size="sm" disabled={!retrievalDirty || !manage} title={manage ? undefined : "Restricted by this agent's access policy"} onClick={() => updateAgentConfig(agent.id, { retrieval: { injection, topK: parseNum(topK, base.topK), similarityThreshold: parseNum(threshold, base.similarityThreshold), reranker, maxInjectedTokens: parseNum(maxInjected, base.maxInjectedTokens), hybrid } })}>Save changes</GateButton>}>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <Field label="Injection strategy"><Select value={injection} onChange={(e) => setInjection(e.target.value as InjectionStrategy)}><option value="tool">Retrieval as a tool</option><option value="stuff">Always-on context</option></Select></Field>
            <Field label={<Term id="top-k">Top-k</Term>}><Input value={topK} onChange={(e) => setTopK(e.target.value)} inputMode="numeric" /></Field>
            <Field label={<Term id="similarity-threshold">Similarity threshold</Term>}><Input value={threshold} onChange={(e) => setThreshold(e.target.value)} inputMode="decimal" /></Field>
            <Field label={<Term id="reranker">Reranker</Term>}><Select value={reranker} onChange={(e) => setReranker(e.target.value as Reranker)}><option value="bge">bge-reranker-v2 (local)</option><option value="none">None</option></Select></Field>
            <Field label={<>Max injected context <Term id="token">tokens</Term></>}><Input value={maxInjected} onChange={(e) => setMaxInjected(e.target.value)} inputMode="numeric" /></Field>
            <button type="button" onClick={() => setHybrid((v) => !v)} className="flex items-center justify-between rounded-lg border border-border bg-elevated px-3 py-2 text-left"><span className="text-sm text-ink-2"><Term id="hybrid-search">Hybrid (BM25 + vector)</Term></span><Toggle on={hybrid} /></button>
          </div>
        </SectionCard>
      )}
      <SectionCard title="Bound knowledge bases" action={<GateButton permission="agent.edit" disabled={!manage} title={manage ? undefined : "Restricted by this agent's access policy"} variant="outline" size="sm" onClick={() => setAttachOpen(true)}><Plus className="size-4" /> Attach knowledge base</GateButton>}>
        {kbs.length === 0 ? (
          <EmptyState icon={<Database className="size-6" />} title="No knowledge bases bound" hint="Attach a knowledge base to ground this agent's answers in your documents." />
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            {kbs.map((kb) => (
              <div key={kb.id} className="relative rounded-xl border border-border bg-elevated p-4 transition-colors hover:border-border-strong">
                {canEdit && <button onClick={() => detach(kb.id)} aria-label="Detach" className="absolute right-3 top-3 text-muted hover:text-critical"><X className="size-4" /></button>}
                <Link href={`/knowledge/${kb.id}`} className="block pr-6">
                  <span className="flex items-center gap-2 font-medium text-ink"><Database className="size-4 text-muted" />{kb.name}</span>
                  <p className="mt-2 font-mono text-xs text-muted">{kb.embeddingModel}</p>
                  <div className="mt-3 flex gap-4 text-xs text-ink-2">
                    <span>{num(kb.docs, { compact: true })} docs</span><span>{num(kb.chunks, { compact: true })} chunks</span><span>{kb.sizeMB} MB</span>
                  </div>
                </Link>
                <div className="mt-3"><Badge tone={kb.status === "ready" ? "good" : kb.status === "indexing" ? "accent" : "critical"}>{kb.status}</Badge></div>
              </div>
            ))}
          </div>
        )}
      </SectionCard>
      <AttachKbDialog agent={agent} open={attachOpen} onClose={() => setAttachOpen(false)} />
    </div>
  );
}

function AttachKbDialog({ agent, open, onClose }: { agent: Agent; open: boolean; onClose: () => void }) {
  const principal = usePrincipal();
  const kbs = useKnowledgeBases().filter((kb) => canUse(kb.accessPolicy, principal));
  const [selected, setSelected] = React.useState<string[]>(agent.knowledgeBases);
  React.useEffect(() => { if (open) setSelected(agent.knowledgeBases); }, [open, agent.knowledgeBases]);
  function toggle(id: string) { setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id])); }
  function submit() { updateAgent(agent.id, { knowledgeBases: selected }, false); toast("Knowledge bases updated"); onClose(); }
  return (
    <Dialog
      open={open} onClose={onClose} title="Attach knowledge bases"
      description="Bound knowledge grounds this agent with retrieval and citations."
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" onClick={submit}>Update</Button></>}
    >
      <div className="divide-y divide-border">
        {kbs.length === 0 && <p className="py-6 text-center text-xs text-muted">No knowledge bases you can use in this workspace.</p>}
        {kbs.map((kb) => (
          <button key={kb.id} type="button" onClick={() => toggle(kb.id)} className="flex w-full items-center justify-between gap-3 py-3 text-left">
            <div className="flex items-center gap-3">
              <Database className="size-4 text-muted" />
              <div><p className="text-sm font-medium text-ink">{kb.name}</p><p className="text-xs text-muted">{num(kb.docs, { compact: true })} docs · {kb.embeddingModel}</p></div>
            </div>
            <Toggle on={selected.includes(kb.id)} />
          </button>
        ))}
      </div>
    </Dialog>
  );
}

/* ================================================================= Tools/MCP */
function ToolsTab({ agent }: { agent: ReturnType<typeof getAgent> & {} }) {
  const { can } = useAuth();
  const principal = usePrincipal();
  const manage = canManage(agent.accessPolicy, principal);
  const canEdit = can("agent.edit") && manage;
  const manageTitle = manage ? undefined : "Restricted by this agent's access policy";
  const mcpServers = useMcpServers().filter((s) => s.status === "connected" && canUse(s.accessPolicy, principal));
  const [toolsOpen, setToolsOpen] = React.useState(false);
  return (
    <div className="space-y-6">
      <SectionCard title="Bound tools" desc="Tools this agent can call. Schemas are snapshotted into the version for reproducibility." action={<GateButton permission="agent.edit" disabled={!manage} title={manageTitle} variant="outline" size="sm" onClick={() => setToolsOpen(true)}>Add tool</GateButton>}>
        {agent.tools.length === 0 ? (
          <EmptyState icon={<Wrench className="size-6" />} title="No tools bound" hint="Attach HTTP tools, MCP tools or sub-agents to extend this agent." />
        ) : (
          <div className="divide-y divide-border">
            {agent.tools.map((t) => (
              <div key={t} className="flex items-center justify-between py-3">
                <div className="flex items-center gap-3">
                  {t.startsWith("subagent") ? <Blocks className="size-4 text-s7" /> : <Wrench className="size-4 text-muted" />}
                  <div>
                    <p className="font-mono text-sm text-ink">{t}</p>
                    <p className="text-xs text-muted">{t.startsWith("subagent") ? "Sub-agent delegation" : "HTTP tool"}</p>
                  </div>
                </div>
                {canEdit && <button onClick={() => { updateAgent(agent.id, { tools: agent.tools.filter((x) => x !== t) }, false); toast("Tool removed", "neutral"); }} aria-label="Remove tool" className="text-muted transition-colors hover:text-critical"><X className="size-4" /></button>}
              </div>
            ))}
          </div>
        )}
      </SectionCard>
      <SectionCard title="Available MCP servers" desc="Connect external systems. Select which discovered tools to expose.">
        <div className="grid gap-4 md:grid-cols-2">
          {mcpServers.length === 0 && <p className="text-xs text-muted">No connected MCP servers you can use in this workspace.</p>}
          {mcpServers.map((s) => (
            <div key={s.id} className="rounded-xl border border-border bg-elevated p-4">
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-2 font-medium text-ink"><Plug className="size-4 text-muted" />{s.name}</span>
                <Badge tone={s.transport === "http" ? "accent" : "neutral"}>{s.transport}</Badge>
              </div>
              <p className="mt-2 text-xs text-muted">{s.toolCount} tools discovered, {s.auth}</p>
              <GateButton permission="agent.edit" disabled={!manage} title={manageTitle} variant="outline" size="sm" className="mt-3" onClick={() => setToolsOpen(true)}>Bind tools</GateButton>
            </div>
          ))}
        </div>
      </SectionCard>

      <ToolsDialog agent={agent} open={toolsOpen} onClose={() => setToolsOpen(false)} />
    </div>
  );
}

/* ================================================================= Guardrails */
function Guardrails({ agent }: { agent: ReturnType<typeof getAgent> & {} }) {
  const principal = usePrincipal();
  const manage = canManage(agent.accessPolicy, principal);
  const base = resolveConfig(agent).guardrails;
  const [input, setInput] = React.useState<GuardrailStage[]>(base.input);
  const [output, setOutput] = React.useState<GuardrailStage[]>(base.output);
  const sameOn = (a: GuardrailStage[], b: GuardrailStage[]) => a.length === b.length && a.every((s, i) => s.on === b[i]?.on);
  const dirty = !sameOn(input, base.input) || !sameOn(output, base.output);
  const toggle = (set: React.Dispatch<React.SetStateAction<GuardrailStage[]>>, i: number) =>
    set((arr) => arr.map((s, idx) => (idx === i ? { ...s, on: !s.on } : s)));
  const StageRow = ({ s, onToggle }: { s: GuardrailStage; onToggle: () => void }) => (
    <button type="button" onClick={onToggle} className="flex w-full items-center justify-between py-3 text-left">
      <div><p className="text-sm font-medium text-ink">{s.name}</p><p className="text-xs text-muted">{s.desc}</p></div>
      <Toggle on={s.on} />
    </button>
  );
  return (
    <div className="space-y-6">
      <Card>
        <CardContent className="flex flex-wrap items-center gap-2 py-4 text-xs font-medium">
          {["Request", "Input guardrails", "Model", "Output guardrails", "Client"].map((step, i, arr) => (
            <React.Fragment key={step}>
              <span className={`rounded-lg px-3 py-1.5 ${step === "Model" ? "bg-accent-soft text-accent" : "bg-elevated text-ink-2"}`}>{step}</span>
              {i < arr.length - 1 && <ChevronRight className="size-4 text-muted" />}
            </React.Fragment>
          ))}
        </CardContent>
      </Card>
      <div className="flex justify-end">
        {/* @backend PUT /v1/agents/:id/guardrails - per-version guardrail config [via updateAgentConfig] */}
        <GateButton permission="agent.edit" variant="primary" size="sm" disabled={!dirty || !manage} title={manage ? undefined : "Restricted by this agent's access policy"} onClick={() => updateAgentConfig(agent.id, { guardrails: { input, output } })}>Save changes</GateButton>
      </div>
      <div className="grid gap-6 lg:grid-cols-2">
        <SectionCard title="Input stages"><div className="divide-y divide-border">{input.map((s, i) => <StageRow key={s.name} s={s} onToggle={() => toggle(setInput, i)} />)}</div></SectionCard>
        <SectionCard title="Output stages"><div className="divide-y divide-border">{output.map((s, i) => <StageRow key={s.name} s={s} onToggle={() => toggle(setOutput, i)} />)}</div></SectionCard>
      </div>
      <p className="text-xs text-muted">Guardrail changes save to the draft config. They are frozen into the version snapshot on publish, which requires a fresh passing eval.</p>
    </div>
  );
}

/* ================================================================= Access */
function AccessTab({ agent, canManage }: { agent: ReturnType<typeof getAgent> & {}; canManage: boolean }) {
  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <div className="space-y-4 lg:col-span-2">
        <SectionCard
          title="Agent access policy"
          desc="Who can invoke this agent, and who can edit or publish it. By role and IAM team."
        >
          <AccessPolicyEditor kind="agent" id={agent.id} policy={agent.accessPolicy} readOnly={!canManage} />
        </SectionCard>
      </div>
      <div className="space-y-4">
        <AccessNote variant="both" />
        {!canManage && (
          <div className="flex items-start gap-2 rounded-lg border border-border bg-elevated/60 px-3 py-2.5 text-xs leading-relaxed text-ink-2">
            <ShieldCheck className="mt-0.5 size-3.5 shrink-0 text-accent" />
            <span>Only workspace admins can change access policies. Ask an admin in this workspace if this agent needs different roles or teams.</span>
          </div>
        )}
        <div className="rounded-lg border border-border bg-elevated/60 px-3 py-2.5 text-xs leading-relaxed text-ink-2">
          <p className="mb-1 font-medium text-ink">At run time</p>
          A caller must satisfy this policy on top of holding a token whose tenant and scopes cover the agent&apos;s
          bound tools and knowledge. Preview the effect in the Playground &mdash; open it from the header, then use Run context &rarr; Provide token.
        </div>
      </div>
    </div>
  );
}

/* ================================================================= Versions */
function Versions({ agent, versions }: { agent: ReturnType<typeof getAgent> & {}; versions: ReturnType<typeof getVersions> }) {
  return (
    <SectionCard title="Version history" desc="Every version is an immutable snapshot. A published endpoint always runs exactly one pinned version." action={<Button variant="outline" size="sm" onClick={() => toast("Version compare (coming soon)", "neutral")}><GitBranch className="size-4" /> Compare</Button>}>
      <ol className="relative space-y-6 before:absolute before:left-[7px] before:top-1 before:h-full before:w-px before:bg-border">
        {versions.map((v) => {
          const active = v.label === agent.activeVersion;
          return (
            <li key={v.label} className="relative pl-7">
              <span className={`absolute left-0 top-1 size-3.5 rounded-full border-2 ${active ? "border-accent bg-accent" : "border-border-strong bg-surface"}`} />
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-semibold text-ink">{v.label}</span>
                {active && <Badge tone="accent">Active</Badge>}
                <Badge tone="neutral">{v.status}</Badge>
                {v.evalVerdict !== "none" && <Badge tone={verdictTone[v.evalVerdict]}>{verdictLabel[v.evalVerdict]}</Badge>}
                <span className="font-mono text-xs text-muted">{v.model}</span>
              </div>
              <p className="mt-1 text-sm text-ink-2">{v.note}</p>
              <p className="mt-1 text-xs text-muted">{v.createdBy} · {dateShort(v.createdAt)}</p>
              <div className="mt-2 flex gap-2">
                {!active && <Button variant="outline" size="sm" onClick={() => { updateAgent(agent.id, { activeVersion: v.label }, false); toast(`Active version set to ${v.label}`); }}>Roll back to {v.label}</Button>}
                <Button variant="ghost" size="sm" onClick={() => toast(`Opening eval for ${v.label}`, "neutral")}>View eval</Button>
              </div>
            </li>
          );
        })}
      </ol>
    </SectionCard>
  );
}

/* ================================================================= Evals */
function EvalRunDetail({ run }: { run: EvalRun }) {
  const hasDetail = !!run.reason || !!run.dimensions?.length || !!run.failingCases?.length;
  return (
    <div className="space-y-3">
      {run.reason && <p className="max-w-[80ch] text-xs leading-relaxed text-ink-2">{run.reason}</p>}
      {run.dimensions && run.dimensions.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-[11px] font-medium uppercase tracking-wide text-muted">Dimensions vs baseline</p>
          {run.dimensions.map((d) => {
            const delta = Math.round((d.score - d.baseline) * 10) / 10;
            const regressed = delta < 0;
            return (
              <div key={d.name} className="flex items-center gap-3 text-xs">
                <span className="w-28 shrink-0 truncate text-ink-2" title={d.name}>{d.name}</span>
                <Progress value={d.score} tone={regressed ? "critical" : "good"} className="max-w-xs flex-1" />
                <span className="w-20 shrink-0 text-right tnum text-muted">{d.score} / {d.baseline}</span>
                <span className={`w-12 shrink-0 text-right tnum ${regressed ? "text-critical" : "text-good"}`}>{delta > 0 ? "+" : ""}{delta}</span>
              </div>
            );
          })}
        </div>
      )}
      {run.failingCases && run.failingCases.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-[11px] font-medium uppercase tracking-wide text-muted">Example failing cases</p>
          <ul className="space-y-1.5">
            {run.failingCases.map((c, i) => (
              <li key={i} className="rounded-lg border border-border bg-surface px-3 py-2 text-xs">
                <p className="font-medium text-ink-2">{c.input}</p>
                <p className="mt-0.5 text-muted">{c.issue}</p>
              </li>
            ))}
          </ul>
        </div>
      )}
      {!hasDetail && <p className="text-xs text-muted">No additional detail for this run.</p>}
    </div>
  );
}

function Evals({ agent }: { agent: ReturnType<typeof getAgent> & {} }) {
  const evals = useEvalRunsForAgent(agent.id);
  const [runEvalOpen, setRunEvalOpen] = React.useState(false);
  const [expandedId, setExpandedId] = React.useState<string | null>(null);
  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-3">
        <SectionCard title={<Term id="eval-pass">Eval pass rate</Term>}><p className="text-3xl font-semibold tnum text-ink">{pct(agent.evalPass)}</p></SectionCard>
        <SectionCard title={<Term id="baseline">Baseline</Term>}><p className="text-sm text-ink-2">Frozen at <span className="font-medium text-ink">{agent.activeVersion === "v7" ? "v6" : "previous version"}</span></p><Button variant="outline" size="sm" className="mt-3" onClick={() => toast(`Baseline re-frozen at ${agent.activeVersion}`)}>Re-freeze baseline</Button></SectionCard>
        <SectionCard title={<Term id="drift">Drift</Term>}><Badge tone={driftTone[agent.drift]}>{agent.drift}</Badge><p className="mt-2 text-xs text-muted">Model, prompt or knowledge changes trigger a re-eval.</p></SectionCard>
      </div>
      <SectionCard title="Eval runs" action={<GateButton permission="eval.run" variant="primary" size="sm" onClick={() => setRunEvalOpen(true)}><FlaskConical className="size-4" /> Run eval</GateButton>}>
        {evals.length === 0 ? <EmptyState icon={<FlaskConical className="size-6" />} title="No eval runs yet" hint="Create an eval suite and run it to establish a baseline." /> : (
          <Table>
            <THead><TR><TH>Run</TH><TH>Version</TH><TH>Trigger</TH><TH className="text-right">Quality</TH><TH className="text-right">Cost Δ</TH><TH className="text-right">Latency Δ</TH><TH>Verdict</TH><TH>Date</TH></TR></THead>
            <tbody>
              {evals.map((e) => {
                const open = expandedId === e.id;
                return (
                  <React.Fragment key={e.id}>
                    <TR className="cursor-pointer" onClick={() => setExpandedId(open ? null : e.id)}>
                      <TD className="font-mono text-xs text-muted">
                        <span className="inline-flex items-center gap-1.5">
                          {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}{e.id}
                        </span>
                      </TD>
                      <TD className="font-mono text-xs text-ink-2">{e.version} vs {e.baseline}</TD>
                      <TD><Badge tone="neutral">{e.trigger}</Badge></TD>
                      <TD className="text-right tnum font-medium text-ink">{e.quality.toFixed(1)}</TD>
                      <TD className={`text-right tnum ${e.costDelta > 0 ? "text-critical" : "text-good"}`}>{e.costDelta > 0 ? "+" : ""}{e.costDelta}%</TD>
                      <TD className={`text-right tnum ${e.latencyDelta > 0 ? "text-critical" : "text-good"}`}>{e.latencyDelta > 0 ? "+" : ""}{e.latencyDelta}ms</TD>
                      <TD><Badge tone={verdictTone[e.verdict]}>{verdictLabel[e.verdict]}</Badge></TD>
                      <TD className="whitespace-nowrap text-xs text-muted">{relative(e.date)}</TD>
                    </TR>
                    {open && (
                      <tr className="border-b border-border/70 bg-elevated/30">
                        <td colSpan={8} className="px-4 py-4"><EvalRunDetail run={e} /></td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </Table>
        )}
      </SectionCard>

      <RunEvalDialog agent={agent} open={runEvalOpen} onClose={() => setRunEvalOpen(false)} />
    </div>
  );
}

/* ================================================================= Publish */
function Publish({ agent }: { agent: ReturnType<typeof getAgent> & {} }) {
  const [fmt, setFmt] = React.useState("curl");
  const tokens = useTokens().filter((t) => t.agent === agent.name);
  const channels = useChannelsForAgent(agent.id);
  const [createOpen, setCreateOpen] = React.useState(false);
  const [revokeId, setRevokeId] = React.useState<string | null>(null);
  const [runEvalOpen, setRunEvalOpen] = React.useState(false);
  const endpoint = agent.endpoint ?? agent.id;
  const url = `https://catalyst.internal/v1/agents/${endpoint}/invoke`;
  const snippets: Record<string, string> = {
    curl: `curl -N ${url} \\\n  -H "Authorization: Bearer $AGENT_CATALYST_TOKEN" \\\n  -H "Content-Type: application/json" \\\n  -d '{"messages":[{"role":"user","content":"Hello"}],"stream":true}'`,
    ts: `const res = await fetch("${url}", {\n  method: "POST",\n  headers: {\n    Authorization: \`Bearer \${process.env.AGENT_CATALYST_TOKEN}\`,\n    "Content-Type": "application/json",\n  },\n  body: JSON.stringify({ messages: [{ role: "user", content: "Hello" }], stream: true }),\n});`,
    python: `import os, requests\nres = requests.post(\n    "${url}",\n    headers={"Authorization": f"Bearer {os.environ['AGENT_CATALYST_TOKEN']}"},\n    json={"messages": [{"role": "user", "content": "Hello"}], "stream": True},\n)`,
  };

  if (agent.status === "draft") {
    return (
      <div className="space-y-4">
        <SectionCard title="Not published yet" desc="Publishing generates an authenticated endpoint and export snippets. First you need a passing eval.">
          <EvalExplainer agent={agent} />
        </SectionCard>
      </div>
    );
  }
  if (agent.status === "eval_failing") {
    return (
      <div className="space-y-4">
        <SectionCard title="Publishing is blocked" desc="The active version failed its latest eval, so the endpoint is serving the previous version.">
          <EvalFailureSummary agent={agent} onRunEval={() => setRunEvalOpen(true)} />
        </SectionCard>
        <RunEvalDialog agent={agent} open={runEvalOpen} onClose={() => setRunEvalOpen(false)} />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <SectionCard title="Published endpoint" action={<Badge tone="good"><StatusDot tone="good" />Live</Badge>}>
        <div className="flex items-center gap-2">
          <code className="flex-1 truncate rounded-lg border border-border bg-canvas px-3 py-2 font-mono text-xs text-ink-2">{url}</code>
          <CopyButton value={url} />
        </div>
        <div className="mt-4 grid gap-4 sm:grid-cols-3 text-sm">
          <div><p className="text-xs text-muted">Pinned version</p><p className="font-medium text-ink">{agent.activeVersion}</p></div>
          <div><p className="text-xs text-muted">Auth</p><p className="font-medium text-ink">Caller&apos;s Trellix IAM token</p></div>
          <div><p className="text-xs text-muted">Rate limit</p><p className="font-medium text-ink">60 req/min</p></div>
        </div>
      </SectionCard>

      <AccessNote variant="export" />
      <div className="rounded-lg border border-border bg-elevated/60 px-3 py-2.5 text-xs leading-relaxed text-ink-2">
        <p className="mb-1 font-medium text-ink">Access model</p>
        Callers authenticate with their own Trellix IAM token, usually a service account rather than a console
        user. At run time the agent can only reach tools and knowledge the caller&apos;s token and tenant allow, and
        only if this agent&apos;s access policy permits that caller. The LLM provider key stays the workspace&apos;s vaulted
        key and is never the caller&apos;s.
      </div>

      <SectionCard title="Reachable via channels" desc="Messaging-app channels that route to this agent. People chat with it from Slack, Teams or Google Chat; access still follows this agent's policy.">
        {channels.length === 0 ? (
          <p className="text-sm text-muted">Not connected to any channel. <Link href="/channels/new" className="text-accent hover:underline">Connect a channel</Link> to let people reach it from a chat app.</p>
        ) : (
          <ul className="space-y-2">
            {channels.map((c) => (
              <li key={c.id} className="flex items-center justify-between gap-3 rounded-lg border border-border bg-elevated px-3 py-2">
                <Link href={`/channels/${c.id}`} className="text-sm text-ink hover:underline">{c.name}</Link>
                <span className="flex items-center gap-2 text-xs text-muted">
                  <span className="capitalize">{c.platform}</span>
                  {c.defaultAgentId === agent.id && <Badge tone="accent">Default</Badge>}
                </span>
              </li>
            ))}
          </ul>
        )}
      </SectionCard>

      <SectionCard title="Integrate" desc="Generated from the OpenAPI spec. The platform token is scoped to this endpoint. Provider keys are never exposed." action={<Segmented options={[{ key: "curl", label: "cURL" }, { key: "ts", label: "TypeScript" }, { key: "python", label: "Python" }]} value={fmt} onChange={setFmt} />}>
        <div className="relative">
          <div className="absolute right-3 top-3 z-10"><CopyButton value={snippets[fmt]} /></div>
          <CodeBlock code={snippets[fmt]} />
        </div>
      </SectionCard>

      <SectionCard title="API tokens" desc="Tokens are shown once at creation and scoped to this endpoint." action={<GateButton permission="agent.publish" variant="primary" size="sm" onClick={() => setCreateOpen(true)}>Create token</GateButton>}>
        <Table>
          <THead><TR><TH>Name</TH><TH>Prefix</TH><TH>Created</TH><TH>Last used</TH><TH></TH></TR></THead>
          <tbody>
            {tokens.length === 0 ? (
              <TR><TD colSpan={5} className="py-6 text-center text-xs text-muted">No tokens yet. Create one to call this endpoint.</TD></TR>
            ) : (
              tokens.map((t) => (
                <TR key={t.id}>
                  <TD className="text-ink">{t.name}</TD>
                  <TD className="font-mono text-xs text-muted">{t.prefix}</TD>
                  <TD className="text-xs text-muted">{dateShort(t.created)}</TD>
                  <TD className="text-xs text-muted">{t.lastUsed === "-" ? "-" : relative(t.lastUsed)}</TD>
                  <TD className="text-right"><Button variant="ghost" size="sm" className="text-critical" onClick={() => setRevokeId(t.id)}>Revoke</Button></TD>
                </TR>
              ))
            )}
          </tbody>
        </Table>
      </SectionCard>

      <CreateTokenDialog agent={agent} open={createOpen} onClose={() => setCreateOpen(false)} />
      <ConfirmDialog
        open={revokeId !== null}
        onClose={() => setRevokeId(null)}
        onConfirm={() => { if (revokeId) revokeToken(revokeId); }}
        title="Revoke token"
        confirmLabel="Revoke"
        body="Revoking this token immediately breaks any integration using it. This cannot be undone."
      />
    </div>
  );
}

/* ================================================================= Dialogs */
function RunEvalDialog({ agent, open, onClose }: { agent: Agent; open: boolean; onClose: () => void }) {
  const versions = getVersions(agent.id);
  const [baseline, setBaseline] = React.useState(versions[0]?.label ?? agent.activeVersion);
  const [suite, setSuite] = React.useState("Golden set");
  const [judge, setJudge] = React.useState("claude-opus-5-5");

  function submit() {
    const quality = Math.round((90 + (Math.random() * 12 - 6)) * 10) / 10;
    const qualityDelta = Math.round((Math.random() * 4 - 2) * 10) / 10;
    const costDelta = Math.round((Math.random() * 6 - 3) * 10) / 10;
    const latencyDelta = Math.round(Math.random() * 120 - 60);
    // Range spans below the low-confidence threshold (10 cases) so that verdict is actually reachable.
    const cases = Math.floor(6 + Math.random() * 60);
    const verdict = cases < 10 ? "low_confidence" : quality >= 90 ? "pass" : "fail";
    const reason =
      verdict === "fail"
        ? `Quality ${quality} fell below the 90% publish bar against ${baseline}.`
        : verdict === "low_confidence"
          ? `Only ${cases} cases in this run; add more for a reliable verdict.`
          : "All dimensions at or above baseline.";
    addEvalRun({
      id: genId("ev"), agentId: agent.id, agent: agent.name, version: agent.activeVersion,
      baseline, quality, qualityDelta, costDelta, latencyDelta, cases, verdict,
      date: nowIso(), trigger: "Manual run", reason,
    });
    onClose();
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Run evaluation"
      description={`Scores ${agent.activeVersion} against a frozen baseline with an LLM judge.`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={submit}><FlaskConical className="size-4" /> Run eval</Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Baseline version">
          <Select value={baseline} onChange={(e) => setBaseline(e.target.value)}>
            {versions.map((v) => <option key={v.label} value={v.label}>{v.label}</option>)}
          </Select>
        </Field>
        <Field label="Eval suite">
          <Select value={suite} onChange={(e) => setSuite(e.target.value)}>
            <option>Golden set</option><option>Regression set</option><option>Smoke set</option>
          </Select>
        </Field>
        <Field label="Judge model">
          <Select value={judge} onChange={(e) => setJudge(e.target.value)}>
            <option>claude-opus-5-5</option><option>claude-sonnet-5-5</option>
          </Select>
        </Field>
      </div>
    </Dialog>
  );
}

function SaveVersionDialog({ agent, open, onClose }: { agent: Agent; open: boolean; onClose: () => void }) {
  const [label, setLabel] = React.useState(`v${agent.versions + 1}`);
  const [note, setNote] = React.useState("");

  function submit() {
    toast("Draft version saved. Run an eval before publishing.");
    onClose();
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Save as new version"
      description="Creates an immutable draft snapshot. Publish only after a passing eval."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={submit}>Save version</Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Version label"><Input value={label} onChange={(e) => setLabel(e.target.value)} /></Field>
        <Field label="Change note"><Textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} placeholder="What changed in this version?" /></Field>
      </div>
    </Dialog>
  );
}

function ToolsDialog({ agent, open, onClose }: { agent: Agent; open: boolean; onClose: () => void }) {
  const principal = usePrincipal();
  const tools = useTools().filter((t) => canUse(t.accessPolicy, principal));
  const [selected, setSelected] = React.useState<string[]>(agent.tools);

  function toggle(name: string) {
    setSelected((s) => (s.includes(name) ? s.filter((x) => x !== name) : [...s, name]));
  }
  function submit() {
    updateAgent(agent.id, { tools: selected }, false);
    toast(`Updated tools for ${agent.name}`);
    onClose();
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Bind tools"
      description="Select which tools this agent can call. Schemas are snapshotted into the next version."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={submit}>Update tools</Button>
        </>
      }
    >
      <div className="divide-y divide-border">
        {tools.map((t) => (
          <button
            key={t.name}
            type="button"
            onClick={() => toggle(t.name)}
            className="flex w-full items-center justify-between gap-3 py-3 text-left"
          >
            <div className="flex items-center gap-3">
              {t.kind === "Sub-agent" ? <Blocks className="size-4 text-s7" /> : <Wrench className="size-4 text-muted" />}
              <div>
                <p className="font-mono text-sm text-ink">{t.name}</p>
                <p className="text-xs text-muted">{t.description}</p>
              </div>
            </div>
            <Toggle on={selected.includes(t.name)} />
          </button>
        ))}
      </div>
    </Dialog>
  );
}

function CreateTokenDialog({ agent, open, onClose }: { agent: Agent; open: boolean; onClose: () => void }) {
  const [name, setName] = React.useState("");
  const [expiry, setExpiry] = React.useState("none");
  const [full, setFull] = React.useState<string | null>(null);

  function close() {
    setName("");
    setExpiry("none");
    setFull(null);
    onClose();
  }
  function submit() {
    const f = `ac_live_${genId("")}${genId("")}`.replace(/-/g, "");
    addToken({
      id: genId("tok"),
      name: name.trim() || `${agent.endpoint ?? agent.id}-token`,
      agent: agent.name,
      prefix: f.slice(0, 14) + "…",
      created: nowIso(),
      lastUsed: "-",
      scopes: "invoke",
    });
    setFull(f);
  }

  return (
    <Dialog
      open={open}
      onClose={close}
      title="Create API token"
      description={full ? undefined : "Scoped to this endpoint with invoke permission."}
      footer={
        full ? (
          <Button variant="primary" onClick={close}>Done</Button>
        ) : (
          <>
            <Button variant="ghost" onClick={close}>Cancel</Button>
            <Button variant="primary" onClick={submit}>Create token</Button>
          </>
        )
      }
    >
      {full ? (
        <div className="space-y-3">
          <div className="flex items-start gap-2 rounded-lg border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
            <TriangleAlert className="size-4 shrink-0" />
            <span>Copy this token now. For security it is shown once and cannot be retrieved again.</span>
          </div>
          <div className="flex items-center gap-2">
            <code className="flex-1 truncate rounded-lg border border-border bg-canvas px-3 py-2 font-mono text-xs text-ink-2">{full}</code>
            <CopyButton value={full} />
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          <Field label="Token name"><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. prod-dashboard" /></Field>
          <Field label="Expiry">
            <Select value={expiry} onChange={(e) => setExpiry(e.target.value)}>
              <option value="none">No expiry</option><option value="30">30 days</option><option value="90">90 days</option>
            </Select>
          </Field>
        </div>
      )}
    </Dialog>
  );
}
