"use client";

import * as React from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  Archive, ArrowLeft, Blocks, Bot, ChevronDown, ChevronRight, Copy,
  Database, FlaskConical, GitBranch, History, Lock, MessageSquare, MoreHorizontal,
  Plug, Plus, Rocket, Settings2, Shield, ShieldCheck, Sparkles, Trash2, TriangleAlert,
  Wrench, X,
} from "lucide-react";
import {
  Badge, Button, Card, CardContent, CardHeader, CardTitle, CodeBlock, EmptyState,
  Field, Input, Select, Separator, StatusDot, Table, TD, TH, THead, TR, Textarea,
} from "@/components/ui";
import { Tabs, Segmented } from "@/components/tabs";
import { CopyButton } from "@/components/copy-button";
import { Dialog, ConfirmDialog } from "@/components/dialog";
import {
  getAgent, getKb, getModel, resolveConfig, parseNum,
  MODELS, driftTone, statusLabel, statusTone, verdictLabel, verdictTone,
  ASSERTION_KINDS, newRubric,
  type Agent, type EvalSuite, type EvalCaseDef, type Assertion, type AssertionKind, type RubricDimension,
  type ReasoningEffort, type OutputFormat, type TruncationStrategy, type InjectionStrategy, type Reranker, type GuardrailStage,
  type InputVariable, type InputVariableType,
} from "@/lib/data";
import {
  useActiveWorkspaceId, useAgent, useTokens, useTools, useKnowledgeBases, useMcpServers, useSkills, useEvalRunsForAgent,
  useChannelsForAgent, useSuitesForAgent, addEvalRun, simulateEvalRun, useEvalPolicyFor, addToken, revokeToken,
  addSuite, renameSuite, deleteSuite, upsertCase, deleteCase,
  updateAgent, updateAgentConfig, deleteAgent, duplicateAgent, toast, genId, nowIso,
  publishAgent, unpublishAgent, archiveAgent, restoreAgent, rollbackVersion, reFreezeBaseline,
  useEnsureAgent, useRemoteStatus, useIsLiveWorkspace, loadAgent, usePublishAdvisory, useEnsureTokens, mintTokenLive,
  useEnsureEvals, loadEvalRunDetail, loadEvalRuns, freezeBaseline, clearBaseline,
  versionsOf, saveLiveDraft, createVersion, useEditConflict, useAgentRevision, resolveEditConflict,
} from "@/lib/store";
import { LIVE_API } from "@/lib/api/client";
import { LiveStatus } from "@/components/live-status";
import { EvalRunDetail, LiveRunEvalDialog } from "@/components/eval-run-live";
import { useAuth, usePrincipal } from "@/lib/auth";
import { ORG } from "@/lib/org";
import { GateButton } from "@/components/gate";
import { Term } from "@/components/term";
import { EvalExplainer, EvalFailingBanner, NeedsReEvalBanner, ProfileOutdatedBanner, EvalFailureSummary, publishGateDecision, PublishGateDialogs, type PublishGate } from "@/components/eval-explainer";
import { canUse, canManage } from "@/lib/access-policy";
import { resolveToolRef, mcpRef } from "@/lib/tools";
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
 *  - @backend Header: POST /v1/agents/:id/duplicate (duplicateAgent); POST /v1/agents/:id/evals async run eval (addEvalRun); PATCH /v1/agents/:id publish/unpublish/archive/restore (publishAgent/unpublishAgent/archiveAgent/restoreAgent - guarded wrappers); DELETE /v1/agents/:id delete + cascade (deleteAgent)
 *  - @backend Configure: PATCH /v1/agents/:id - general (name/description/tags) + model config [via updateAgent/updateAgentConfig]; input variables persist to io_contract [via updateAgentConfig.inputVariables]; GET /v1/models - provider/model list (MODELS is local today). Lifecycle is NOT edited here - use the guarded Publish/Archive controls.
 *  - @backend Knowledge tab: PATCH /v1/agents/:id - bind/detach knowledgeBases [via updateAgent]; GET /v1/knowledge-bases picker [via useKnowledgeBases]
 *  - @backend Tools tab: PATCH /v1/agents/:id - bind/detach tools [via updateAgent]; GET /v1/tools picker [via useTools]; GET /v1/mcp-servers picker [via useMcpServers]
 *  - @backend Guardrails tab: GET/PUT /v1/agents/:id/guardrails - per-version config [persisted via updateAgentConfig.guardrails]
 *  - @backend Access tab: PUT /v1/agents/:id/access-policy - access policy [via setAccessPolicy]
 *  - @backend wired(026) Versions tab: versions from the live AgentOut embed [via versionsOf]; re-point = PATCH active_version_id by version id [via rollbackVersion]; "Save as new version" -> POST …/versions [via createVersion]. "Re-freeze baseline" is still a mock toast (US4).
 *  - @backend Playground moved to its own route (/playground); header "Open in Playground" deep-links there. Invoke contract lives on that page and in src/components/playground.tsx.
 *  - @backend wired(026) Evals tab: GET /api/v1/agents/:id/eval-runs + /eval-suites [via useEnsureEvals]; GET /api/v1/eval-runs/:id
 *    [loadEvalRunDetail]; POST /api/v1/agents/:id/eval-runs {version_id, suite_id} [LiveRunEvalDialog → runEvalLive];
 *    suite/case CRUD [addSuite/renameSuite/deleteSuite/upsertCase/deleteCase]; PUT/DELETE /api/v1/eval-suites/:sid/baseline
 *    [freezeBaseline/clearBaseline]. Mock mode keeps addEvalRun/simulateEvalRun.
 *  - @backend wired(026) Publish & Export: publish -> POST /api/v1/agents/:id/publish, advisory shown [via publishAgent/usePublishAdvisory];
 *    tokens -> GET/POST /api/v1/agents/:id/tokens + DELETE /api/v1/tokens/:id [via useEnsureTokens/mintTokenLive/revokeToken];
 *    the cURL targets ${NEXT_PUBLIC_PUBLIC_API_BASE || origin}/v1/agents/:id/invoke with `Bearer $AC_TOKEN` (D17). Mock agents keep the seeds.
 * See src/lib/store.ts markers + docs/backend-integration-map.md for the authoritative contracts.
 */
export default function AgentDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { can } = useAuth();
  const principal = usePrincipal();
  const agent = useAgent(params.id);
  const activeWorkspaceId = useActiveWorkspaceId();
  const evals = useEvalRunsForAgent(params.id);
  const policy = useEvalPolicyFor(agent?.workspaceId);
  const [runEvalOpen, setRunEvalOpen] = React.useState(false);
  const [gate, setGate] = React.useState<PublishGate | null>(null);
  const [deleteOpen, setDeleteOpen] = React.useState(false);
  // Live mode: a deep link / reload lands here before the list has loaded — fetch the one agent.
  useEnsureAgent(params.id);
  const remote = useRemoteStatus(`agent:${params.id}`);
  const discovery = useRemoteStatus("workspaces");
  const live = useIsLiveWorkspace();

  // Live cold load: until discovery has run (and, for a live workspace, until the one-agent fetch has run), a missing
  // agent means "not loaded yet", not "not found" — otherwise the page flashes "Agent not found" first.
  const pendingDiscovery = LIVE_API && (discovery.status === "idle" || discovery.status === "loading");
  const pendingAgent = live && (remote.status === "idle" || remote.status === "loading");
  if (!agent && (pendingDiscovery || pendingAgent)) {
    return (
      <div className="space-y-6">
        <Link href="/agents" className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-ink"><ArrowLeft className="size-4" /> Back to agents</Link>
        <EmptyState icon={<Bot className="size-6" />} title="Loading agent…" hint="Fetching this agent from the API." />
      </div>
    );
  }

  // Live: a transport/server failure is NOT "not found" — surface it with Retry. (404, and 403 which the API
  // deliberately makes indistinguishable from 404 — P5 no-leak, fall through to not-found below.)
  if (!agent && remote.status === "error" && remote.httpStatus !== 404 && remote.httpStatus !== 403) {
    return (
      <div className="space-y-6">
        <Link href="/agents" className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-ink"><ArrowLeft className="size-4" /> Back to agents</Link>
        <LiveStatus live status={remote} label="this agent" onRetry={() => void loadAgent(params.id, { force: true })} />
      </div>
    );
  }

  // Cross-workspace defense-in-depth: useAgent resolves by id across all workspaces, so treat an agent
  // belonging to another tenant as not-found (AppBoundary only gates the active workspace).
  // Live: a 404 from the API (or a 403 — no existence leak) also lands here.
  if (!agent || agent.workspaceId !== activeWorkspaceId) {
    return (
      <div className="space-y-6">
        <Link href="/agents" className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-ink"><ArrowLeft className="size-4" /> Back to agents</Link>
        <EmptyState icon={<TriangleAlert className="size-6" />} title="Agent not found" hint="This agent may have been archived or the link is out of date." />
      </div>
    );
  }

  const versions = versionsOf(agent);
  const model = getModel(agent.model);
  const archivedLive = live && agent.status === "archived";
  const naLive = "Not available yet for live agents";
  const manage = canManage(agent.accessPolicy, principal);
  const manageTitle = manage ? undefined : "Restricted by this agent's access policy";
  // needsReEval (config drift) gates publish; profileOutdated (model sunset) is informational. When both
  // apply on a draft, show only the gating one so two near-identical "run eval" banners don't stack.
  const showNeedsReEval = agent.status === "draft" && publishGateDecision(agent, evals[0]?.verdict, policy) === "needs_reeval";

  return (
    <div className="space-y-6">
      {live && (
        <LiveStatus live status={remote.status === "error" ? { status: "ready" } : remote} label="this agent"
          onRetry={() => void loadAgent(params.id, { force: true })}
          note="name, description, model, prompt, I/O contract, versions, re-point, archive/restore, publish (advisory), API tokens and eval suites / runs / baselines are live; bindings, guardrails, memory, spend and tags are not saved to the server yet" />
      )}
      <ConflictDialog agentId={agent.id} />
      <div>
        <Link href="/agents" className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-ink"><ArrowLeft className="size-4" /> Agents</Link>
        <div className="mt-3 flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-xl font-semibold tracking-tight text-ink">{agent.name}</h1>
              <Badge tone={statusTone[agent.status]}><StatusDot tone={statusTone[agent.status]} />{statusLabel[agent.status]}</Badge>
              <Badge tone="neutral">{agent.activeVersion} active</Badge>
              {agent.draftDirty && <Badge tone="warning" title="The saved draft differs from the active version — create a version to freeze it">Draft changed since {agent.activeVersion}</Badge>}
            </div>
            <p className="mt-1.5 max-w-2xl text-sm text-muted">{agent.description}</p>
          </div>
          <div className="flex items-center gap-2">
            <GateButton permission="agent.edit" variant="outline" disabled={live} title={live ? naLive : undefined} onClick={() => { const id = duplicateAgent(agent.id); if (id) router.push(`/agents/${id}`); }}><Copy className="size-4" /> Duplicate</GateButton>
            <GateButton permission="eval.run" variant="outline" onClick={() => setRunEvalOpen(true)}><FlaskConical className="size-4" /> Run eval</GateButton>
            {agent.status !== "archived" && (
              <GateButton permission="playground.use" variant="outline" href={`/playground?agent=${agent.id}`}><MessageSquare className="size-4" /> Open in Playground</GateButton>
            )}
            {agent.status === "published"
              ? <GateButton permission="agent.publish" disabled={!manage || live} title={live ? naLive : manageTitle} variant="outline" onClick={() => unpublishAgent(agent.id)}><Rocket className="size-4" /> Unpublish</GateButton>
              : (
                <GateButton
                  permission="agent.publish"
                  disabled={!manage}
                  title={manageTitle}
                  variant="primary"
                  onClick={() => setGate(live ? "ok" : publishGateDecision(agent, evals[0]?.verdict, policy))}
                >
                  <Rocket className="size-4" /> Publish {agent.activeVersion}
                </GateButton>
              )}
            <HeaderMenu agent={agent} can={can} live={live} onDelete={() => setDeleteOpen(true)} />
          </div>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-muted">
          <span className="inline-flex items-center gap-1.5"><Sparkles className="size-3.5" /> {agent.provider} <span className="font-mono text-ink-2">{agent.model}</span></span>
          <span>Owner <span className="text-ink-2">{agent.owner}</span></span>
          <span>Created {dateShort(agent.createdAt)}</span>
          <span>Updated {relative(agent.updatedAt)}</span>
        </div>
      </div>

      {archivedLive && (
        <div className="flex items-start gap-2 rounded-lg border border-border bg-elevated px-4 py-3 text-sm text-ink-2">
          <Archive className="mt-0.5 size-4 shrink-0 text-muted" />
          <p><span className="font-medium text-ink">This agent is archived — read-only.</span> The playground and its published endpoint refuse it. Restore it{agent.restoreStatus ? <> (back to <span className="font-medium">{statusLabel[agent.restoreStatus]}</span>)</> : null} from the ⋯ menu to edit or run it again.</p>
        </div>
      )}

      {live && (agent.restrictedBindings ?? 0) > 0 && (
        <p className="text-xs text-muted">This draft also binds {agent.restrictedBindings} resource(s) you don&apos;t have access to. They are kept when you save.</p>
      )}

      {agent.status === "eval_failing" && (
        <EvalFailingBanner agent={agent} onRunEval={() => setRunEvalOpen(true)} />
      )}

      {showNeedsReEval && (
        <NeedsReEvalBanner agent={agent} onRunEval={() => setRunEvalOpen(true)} />
      )}

      {/* @backend profileOutdated: a DISTINCT informational flag from the "outdated" view over the existing
          model_deprecation_scan detector (backend/08 /org/compliance) — NOT needsReEval, so it never gates publish.
          Suppressed when the re-eval banner already shows to avoid stacking. */}
      {agent.status !== "archived" && agent.profileOutdated && !showNeedsReEval && (
        <ProfileOutdatedBanner agent={agent} onRunEval={() => setRunEvalOpen(true)} />
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
          { key: "configure", label: "Configure", icon: <Settings2 className="size-4" />, content: <ConfigureKeyed agent={agent} model={model} /> },
          { key: "knowledge", label: "Knowledge", icon: <Database className="size-4" />, content: <><LiveBindingsNote agent={agent} kind="kb" /><Knowledge agent={agent} /></> },
          { key: "tools", label: "Tools & MCP", icon: <Plug className="size-4" />, content: <><LiveBindingsNote agent={agent} kind="tool" /><ToolsTab agent={agent} /></> },
          { key: "skills", label: "Skills", icon: <Sparkles className="size-4" />, content: <><LiveBindingsNote agent={agent} kind="skill" /><SkillsTab agent={agent} /></> },
          { key: "guardrails", label: "Guardrails", icon: <Shield className="size-4" />, content: <Guardrails agent={agent} /> },
          { key: "access", label: "Access", icon: <Lock className="size-4" />, content: <AccessTab agent={agent} canManage={can("access.manage")} /> },
          { key: "versions", label: "Versions", icon: <History className="size-4" />, content: <Versions agent={agent} versions={versions} /> },
          { key: "evals", label: "Evaluations", icon: <FlaskConical className="size-4" />, content: <Evals agent={agent} /> },
          { key: "publish", label: "Publish & Export", icon: <Rocket className="size-4" />, content: <Publish agent={agent} /> },
        ]}
      />

      <RunEvalDialog agent={agent} open={runEvalOpen} onClose={() => setRunEvalOpen(false)} />
      <PublishGateDialogs
        agent={agent}
        policy={policy}
        gate={gate}
        onClose={() => setGate(null)}
        onRunEval={() => { setGate(null); setRunEvalOpen(true); }}
        onConfirmPublish={() => publishAgent(agent.id)}
      />
      <ConfirmDialog
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        onConfirm={() => { if (deleteAgent(agent.id)) router.push("/agents"); }}
        title="Delete agent"
        confirmLabel="Delete"
        body={<>Delete <span className="font-medium text-ink">{agent.name}</span> and all its versions? This cannot be undone.</>}
      />
    </div>
  );
}

function HeaderMenu({ agent, can, live, onDelete }: { agent: Agent; can: (p: Permission) => boolean; live: boolean; onDelete: () => void }) {
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
            <button className={item} onClick={() => { setOpen(false); if (archived) { restoreAgent(agent.id); } else { archiveAgent(agent.id); } }}>
              <Archive className="size-4" /> {archived ? "Restore" : "Archive"}
            </button>
            <div className="my-1 h-px bg-border" />
            <button className={`${item} text-critical hover:text-critical disabled:cursor-not-allowed disabled:opacity-50`} disabled={live}
              title={live ? "Not available yet for live agents" : undefined} onClick={() => { setOpen(false); onDelete(); }}>
              <Trash2 className="size-4" /> Delete
            </button>
          </div>
        </>
      )}
    </div>
  );
}

/* ============================================================ live helpers */
/** 409 conflict.stale on a live save: someone else changed the agent since this tab loaded it (D13). */
function ConflictDialog({ agentId }: { agentId: string }) {
  const conflict = useEditConflict(agentId);
  const [busy, setBusy] = React.useState(false);
  const resolve = async (reapply: boolean) => {
    setBusy(true);
    try { await resolveEditConflict(agentId, reapply); } finally { setBusy(false); }
  };
  return (
    <Dialog
      open={!!conflict}
      onClose={() => void resolve(false)}
      title="Changed by someone else"
      description="This agent was changed since you opened it, so your save was not applied."
      footer={
        <>
          <Button variant="ghost" disabled={busy} onClick={() => void resolve(false)}>Reload, discard my edit</Button>
          <Button variant="primary" disabled={busy} onClick={() => void resolve(true)}>Reload and re-apply my edit</Button>
        </>
      }
    >
      <p className="text-sm text-ink-2">
        <span className="font-medium text-ink">Re-apply</span> loads the latest version and saves only the fields you just
        changed on top of it — the other person&apos;s changes to other fields are kept. <span className="font-medium text-ink">Discard</span> loads
        the latest version and drops your unsaved edit.
      </p>
    </Dialog>
  );
}

/** Configure re-initialises its form state from the server after a conflict reload (revision bump). */
function ConfigureKeyed(props: { agent: Agent; model: ReturnType<typeof getModel> }) {
  const revision = useAgentRevision(props.agent.id);
  return <Configure key={revision} {...props} />;
}

/** Live: bindings are authored in the server draft, but the knowledge/tools/skills families are still mock, so the
 *  console can't edit them yet — show what the server draft binds instead of an empty (misleading) list. */
function LiveBindingsNote({ agent, kind }: { agent: Agent; kind: "kb" | "tool" | "skill" }) {
  const live = useIsLiveWorkspace();
  if (!live || !agent.draft) return null;
  const ids = kind === "kb" ? agent.draft.kb_ids : kind === "tool" ? agent.draft.tool_ids : agent.draft.skill_ids;
  const noun = kind === "kb" ? "knowledge base" : kind;
  return (
    <div className="mb-4 rounded-lg border border-border bg-elevated px-4 py-3 text-xs text-ink-2">
      <p>
        <Badge tone="neutral">Live</Badge>{" "}
        The server draft binds <span className="font-medium text-ink">{ids.length}</span> {noun}{ids.length === 1 ? "" : "s"}.
        Editing bindings in the console is not available yet — they are carried into the next version as-is.
      </p>
      {ids.length > 0 && <p className="mt-1.5 break-all font-mono text-[11px] text-muted">{ids.join(", ")}</p>}
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
  const evals = useEvalRunsForAgent(agent.id);
  const policy = useEvalPolicyFor(agent.workspaceId);
  const [provider, setProvider] = React.useState<string>(agent.provider);
  const [modelId, setModelId] = React.useState<string>(agent.model);
  const [gate, setGate] = React.useState<PublishGate | null>(null);
  const [runEvalOpen, setRunEvalOpen] = React.useState(false);
  const [saveOpen, setSaveOpen] = React.useState(false);
  const [deleteOpen, setDeleteOpen] = React.useState(false);
  const router = useRouter();
  const live = useIsLiveWorkspace();
  const readOnly = live && agent.status === "archived";
  const naLive = "Not available yet for live agents";
  const [name, setName] = React.useState(agent.name);
  const [description, setDescription] = React.useState(agent.description);
  const [tags, setTags] = React.useState(agent.tags.join(", "));
  const parsedTags = [...new Set(tags.split(",").map((t) => t.trim()).filter(Boolean))];
  // Model params
  const [temperature, setTemperature] = React.useState(String(base.temperature));
  const [maxTokens, setMaxTokens] = React.useState(String(base.maxOutputTokens));
  // Live (review #9): a param the server never set renders the console default, so "unchanged text" can't tell an
  // untouched field from the user deliberately picking that default. Track a real touched flag instead.
  const [tempTouched, setTempTouched] = React.useState(false);
  const [maxTouched, setMaxTouched] = React.useState(false);
  const tempUnset = live && agent.draft?.model_params.temperature === undefined;
  const maxUnset = live && agent.draft?.model_params.max_tokens === undefined;
  const [reasoning, setReasoning] = React.useState<ReasoningEffort>(base.reasoningEffort);
  const [fallback, setFallback] = React.useState(base.fallbackModel);
  // System prompt
  const [systemPrompt, setSystemPrompt] = React.useState(base.systemPrompt);
  // I/O contract
  const [outputFormat, setOutputFormat] = React.useState<OutputFormat>(base.outputFormat);
  const [includeCitations, setIncludeCitations] = React.useState(base.includeCitations);
  const [inputVariables, setInputVariables] = React.useState<InputVariable[]>(base.inputVariables);
  // Context & memory (planned — captured, not enforced at runtime)
  const [truncation, setTruncation] = React.useState<TruncationStrategy>(base.truncationStrategy);
  const [sessionMemory, setSessionMemory] = React.useState(base.sessionMemory);
  const [maxTurns, setMaxTurns] = React.useState(String(base.maxTurnsRetained));
  const [longTerm, setLongTerm] = React.useState(base.longTermMemory);
  // Spend controls
  const [monthlyCap, setMonthlyCap] = React.useState(String(base.spend.monthlyCapUsd));
  const [concurrency, setConcurrency] = React.useState(String(base.spend.maxConcurrency));
  const [timeout, setTimeoutSec] = React.useState(String(base.spend.timeoutSec));


  const generalDirty = name !== agent.name || description !== agent.description || parsedTags.join("\u0000") !== agent.tags.join("\u0000");
  const modelDirty = provider !== agent.provider || modelId !== agent.model || parseNum(temperature, base.temperature) !== base.temperature || parseNum(maxTokens, base.maxOutputTokens) !== base.maxOutputTokens || (tempUnset && tempTouched) || (maxUnset && maxTouched) || reasoning !== base.reasoningEffort || fallback !== base.fallbackModel;
  const promptDirty = systemPrompt !== base.systemPrompt;
  const ioDirty = outputFormat !== base.outputFormat || includeCitations !== base.includeCitations || JSON.stringify(inputVariables) !== JSON.stringify(base.inputVariables);
  const memoryDirty = truncation !== base.truncationStrategy || sessionMemory !== base.sessionMemory || parseNum(maxTurns, base.maxTurnsRetained) !== base.maxTurnsRetained || longTerm !== base.longTermMemory;
  const spendDirty = parseNum(monthlyCap, base.spend.monthlyCapUsd) !== base.spend.monthlyCapUsd || parseNum(concurrency, base.spend.maxConcurrency) !== base.spend.maxConcurrency || parseNum(timeout, base.spend.timeoutSec) !== base.spend.timeoutSec;

  const curModel = getModel(modelId) ?? model;
  const saveBtn = (dirty: boolean, onClick: () => void, liveSupported = true) => (
    <GateButton permission="agent.edit" variant="primary" size="sm"
      disabled={!dirty || !manage || readOnly || (live && !liveSupported)}
      title={live && !liveSupported ? naLive : readOnly ? "Archived — restore to edit" : manageTitle} onClick={onClick}>Save changes</GateButton>
  );
  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <div className="space-y-6 lg:col-span-2">
        <SectionCard title="General" desc="Lifecycle is managed by the Publish and Archive controls — not edited here." action={saveBtn(generalDirty, () => updateAgent(agent.id, { name: name.trim() || agent.name, description, tags: parsedTags }))}>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Agent name"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
            <Field label="Tags" hint={live ? `${naLive} (no server field)` : "Comma-separated; used for filtering on the agents list"}>
              <Input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="security, triage, rag" disabled={live} />
            </Field>
          </div>
          <div className="mt-4"><Field label="Description"><Textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} /></Field></div>
          {parsedTags.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {parsedTags.map((t) => <Badge key={t} tone="neutral">{t}</Badge>)}
            </div>
          )}
        </SectionCard>

        <SectionCard title="Model" desc="Model-agnostic. Edits save to the agent's draft config; a provider or model swap should be re-evaluated before you publish." action={saveBtn(modelDirty, () => {
          if (live) {
            void saveLiveDraft(agent.id, {
              provider, model: modelId,
              ...(tempTouched ? { temperature: parseNum(temperature, base.temperature) } : {}),
              ...(maxTouched ? { maxOutputTokens: parseNum(maxTokens, base.maxOutputTokens) } : {}),
            });
            return;
          }
          updateAgent(agent.id, { provider: provider as Agent["provider"], model: modelId }, false); updateAgentConfig(agent.id, { temperature: parseNum(temperature, base.temperature), maxOutputTokens: parseNum(maxTokens, base.maxOutputTokens), reasoningEffort: reasoning, fallbackModel: fallback });
        })}>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Provider">
              <Select value={provider} onChange={(e) => { setProvider(e.target.value); const first = MODELS.find((m) => m.provider === e.target.value); if (first) setModelId(first.model); }}>{providers.map((p) => <option key={p}>{p}</option>)}</Select>
            </Field>
            <Field label="Model">
              <Select value={modelId} onChange={(e) => setModelId(e.target.value)}>{MODELS.filter((m) => m.provider === provider).map((m) => <option key={m.model}>{m.model}</option>)}</Select>
            </Field>
            <Field label={<Term id="temperature">Temperature</Term>} hint={live && agent.draft?.model_params.temperature === undefined ? "Not set on the server (provider default) — shown value is the console default" : undefined}><Input value={temperature} onChange={(e) => { setTemperature(e.target.value); setTempTouched(true); }} inputMode="decimal" /></Field>
            <Field label={<>Max output <Term id="token">tokens</Term></>} hint={live && agent.draft?.model_params.max_tokens === undefined ? "Not set on the server (provider default) — shown value is the console default" : undefined}><Input value={maxTokens} onChange={(e) => { setMaxTokens(e.target.value); setMaxTouched(true); }} inputMode="numeric" /></Field>
            <Field label={<Term id="reasoning-effort">Reasoning effort</Term>} hint={live ? naLive : undefined}>
              <Select value={reasoning} disabled={live} onChange={(e) => setReasoning(e.target.value as ReasoningEffort)}><option value="low">low</option><option value="medium">medium</option><option value="high">high</option><option value="xhigh">xhigh</option><option value="max">max</option></Select>
            </Field>
            <Field label="Fallback model" hint={live ? naLive : "Used on provider refusal or error"}>
              <Select value={fallback} disabled={live} onChange={(e) => setFallback(e.target.value)}>{MODELS.map((m) => <option key={m.model}>{m.model}</option>)}</Select>
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

        <SectionCard
          title="Input / output contract"
          desc="Typed inputs become the published API request schema."
          action={saveBtn(ioDirty, () => {
            const cleaned = inputVariables.filter((v) => v.name.trim()).map((v) => ({ ...v, name: v.name.trim() }));
            if (live) void saveLiveDraft(agent.id, { outputFormat, inputVariables: cleaned });
            else updateAgentConfig(agent.id, { outputFormat, includeCitations, inputVariables: cleaned });
            setInputVariables(cleaned);
          })}
        >
          <div className="mb-2 flex items-center justify-between">
            <p className="text-xs font-medium text-ink-2">Input variables</p>
            <GateButton permission="agent.edit" variant="ghost" size="sm" disabled={!manage} title={manageTitle}
              onClick={() => setInputVariables((arr) => [...arr, { name: "", type: "string", required: false }])}>
              <Plus className="size-3.5" /> Add variable
            </GateButton>
          </div>
          <Table>
            <THead><TR><TH>Name</TH><TH>Type</TH><TH>Required</TH><TH>Default</TH><TH>{""}</TH></TR></THead>
            <tbody>
              {inputVariables.length === 0 && (
                <TR><TD colSpan={5} className="text-muted">No input variables. The endpoint accepts a free-form body.</TD></TR>
              )}
              {inputVariables.map((v, i) => (
                <TR key={i}>
                  <TD><Input value={v.name} onChange={(e) => setInputVariables((arr) => arr.map((x, idx) => (idx === i ? { ...x, name: e.target.value } : x)))} placeholder="variable_name" className="font-mono text-xs" /></TD>
                  <TD>
                    <Select value={v.type} onChange={(e) => setInputVariables((arr) => arr.map((x, idx) => (idx === i ? { ...x, type: e.target.value as InputVariableType } : x)))}>
                      <option value="string">string</option><option value="number">number</option><option value="boolean">boolean</option><option value="enum">enum</option>
                    </Select>
                  </TD>
                  <TD>
                    <button type="button" aria-label="Toggle required" onClick={() => setInputVariables((arr) => arr.map((x, idx) => (idx === i ? { ...x, required: !x.required } : x)))}>
                      <Toggle on={v.required} />
                    </button>
                  </TD>
                  <TD><Input value={v.default ?? ""} onChange={(e) => setInputVariables((arr) => arr.map((x, idx) => (idx === i ? { ...x, default: e.target.value || undefined } : x)))} placeholder="-" /></TD>
                  <TD>
                    <button type="button" aria-label="Remove variable" className="text-muted hover:text-critical" onClick={() => setInputVariables((arr) => arr.filter((_, idx) => idx !== i))}>
                      <X className="size-4" />
                    </button>
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
          <p className="mt-2 text-xs text-muted">Typed inputs are validated at invoke and become part of the published OpenAPI request schema.</p>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <Field label="Output format"><Select value={outputFormat} onChange={(e) => setOutputFormat(e.target.value as OutputFormat)}><option value="text">Free text</option><option value="json">Structured output (JSON schema)</option></Select></Field>
            <button type="button" disabled={live} title={live ? naLive : undefined} onClick={() => setIncludeCitations((v) => !v)} className="flex items-center justify-between rounded-lg border border-border bg-elevated px-3 py-2 text-left disabled:cursor-not-allowed disabled:opacity-60">
              <span className="text-sm text-ink-2">Include citations</span><Toggle on={includeCitations} />
            </button>
          </div>
        </SectionCard>

        <SectionCard title="Context & memory" action={saveBtn(memoryDirty, () => updateAgentConfig(agent.id, { truncationStrategy: truncation, sessionMemory, maxTurnsRetained: parseNum(maxTurns, base.maxTurnsRetained), longTermMemory: longTerm }), false)}>
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
          <GateButton
            permission="agent.publish"
            variant="primary"
            className="mt-4 w-full"
            disabled={!manage || readOnly}
            title={readOnly ? "Archived — restore to publish" : manageTitle}
            onClick={() => setGate(live ? "ok" : publishGateDecision(agent, evals[0]?.verdict, policy))}
          >
            <Rocket className="size-4" /> Publish {agent.activeVersion}
          </GateButton>
          <GateButton permission="agent.edit" variant="outline" className="mt-2 w-full" disabled={!manage || readOnly}
            title={readOnly ? "Archived — restore to create a version" : manageTitle} onClick={() => setSaveOpen(true)}>Save as new version</GateButton>
        </SectionCard>
        <SectionCard title="Spend controls" action={saveBtn(spendDirty, () => updateAgentConfig(agent.id, { spend: { monthlyCapUsd: parseNum(monthlyCap, base.spend.monthlyCapUsd), maxConcurrency: parseNum(concurrency, base.spend.maxConcurrency), timeoutSec: parseNum(timeout, base.spend.timeoutSec) } }), false)}>
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
              <GateButton permission="agent.delete" variant="outline" size="sm" disabled={!manage} title={manageTitle} onClick={() => (agent.status === "archived" ? restoreAgent(agent.id) : archiveAgent(agent.id))}>
                {agent.status === "archived" ? "Restore" : "Archive"}
              </GateButton>
            </div>
            <Separator />
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-sm font-medium text-ink">Delete agent</p>
                <p className="text-xs text-muted">Remove the agent and all versions. Cannot be undone.</p>
              </div>
              <GateButton permission="agent.delete" variant="danger" size="sm" disabled={live || !manage} title={live ? naLive : manageTitle} onClick={() => setDeleteOpen(true)}>Delete</GateButton>
            </div>
          </div>
        </SectionCard>
      </aside>

      {/* keyed by the version count so the suggested next label resets after each create */}
      <SaveVersionDialog key={agent.versions} agent={agent} open={saveOpen} onClose={() => setSaveOpen(false)} />
      <RunEvalDialog agent={agent} open={runEvalOpen} onClose={() => setRunEvalOpen(false)} />
      <PublishGateDialogs
        agent={agent}
        policy={policy}
        gate={gate}
        onClose={() => setGate(null)}
        onRunEval={() => { setGate(null); setRunEvalOpen(true); }}
        onConfirmPublish={() => publishAgent(agent.id)}
      />
      <ConfirmDialog
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        onConfirm={() => { if (deleteAgent(agent.id)) router.push("/agents"); }}
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
  const live = useIsLiveWorkspace();
  const canEdit = can("agent.edit") && manage && !live;
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
        <SectionCard title="Retrieval configuration" desc="How this version retrieves context. Changing the embedding model triggers a re-eval." action={<GateButton permission="agent.edit" variant="primary" size="sm" disabled={!retrievalDirty || !manage || live} title={live ? "Not available yet for live agents" : manage ? undefined : "Restricted by this agent's access policy"} onClick={() => updateAgentConfig(agent.id, { retrieval: { injection, topK: parseNum(topK, base.topK), similarityThreshold: parseNum(threshold, base.similarityThreshold), reranker, maxInjectedTokens: parseNum(maxInjected, base.maxInjectedTokens), hybrid } })}>Save changes</GateButton>}>
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
  // Reset the picker to the agent's bound KBs whenever the dialog (re)opens — adjust-state-during-render
  // instead of a set-state-in-effect. Mirrors the previous effect's [open, agent.knowledgeBases] deps.
  const [resetKey, setResetKey] = React.useState({ open, kbs: agent.knowledgeBases });
  if (resetKey.open !== open || resetKey.kbs !== agent.knowledgeBases) {
    setResetKey({ open, kbs: agent.knowledgeBases });
    if (open) setSelected(agent.knowledgeBases);
  }
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
              <div>
                <p className="text-sm font-medium text-ink">{kb.name}</p>
                <p className="text-xs text-muted">{num(kb.docs, { compact: true })} docs · {kb.embeddingModel} · synced {relative(kb.lastSync)}</p>
                {kb.status !== "ready" && (
                  <p className={`mt-0.5 flex items-center gap-1 text-xs ${kb.status === "error" ? "text-critical" : "text-warning"}`}>
                    <TriangleAlert className="size-3 shrink-0" />{kb.status === "error" ? "Last ingestion failed" : "Indexing — not ready"}
                  </p>
                )}
              </div>
            </div>
            <Toggle on={selected.includes(kb.id)} />
          </button>
        ))}
      </div>
    </Dialog>
  );
}

/* ================================================================= Skills */
function SkillsTab({ agent }: { agent: ReturnType<typeof getAgent> & {} }) {
  const { can } = useAuth();
  const principal = usePrincipal();
  const manage = canManage(agent.accessPolicy, principal);
  const live = useIsLiveWorkspace();
  const canEdit = can("agent.edit") && manage && !live;
  const [attachOpen, setAttachOpen] = React.useState(false);
  const allSkills = useSkills();
  const boundIds = agent.skills ?? [];
  // Resolve bound ids to live skill objects; a deleted skill stays listed as a dangling ref (removable).
  const bound = boundIds.map((id) => ({ id, skill: allSkills.find((s) => s.id === id) }));
  function detach(id: string) {
    updateAgent(agent.id, { skills: boundIds.filter((s) => s !== id) }, false);
    toast("Skill detached", "neutral");
  }
  return (
    <div className="space-y-6">
      <SectionCard
        title="Bound skills"
        desc="Instruction playbooks injected into this agent. Editing a bound skill triggers a re-eval."
        action={<GateButton permission="agent.edit" disabled={!manage} title={manage ? undefined : "Restricted by this agent's access policy"} variant="outline" size="sm" onClick={() => setAttachOpen(true)}><Plus className="size-4" /> Attach skill</GateButton>}
      >
        {bound.length === 0 ? (
          <EmptyState icon={<Sparkles className="size-6" />} title="No skills bound" hint="Attach a skill to give this agent a reusable instruction playbook." />
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            {bound.map(({ id, skill }) => (
              <div key={id} className="relative rounded-xl border border-border bg-elevated p-4 transition-colors hover:border-border-strong">
                {canEdit && <button onClick={() => detach(id)} aria-label="Detach" className="absolute right-3 top-3 text-muted hover:text-critical"><X className="size-4" /></button>}
                {skill ? (
                  <Link href={`/skills/${skill.id}`} className="block pr-6">
                    <span className="flex items-center gap-2 font-medium text-ink"><Sparkles className="size-4 text-muted" />{skill.name}</span>
                    <p className="mt-2 line-clamp-2 text-xs text-muted">{skill.description}</p>
                    <div className="mt-3 flex items-center gap-2">
                      <Badge tone="neutral">{skill.version}</Badge>
                      {skill.signature && <Badge tone="good"><ShieldCheck className="size-3" /> signed</Badge>}
                      {skill.status === "deprecated" && <Badge tone="warning">deprecated</Badge>}
                    </div>
                  </Link>
                ) : (
                  <div className="pr-6">
                    <span className="flex items-center gap-2 font-medium text-ink"><Sparkles className="size-4 text-muted" />{id}</span>
                    <p className="mt-2 text-xs text-critical">Skill no longer available — remove this binding.</p>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </SectionCard>
      {/* key-remount so the dialog initializes `selected` from the current bindings without a setState effect */}
      <AttachSkillDialog key={String(attachOpen)} agent={agent} open={attachOpen} onClose={() => setAttachOpen(false)} />
    </div>
  );
}

function AttachSkillDialog({ agent, open, onClose }: { agent: Agent; open: boolean; onClose: () => void }) {
  const principal = usePrincipal();
  const skills = useSkills().filter((s) => canUse(s.accessPolicy, principal));
  // Initialized from props; the parent key-remounts this dialog on open, so no setState-in-effect sync.
  const [selected, setSelected] = React.useState<string[]>(agent.skills ?? []);
  function toggle(id: string) { setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id])); }
  function submit() { updateAgent(agent.id, { skills: selected }, false); toast("Skills updated"); onClose(); }
  return (
    <Dialog
      open={open} onClose={onClose} title="Attach skills"
      description="Only published (signed) skills can be bound; drafts are shown disabled."
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" onClick={submit}>Update</Button></>}
    >
      <div className="divide-y divide-border">
        {skills.length === 0 && <p className="py-6 text-center text-xs text-muted">No skills you can use in this workspace.</p>}
        {skills.map((sk) => {
          const isSelected = selected.includes(sk.id);
          // Only published skills are bindable, but an already-bound skill that was later deprecated/unpublished
          // must still be removable here — so only block toggling ON an unpublished, not-yet-bound skill.
          const blockBind = sk.status !== "published" && !isSelected;
          return (
            <button key={sk.id} type="button" disabled={blockBind} onClick={() => toggle(sk.id)}
              title={blockBind ? "Publish to bind — drafts can't be attached" : undefined}
              className="flex w-full items-center justify-between gap-3 py-3 text-left disabled:cursor-not-allowed disabled:opacity-55">
              <div className="flex items-center gap-3">
                <Sparkles className="size-4 text-muted" />
                <div>
                  <p className="text-sm font-medium text-ink">{sk.name}</p>
                  <p className="text-xs text-muted">{sk.status !== "published" ? (isSelected ? `${sk.status} — toggle off to unbind` : "Draft — publish to bind") : `${sk.version} · signed · updated ${relative(sk.lastUpdated)}`}</p>
                </div>
              </div>
              <Toggle on={isSelected} />
            </button>
          );
        })}
      </div>
    </Dialog>
  );
}

/* ================================================================= Tools/MCP */
function ToolsTab({ agent }: { agent: ReturnType<typeof getAgent> & {} }) {
  const { can } = useAuth();
  const principal = usePrincipal();
  const manage = canManage(agent.accessPolicy, principal);
  const live = useIsLiveWorkspace();
  const canEdit = can("agent.edit") && manage && !live;
  const manageTitle = manage ? undefined : "Restricted by this agent's access policy";
  // Full lists for resolving bound refs (so a ref to a paused/other server still shows its name, and
  // deleted ones render as dangling); the filtered list drives the "available to bind" section.
  const allTools = useTools();
  const allMcpServers = useMcpServers();
  const mcpServers = allMcpServers.filter((s) => s.status === "connected" && canUse(s.accessPolicy, principal));
  const [toolsOpen, setToolsOpen] = React.useState(false);

  const kindMeta = (r: ReturnType<typeof resolveToolRef>) => {
    if (r.missing) return { icon: <TriangleAlert className="size-4 text-critical" />, label: "Removed — no longer available" };
    if (r.kind === "MCP") return { icon: <Plug className="size-4 text-muted" />, label: `MCP · ${r.serverName}` };
    if (r.kind === "Sub-agent") return { icon: <Blocks className="size-4 text-s7" />, label: "Sub-agent delegation" };
    if (r.kind === "Built-in") return { icon: <Wrench className="size-4 text-muted" />, label: "Built-in tool" };
    return { icon: <Wrench className="size-4 text-muted" />, label: "HTTP tool" };
  };

  return (
    <div className="space-y-6">
      <SectionCard title="Bound tools" desc="Tools this agent can call. Schemas are snapshotted into the version for reproducibility." action={<GateButton permission="agent.edit" disabled={!manage} title={manageTitle} variant="outline" size="sm" onClick={() => setToolsOpen(true)}>Add tool</GateButton>}>
        {agent.tools.length === 0 ? (
          <EmptyState icon={<Wrench className="size-6" />} title="No tools bound" hint="Attach HTTP tools, MCP tools or sub-agents to extend this agent." />
        ) : (
          <div className="divide-y divide-border">
            {agent.tools.map((ref) => {
              const r = resolveToolRef(ref, { tools: allTools, mcpServers: allMcpServers });
              const meta = kindMeta(r);
              return (
                <div key={ref} className="flex items-center justify-between py-3">
                  <div className="flex items-center gap-3">
                    {meta.icon}
                    <div>
                      <p className="flex items-center gap-2 font-mono text-sm text-ink">{r.label}{r.perUser && <Badge tone="accent">Per-user</Badge>}</p>
                      <p className="text-xs text-muted">{meta.label}{r.riskTier && r.riskTier !== "read" ? ` · ${r.riskTier}` : ""}{r.perUser ? " · caller connects their own account" : ""}</p>
                    </div>
                  </div>
                  {canEdit && <button onClick={() => { updateAgent(agent.id, { tools: agent.tools.filter((x) => x !== ref) }, false); toast("Tool removed", "neutral"); }} aria-label="Remove tool" className="text-muted transition-colors hover:text-critical"><X className="size-4" /></button>}
                </div>
              );
            })}
          </div>
        )}
      </SectionCard>
      <SectionCard title="Available MCP servers" desc="Connect external systems. Bind which discovered tools to expose.">
        <div className="grid gap-4 md:grid-cols-2">
          {mcpServers.length === 0 && <p className="text-xs text-muted">No connected MCP servers you can use in this workspace.</p>}
          {mcpServers.map((s) => (
            <div key={s.id} className="rounded-xl border border-border bg-elevated p-4">
              <div className="flex items-center justify-between">
                <Link href={`/tools/mcp/${s.id}`} className="flex items-center gap-2 font-medium text-ink hover:underline"><Plug className="size-4 text-muted" />{s.name}</Link>
                <Badge tone={s.transport === "http" ? "accent" : "neutral"}>{s.transport}</Badge>
              </div>
              <p className="mt-2 text-xs text-muted">{s.tools.filter((t) => t.enabled).length} tools exposed · {s.toolCount} discovered</p>
              <GateButton permission="agent.edit" disabled={!manage} title={manageTitle} variant="outline" size="sm" className="mt-3" onClick={() => setToolsOpen(true)}>Bind tools</GateButton>
            </div>
          ))}
        </div>
      </SectionCard>

      <ToolsDialog key={toolsOpen ? agent.id : "closed"} agent={agent} open={toolsOpen} onClose={() => setToolsOpen(false)} />
    </div>
  );
}

/* ================================================================= Guardrails */
function Guardrails({ agent }: { agent: ReturnType<typeof getAgent> & {} }) {
  const principal = usePrincipal();
  const manage = canManage(agent.accessPolicy, principal);
  const live = useIsLiveWorkspace();
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
        <GateButton permission="agent.edit" variant="primary" size="sm" disabled={!dirty || !manage || live} title={live ? "Not available yet for live agents" : manage ? undefined : "Restricted by this agent's access policy"} onClick={() => updateAgentConfig(agent.id, { guardrails: { input, output } })}>Save changes</GateButton>
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
function Versions({ agent, versions }: { agent: ReturnType<typeof getAgent> & {}; versions: ReturnType<typeof versionsOf> }) {
  const principal = usePrincipal();
  const live = useIsLiveWorkspace();
  const manage = canManage(agent.accessPolicy, principal);
  const manageTitle = manage ? undefined : "Restricted by this agent's access policy";
  return (
    <SectionCard title="Version history" desc="Every version is an immutable snapshot. A published endpoint always runs exactly one pinned version." action={<Button variant="outline" size="sm" onClick={() => toast("Version compare (coming soon)", "neutral")}><GitBranch className="size-4" /> Compare</Button>}>
      <ol className="relative space-y-6 before:absolute before:left-[7px] before:top-1 before:h-full before:w-px before:bg-border">
        {versions.map((v) => {
          // Live: the server marks the active version (is_active); labels are display-only.
          const active = v.isActive ?? v.label === agent.activeVersion;
          return (
            <li key={v.id ?? v.label} className="relative pl-7">
              <span className={`absolute left-0 top-1 size-3.5 rounded-full border-2 ${active ? "border-accent bg-accent" : "border-border-strong bg-surface"}`} />
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-semibold text-ink">{v.label}</span>
                {active && <Badge tone="accent">Active</Badge>}
                {!v.id && <Badge tone="neutral">{v.status}</Badge>}
                {v.evalVerdict !== "none" && <Badge tone={verdictTone[v.evalVerdict]}>{verdictLabel[v.evalVerdict]}</Badge>}
                <span className="font-mono text-xs text-muted">{v.model}</span>
              </div>
              {v.note && <p className="mt-1 text-sm text-ink-2">{v.note}</p>}
              <p className="mt-1 text-xs text-muted">
                {v.createdBy || "unknown creator"} · {dateShort(v.createdAt)}
                {v.id && <> · <span className="font-mono" title={v.id}>{v.id.slice(0, 8)}</span></>}
              </p>
              <div className="mt-2 flex gap-2">
                {!active && <GateButton permission="agent.publish" variant="outline" size="sm" disabled={!manage} title={manageTitle} onClick={() => rollbackVersion(agent.id, v.label, v.id)}>{live ? "Make active" : "Roll back to"} {v.label}</GateButton>}
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
function Evals({ agent }: { agent: ReturnType<typeof getAgent> & {} }) {
  const live = useIsLiveWorkspace();
  useEnsureEvals(agent.id, live);
  const evals = useEvalRunsForAgent(agent.id);
  const evalRemote = useRemoteStatus(`evalRuns:${agent.id}`);
  const [runEvalOpen, setRunEvalOpen] = React.useState(false);
  const [expandedId, setExpandedId] = React.useState<string | null>(null);
  function toggle(id: string, open: boolean) {
    setExpandedId(open ? null : id);
    if (!open && live) void loadEvalRunDetail(id);
  }
  return (
    <div className="space-y-6">
      {live && <LiveStatus live status={evalRemote} label="eval runs" onRetry={() => void loadEvalRuns(agent.id, { force: true })} />}
      <div className="grid gap-4 sm:grid-cols-3">
        <SectionCard title={<Term id="eval-pass">Eval pass rate</Term>}><p className="text-3xl font-semibold tnum text-ink">{pct(agent.evalPass)}</p></SectionCard>
        {live ? <LiveBaselineCard agentId={agent.id} /> : <SectionCard title={<Term id="baseline">Baseline</Term>}><p className="text-sm text-ink-2">Frozen at <span className="font-medium text-ink">{agent.activeVersion === "v7" ? "v6" : "previous version"}</span></p><GateButton permission="eval.govern" variant="outline" size="sm" className="mt-3" onClick={() => reFreezeBaseline(agent.id)}>Re-freeze baseline</GateButton></SectionCard>}
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
                    <TR className="cursor-pointer" onClick={() => toggle(e.id, open)}>
                      <TD className="font-mono text-xs text-muted">
                        <span className="inline-flex items-center gap-1.5">
                          {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}{live ? e.id.slice(0, 8) : e.id}
                        </span>
                      </TD>
                      <TD className="font-mono text-xs text-ink-2">{e.version} vs {e.baseline}</TD>
                      <TD><Badge tone="neutral">{e.trigger}</Badge></TD>
                      <TD className="text-right tnum font-medium text-ink">{e.scored === false ? "—" : e.quality.toFixed(1)}</TD>
                      <TD className={`text-right tnum ${e.costDelta > 0 ? "text-critical" : "text-good"}`}>{e.costDelta > 0 ? "+" : ""}{e.costDelta}%</TD>
                      <TD className={`text-right tnum ${e.latencyDelta > 0 ? "text-critical" : "text-good"}`}>{e.latencyDelta > 0 ? "+" : ""}{e.latencyDelta}ms</TD>
                      <TD>{e.status === "failed" ? <Badge tone="critical">Failed</Badge> : <Badge tone={verdictTone[e.verdict]}>{verdictLabel[e.verdict]}</Badge>}</TD>
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

      <SuiteAuthoring agent={agent} />

      <RunEvalDialog agent={agent} open={runEvalOpen} onClose={() => setRunEvalOpen(false)} />
    </div>
  );
}

/** Live Baseline card (026 US4, D7/O2): the baseline is per SUITE — frozen pointer, else the active version's latest
 *  completed run, else none. Freeze/unfreeze are admin-only (eval.govern); a server 403 is toasted by the store. */
function LiveBaselineCard({ agentId }: { agentId: string }) {
  const suites = useSuitesForAgent(agentId);
  const evals = useEvalRunsForAgent(agentId);
  const [sid, setSid] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const suite = suites.find((x) => x.id === sid) ?? suites[0];
  const latest = suite ? evals.find((e) => e.suiteId === suite.id && e.status === "completed") : undefined;
  const b = suite?.baseline;
  async function act(fn: () => Promise<boolean>) { setBusy(true); try { await fn(); } finally { setBusy(false); } }
  return (
    <SectionCard title={<Term id="baseline">Baseline</Term>}>
      {!suite ? (
        <p className="text-sm text-ink-2">None yet — a baseline is a completed run of an eval suite.</p>
      ) : (
        <div className="space-y-2">
          {suites.length > 1 && (
            <Select value={suite.id} onChange={(e) => setSid(e.target.value)} aria-label="Suite">
              {suites.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
            </Select>
          )}
          <p className="text-sm text-ink-2">
            {b?.origin === "frozen" ? <>Frozen{b.frozenBy ? <> by <span className="font-medium text-ink">{b.frozenBy}</span></> : null}</>
              : b?.origin === "automatic" ? "Automatic — the active version's latest run"
              : "None yet — run the active version first"}
          </p>
          {b?.evalRunId && <p className="font-mono text-[11px] text-muted">run {b.evalRunId.slice(0, 8)}</p>}
          <div className="flex flex-wrap gap-2 pt-1">
            <GateButton permission="eval.govern" variant="outline" size="sm" disabled={busy || !latest || (b?.origin === "frozen" && b.evalRunId === latest?.id)}
              title={latest ? `Pin run ${latest.id.slice(0, 8)} (${latest.version})` : "No completed run of this suite yet"}
              onClick={() => latest && void act(() => freezeBaseline(suite.id, latest.id))}>Freeze latest run</GateButton>
            {b?.origin === "frozen" && (
              <GateButton permission="eval.govern" variant="ghost" size="sm" disabled={busy} onClick={() => void act(() => clearBaseline(suite.id))}>Unfreeze</GateButton>
            )}
          </div>
        </div>
      )}
    </SectionCard>
  );
}

/* ---- Eval suite / case authoring (eval.author) ---- */
function SuiteAuthoring({ agent }: { agent: ReturnType<typeof getAgent> & {} }) {
  const suites = useSuitesForAgent(agent.id);
  const policy = useEvalPolicyFor(agent.workspaceId);
  const { can } = useAuth();
  const canAuthor = can("eval.author");
  const [suiteDialog, setSuiteDialog] = React.useState<{ id?: string; name: string } | null>(null);
  const [caseEdit, setCaseEdit] = React.useState<{ suiteId: string; caseDef: EvalCaseDef | null } | null>(null);
  const [delSuite, setDelSuite] = React.useState<EvalSuite | null>(null);
  const [delCase, setDelCase] = React.useState<{ suiteId: string; c: EvalCaseDef } | null>(null);

  return (
    <SectionCard
      title="Eval suites"
      desc="Authored test cases the judge scores this agent against. A suite below the min-case floor yields a low-confidence verdict."
      action={<GateButton permission="eval.author" variant="outline" size="sm" onClick={() => setSuiteDialog({ name: "" })}><Plus className="size-4" /> New suite</GateButton>}
    >
      {suites.length === 0 ? (
        <EmptyState icon={<FlaskConical className="size-6" />} title="No eval suites yet" hint={canAuthor ? "Create a suite, then add cases with assertions and a rubric." : "No eval cases have been authored for this agent yet."} />
      ) : (
        <div className="space-y-4">
          {suites.map((s) => (
            <SuiteCard
              key={s.id} suite={s} canAuthor={canAuthor} minCases={policy.minCases}
              onRename={() => setSuiteDialog({ id: s.id, name: s.name })}
              onDelete={() => setDelSuite(s)}
              onAddCase={() => setCaseEdit({ suiteId: s.id, caseDef: null })}
              onEditCase={(c) => setCaseEdit({ suiteId: s.id, caseDef: c })}
              onDeleteCase={(c) => setDelCase({ suiteId: s.id, c })}
            />
          ))}
        </div>
      )}

      {suiteDialog && <SuiteNameDialog agentId={agent.id} initial={suiteDialog} onClose={() => setSuiteDialog(null)} />}
      {caseEdit && <CaseEditorDialog agent={agent} suiteId={caseEdit.suiteId} initial={caseEdit.caseDef} onClose={() => setCaseEdit(null)} />}
      <ConfirmDialog
        open={!!delSuite} onClose={() => setDelSuite(null)}
        onConfirm={() => { if (delSuite) deleteSuite(delSuite.id); }}
        title="Delete suite?" body={`"${delSuite?.name}" and its ${delSuite?.cases.length ?? 0} case(s) will be removed.`}
        confirmLabel="Delete suite" tone="danger"
      />
      <ConfirmDialog
        open={!!delCase} onClose={() => setDelCase(null)}
        onConfirm={() => { if (delCase) deleteCase(delCase.suiteId, delCase.c.id); }}
        title="Delete case?" body="This eval case will be removed from the suite." confirmLabel="Delete case" tone="danger"
      />
    </SectionCard>
  );
}

function SuiteCard({
  suite, canAuthor, minCases, onRename, onDelete, onAddCase, onEditCase, onDeleteCase,
}: {
  suite: EvalSuite; canAuthor: boolean; minCases: number;
  onRename: () => void; onDelete: () => void; onAddCase: () => void;
  onEditCase: (c: EvalCaseDef) => void; onDeleteCase: (c: EvalCaseDef) => void;
}) {
  const thin = suite.cases.length < minCases;
  return (
    <div className="rounded-lg border border-border bg-surface">
      <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <p className="truncate text-sm font-medium text-ink">{suite.name}</p>
            <Badge tone={thin ? "warning" : "good"}>{suite.cases.length} {suite.cases.length === 1 ? "case" : "cases"}</Badge>
            {thin && <span className="text-[11px] text-warning">below min {minCases}</span>}
          </div>
          <p className="mt-0.5 text-[11px] text-muted">
            Updated {relative(suite.updatedAt)}
            {suite.baseline !== undefined && <> · baseline {suite.baseline?.origin === "frozen" ? "frozen" : suite.baseline?.origin === "automatic" ? "automatic" : "none"}</>}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <GateButton permission="eval.author" variant="ghost" size="sm" onClick={onRename}>Rename</GateButton>
          <GateButton permission="eval.author" variant="ghost" size="sm" onClick={onDelete}><Trash2 className="size-4" /></GateButton>
          <GateButton permission="eval.author" variant="outline" size="sm" onClick={onAddCase}><Plus className="size-4" /> Add case</GateButton>
        </div>
      </div>
      {suite.cases.length === 0 ? (
        <p className="px-4 py-4 text-xs text-muted">No cases yet. {canAuthor ? "Add a case to start scoring this agent." : ""}</p>
      ) : (
        <ul className="divide-y divide-border/70">
          {suite.cases.map((c) => (
            <li key={c.id} className="flex items-start justify-between gap-3 px-4 py-2.5">
              <div className="min-w-0">
                <p className="truncate text-xs text-ink-2" title={c.input}>{c.input}</p>
                <div className="mt-1 flex flex-wrap items-center gap-1.5">
                  {c.assertions.map((a, i) => (
                    <Badge key={i} tone="neutral" className="font-normal">
                      {a.hard ? "hard" : "soft"}: {a.kind}
                    </Badge>
                  ))}
                  <span className="text-[11px] text-muted">{c.rubric.length} rubric dim{c.rubric.length === 1 ? "" : "s"}</span>
                  {c.expected && <span className="text-[11px] text-muted">· has reference</span>}
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-1.5">
                <GateButton permission="eval.author" variant="ghost" size="sm" onClick={() => onEditCase(c)}>Edit</GateButton>
                <GateButton permission="eval.author" variant="ghost" size="sm" onClick={() => onDeleteCase(c)}><Trash2 className="size-4" /></GateButton>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function SuiteNameDialog({ agentId, initial, onClose }: { agentId: string; initial: { id?: string; name: string }; onClose: () => void }) {
  const [name, setName] = React.useState(initial.name);
  function submit() {
    const trimmed = name.trim();
    if (!trimmed) return;
    if (initial.id) renameSuite(initial.id, trimmed);
    else addSuite(agentId, trimmed);
    onClose();
  }
  return (
    <Dialog
      open onClose={onClose}
      title={initial.id ? "Rename suite" : "New eval suite"}
      description={initial.id ? undefined : "A named set of cases for this agent (e.g. Golden set, Regression set)."}
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!name.trim()} onClick={submit}>{initial.id ? "Rename" : "Create suite"}</Button></>}
    >
      <Field label="Suite name"><Input value={name} autoFocus onChange={(e) => setName(e.target.value)} placeholder="Golden set" /></Field>
    </Dialog>
  );
}

/** Live (026 US4): the engine's deterministic layer only checks `required_fields`, and the rubric is capped at 8. */
const LIVE_MAX_RUBRIC = 8;
function CaseEditorDialog({ agent, suiteId, initial, onClose }: { agent: Agent; suiteId: string; initial: EvalCaseDef | null; onClose: () => void }) {
  const live = useIsLiveWorkspace();
  const inputVars = resolveConfig(agent).inputVariables;
  const [input, setInput] = React.useState(initial?.input ?? "");
  const [vars, setVars] = React.useState<Record<string, string>>(() => {
    const seed: Record<string, string> = {};
    for (const v of inputVars) seed[v.name] = initial?.vars?.[v.name] ?? v.default ?? "";
    return seed;
  });
  // Clone arrays so editing never aliases the stored case (seed cases share a rubric reference).
  const [assertions, setAssertions] = React.useState<Assertion[]>(() =>
    initial ? initial.assertions.filter((a) => !live || a.kind === "required_fields").map((a) => ({ ...a }))
      : live ? [] : [{ kind: "no_refusal", hard: true }]);
  const [rubric, setRubric] = React.useState<RubricDimension[]>(() => (initial ? initial.rubric.map((d) => ({ ...d })) : newRubric()));
  const [expected, setExpected] = React.useState(initial?.expected ?? "");

  // An assertion whose kind carries a detailHint (regex, required_fields, tool_called, exact) must supply
  // that detail — the backend contract rejects it otherwise, so gate save the same way.
  const assertionsValid = assertions.every((a) => {
    const needsDetail = (ASSERTION_KINDS.find((x) => x.kind === a.kind)?.detailHint ?? "") !== "";
    return !needsDetail || !!a.detail?.trim();
  });
  const liveValid = !live || (rubric.length <= LIVE_MAX_RUBRIC && assertions.every((a) => a.kind === "required_fields"));
  const valid = input.trim().length > 0 && assertionsValid && liveValid && rubric.length > 0 && rubric.every((d) => d.name.trim() && d.weight > 0);

  function submit() {
    if (!valid) return;
    // Only persist vars the agent actually declares, dropping blanks.
    // Vars the agent doesn't declare (e.g. an API-authored case) aren't editable here — carry them through unchanged
    // so saving never silently rewrites the server case (review #5).
    const declared = new Set(inputVars.map((v) => v.name));
    const keptVars: Record<string, string> = Object.fromEntries(
      Object.entries(initial?.vars ?? {}).filter(([k]) => !declared.has(k)));
    for (const v of inputVars) if (vars[v.name]?.trim()) keptVars[v.name] = vars[v.name].trim();
    const c: EvalCaseDef = {
      id: initial?.id ?? genId("case"),
      input: input.trim(),
      ...(Object.keys(keptVars).length ? { vars: keptVars } : {}),
      assertions: assertions.map((a) => ({ ...a, detail: a.detail?.trim() || undefined })),
      rubric: rubric.map((d) => ({ name: d.name.trim(), weight: d.weight })),
      ...(expected.trim() ? { expected: expected.trim() } : {}),
      // live: the server's original non-string values, so unedited fields round-trip exactly (adapters.restore)
      ...(initial?.raw ? { raw: initial.raw } : {}),
    };
    upsertCase(suiteId, c);   // live: the store toasts the server result (or its refusal)
    if (!live) toast(initial ? "Case updated" : "Case added");
    onClose();
  }

  return (
    <Dialog
      open onClose={onClose} size="lg"
      title={initial ? "Edit eval case" : "Add eval case"}
      description="A scenario the judge scores against the frozen baseline. Hard assertions fail the case outright."
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!valid} onClick={submit}>{initial ? "Save case" : "Add case"}</Button></>}
    >
      <div className="space-y-5">
        <Field label="Input message" hint="The user message / scenario this case sends.">
          <Textarea rows={3} value={input} onChange={(e) => setInput(e.target.value)} placeholder="Triage alert 48213: repeated failed logins from one host." />
        </Field>

        {inputVars.length > 0 && (
          <div className="space-y-2">
            <p className="text-xs font-medium text-ink-2">Input variables <span className="font-normal text-muted">(from this agent&apos;s I/O contract)</span></p>
            <div className="grid gap-3 sm:grid-cols-2">
              {inputVars.map((v) => (
                <Field key={v.name} label={`${v.name}${v.required ? " *" : ""}`} hint={v.type}>
                  {v.type === "boolean" ? (
                    <Select value={vars[v.name] ?? ""} onChange={(e) => setVars((p) => ({ ...p, [v.name]: e.target.value }))}>
                      <option value="">—</option><option value="true">true</option><option value="false">false</option>
                    </Select>
                  ) : (
                    <Input
                      value={vars[v.name] ?? ""} inputMode={v.type === "number" ? "numeric" : undefined}
                      onChange={(e) => setVars((p) => ({ ...p, [v.name]: e.target.value }))} placeholder={v.default ?? ""}
                    />
                  )}
                </Field>
              ))}
            </div>
          </div>
        )}

        <AssertionRows assertions={assertions} setAssertions={setAssertions} live={live} />
        <RubricRows rubric={rubric} setRubric={setRubric} max={live ? LIVE_MAX_RUBRIC : undefined} />

        <Field label="Reference answer" hint="Optional. The pairwise judge compares to the frozen baseline, so a golden answer isn't required.">
          <Textarea rows={2} value={expected} onChange={(e) => setExpected(e.target.value)} placeholder="(optional) expected answer sketch" />
        </Field>
      </div>
    </Dialog>
  );
}

function AssertionRows({ assertions, setAssertions, live = false }: { assertions: Assertion[]; setAssertions: React.Dispatch<React.SetStateAction<Assertion[]>>; live?: boolean }) {
  const update = (i: number, patch: Partial<Assertion>) => setAssertions((p) => p.map((a, j) => (j === i ? { ...a, ...patch } : a)));
  const detailHintFor = (k: AssertionKind) => ASSERTION_KINDS.find((x) => x.kind === k)?.detailHint ?? "";
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium text-ink-2">Deterministic assertions</p>
        <Button variant="ghost" size="sm" onClick={() => setAssertions((p) => [...p, live ? { kind: "required_fields", hard: true } : { kind: "no_refusal", hard: false }])}><Plus className="size-4" /> Add assertion</Button>
      </div>
      {live && <p className="text-[11px] text-muted">Only <span className="text-ink-2">Required fields</span> is checked by the eval engine today (always hard); the other kinds are not available yet.</p>}
      {assertions.length === 0 ? (
        <p className="text-[11px] text-muted">No assertions — this case is scored by the rubric alone.</p>
      ) : (
        <div className="space-y-2">
          {assertions.map((a, i) => {
            const hint = detailHintFor(a.kind);
            return (
              <div key={i} className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-elevated/40 px-2.5 py-2">
                <Select className="w-auto" value={a.kind} onChange={(e) => update(i, { kind: e.target.value as AssertionKind })}>
                  {ASSERTION_KINDS.map((x) => {
                    const off = live && x.kind !== "required_fields";
                    return <option key={x.kind} value={x.kind} disabled={off}>{x.label}{off ? " — not available yet" : ""}</option>;
                  })}
                </Select>
                {hint && <Input className={`min-w-40 flex-1 ${!a.detail?.trim() ? "border-warning/60" : ""}`} value={a.detail ?? ""} onChange={(e) => update(i, { detail: e.target.value })} placeholder={`${hint} (required)`} />}
                <button
                  type="button" disabled={live} onClick={() => update(i, { hard: !a.hard })}
                  className={`rounded-md border px-2.5 py-1 text-xs font-medium transition-colors ${a.hard ? "border-transparent bg-accent-soft text-accent" : "border-border bg-elevated text-ink-2"}`}
                  title={a.hard ? "Hard: failing this fails the case" : "Soft: informs the judge only"}
                >
                  {a.hard ? "Hard" : "Soft"}
                </button>
                <button type="button" onClick={() => setAssertions((p) => p.filter((_, j) => j !== i))} className="text-muted transition-colors hover:text-critical" aria-label="Remove assertion"><X className="size-4" /></button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function RubricRows({ rubric, setRubric, max }: { rubric: RubricDimension[]; setRubric: React.Dispatch<React.SetStateAction<RubricDimension[]>>; max?: number }) {
  const update = (i: number, patch: Partial<RubricDimension>) => setRubric((p) => p.map((d, j) => (j === i ? { ...d, ...patch } : d)));
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs font-medium text-ink-2">Judge rubric</p>
          <p className="text-[11px] text-muted">The judge scores each dimension 1–5; the weighted blend is the case quality.</p>
        </div>
        <Button variant="ghost" size="sm" disabled={max !== undefined && rubric.length >= max} onClick={() => setRubric((p) => [...p, { name: "", weight: 1 }])}><Plus className="size-4" /> Add dimension</Button>
      </div>
      {max !== undefined && rubric.length > max && <p className="text-[11px] text-warning">At most {max} dimensions — remove {rubric.length - max}.</p>}
      {rubric.length === 0 ? (
        <p className="text-[11px] text-warning">Add at least one rubric dimension.</p>
      ) : (
        <div className="space-y-2">
          {rubric.map((d, i) => (
            <div key={i} className="flex items-center gap-2 rounded-lg border border-border bg-elevated/40 px-2.5 py-2">
              <Input className="flex-1" value={d.name} onChange={(e) => update(i, { name: e.target.value })} placeholder="Accuracy" />
              <div className="flex items-center gap-1.5">
                <span className="text-[11px] text-muted">weight</span>
                <Input className="w-16 text-center" inputMode="numeric" value={String(d.weight)} onChange={(e) => update(i, { weight: Math.max(0, Number(e.target.value) || 0) })} />
              </div>
              <button type="button" onClick={() => setRubric((p) => p.filter((_, j) => j !== i))} className="text-muted transition-colors hover:text-critical" aria-label="Remove dimension"><X className="size-4" /></button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ================================================================= Publish */
const noopSubscribe = () => () => {};
/** Example value for an io_contract input variable (cURL body, D17). */
/** A value the published endpoint will ACCEPT for this input (026 US3 review #6): never NaN (→ `null` in JSON), never
 *  an empty enum. Enum → the default if it is an allowed value, else the first option. */
function exampleValue(v: InputVariable): unknown {
  const d = v.default !== undefined && v.default !== "" ? v.default : undefined;
  switch (v.type) {
    case "number": { const n = d === undefined ? NaN : Number(d); return Number.isFinite(n) ? n : 1; }
    case "boolean": return d === undefined ? true : d === "true";
    case "enum": return d !== undefined && (!v.options || v.options.includes(d)) ? d : v.options?.[0] ?? d ?? "";
    default: return d ?? "Hello";
  }
}
/** JSON value → a Python literal (review #4): a regex over JSON text also rewrote `true`/`null` INSIDE strings. */
function toPython(v: unknown): string {
  if (v === null || v === undefined) return "None";
  if (typeof v === "boolean") return v ? "True" : "False";
  if (typeof v === "number") return Number.isFinite(v) ? String(v) : "None";
  if (typeof v === "string") return JSON.stringify(v); // a JSON string literal is a valid Python str literal
  if (Array.isArray(v)) return `[${v.map(toPython).join(", ")}]`;
  return `{${Object.entries(v as Record<string, unknown>).map(([k, x]) => `${JSON.stringify(k)}: ${toPython(x)}`).join(", ")}}`;
}
const ADVISORY_TEXT: Record<string, { label: string; tone: "good" | "critical" | "warning" }> = {
  pass: { label: "Eval passing", tone: "good" },
  fail: { label: "Eval failing", tone: "critical" },
  low_confidence: { label: "Low confidence", tone: "warning" },
};
function PublishAdvisoryCard({ agentId }: { agentId: string }) {
  const adv = usePublishAdvisory(agentId);
  if (!adv) return null;
  const t = ADVISORY_TEXT[adv.verdict] ?? ADVISORY_TEXT.low_confidence;
  return (
    <SectionCard title="Publish advisory" desc={`From the last publish of ${adv.versionLabel} in this session. Advisory only: the verdict never blocks publishing.`}
      action={<Badge tone={t.tone}><StatusDot tone={t.tone} />{t.label}</Badge>}>
      <div className="grid gap-3 text-sm sm:grid-cols-3">
        <div><p className="text-xs text-muted">Reason</p><p className="font-mono text-xs text-ink">{adv.reason_code}</p></div>
        {adv.eval_run_id ? (
          <>
            <div><p className="text-xs text-muted">Eval run</p><p className="font-mono text-xs text-ink" title={adv.eval_run_id}>{adv.eval_run_id.slice(0, 8)}</p></div>
            <div><p className="text-xs text-muted">Suite</p><p className="font-mono text-xs text-ink" title={adv.suite_id ?? ""}>{adv.suite_id?.slice(0, 8)}</p></div>
          </>
        ) : (
          <p className="text-xs text-muted sm:col-span-2">No completed eval run exists for this version. Run an eval to get a real verdict.</p>
        )}
      </div>
    </SectionCard>
  );
}

function Publish({ agent }: { agent: ReturnType<typeof getAgent> & {} }) {
  const [fmt, setFmt] = React.useState("curl");
  const live = useIsLiveWorkspace();
  const { can } = useAuth();
  // GET tokens is agent.publish server-side: a builder never fetches (it would only 403) - FR-020.
  useEnsureTokens(agent.id, live && can("agent.publish"));
  const tokens = useTokens().filter((t) => (live ? t.agentId === agent.id : t.agent === agent.name));
  const channels = useChannelsForAgent(agent.id);
  const [createOpen, setCreateOpen] = React.useState(false);
  const [revokeId, setRevokeId] = React.useState<string | null>(null);
  const [runEvalOpen, setRunEvalOpen] = React.useState(false);
  const origin = React.useSyncExternalStore(noopSubscribe, () => window.location.origin, () => "");
  const endpoint = agent.endpoint ?? agent.id;
  // D17: the published plane is /v1/agents/{id}/invoke on the public API base; the token is NEVER interpolated.
  const url = live
    ? `${process.env.NEXT_PUBLIC_PUBLIC_API_BASE || origin}/v1/agents/${agent.id}/invoke`
    : `https://agent-platform.internal/v1/agents/${endpoint}/invoke`;
  const tokenVar = live ? "AC_TOKEN" : "AGENT_PLATFORM_TOKEN";
  const activeVars = versionsOf(agent).find((v) => v.isActive)?.inputVariables ?? [];
  const body: Record<string, unknown> = live
    ? { inputs: Object.fromEntries(activeVars.map((v) => [v.name, exampleValue(v)])) }
    : { messages: [{ role: "user", content: "Hello" }], stream: true };
  const bodyJson = JSON.stringify(body);
  const pyJson = toPython(body);
  // review #5: the body sits inside a single-quoted shell string — a `'` in a default value would end it.
  const shellJson = bodyJson.replace(/'/g, "'\\''");
  const snippets: Record<string, string> = {
    curl: `curl -N ${url} \\\n  -H "Authorization: Bearer $${tokenVar}" \\\n  -H "Content-Type: application/json" \\\n  -d '${shellJson}'`,
    ts: `const res = await fetch("${url}", {\n  method: "POST",\n  headers: {\n    Authorization: \`Bearer \${process.env.${tokenVar}}\`,\n    "Content-Type": "application/json",\n  },\n  body: JSON.stringify(${bodyJson}),\n});`,
    python: `import os, requests\nres = requests.post(\n    "${url}",\n    headers={"Authorization": f"Bearer {os.environ['${tokenVar}']}"},\n    json=${pyJson},\n    stream=True,\n)`,
  };

  if (live && agent.status === "draft") {
    return (
      <div className="space-y-4">
        <PublishAdvisoryCard agentId={agent.id} />
        <SectionCard title="Not published yet" desc="Publish the active version from the header. The latest completed eval of that version is shown as an advisory verdict and never blocks publishing.">
          <p className="text-sm text-muted">Once published, this tab shows the endpoint, a cURL snippet and the API tokens that can call it.</p>
        </SectionCard>
      </div>
    );
  }
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
      {live && <PublishAdvisoryCard agentId={agent.id} />}
      <SectionCard title="Published endpoint" action={agent.status === "archived" ? <Badge tone="neutral">Archived: calls return 404</Badge> : <Badge tone="good"><StatusDot tone="good" />Live</Badge>}>
        <div className="flex items-center gap-2">
          <code className="flex-1 truncate rounded-lg border border-border bg-canvas px-3 py-2 font-mono text-xs text-ink-2">{url}</code>
          <CopyButton value={url} />
        </div>
        <div className="mt-4 grid gap-4 sm:grid-cols-3 text-sm">
          <div><p className="text-xs text-muted">Pinned version</p><p className="font-medium text-ink">{agent.activeVersion}</p></div>
          <div><p className="text-xs text-muted">Auth</p><p className="font-medium text-ink">{live ? "API token (Bearer)" : <>Caller&apos;s {ORG.idpName} token</>}</p></div>
          <div><p className="text-xs text-muted">Rate limit</p><p className="font-medium text-ink">{live ? "Workspace policy" : "60 req/min"}</p></div>
        </div>
      </SectionCard>

      <AccessNote variant="export" />
      <div className="rounded-lg border border-border bg-elevated/60 px-3 py-2.5 text-xs leading-relaxed text-ink-2">
        <p className="mb-1 font-medium text-ink">Access model</p>
        Callers authenticate with their own {ORG.idpName} token, usually a service account rather than a console
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
          <THead><TR><TH>Name</TH><TH>Prefix</TH><TH>Created</TH><TH>Expires</TH><TH>Last used</TH><TH></TH></TR></THead>
          <tbody>
            {tokens.length === 0 ? (
              <TR><TD colSpan={6} className="py-6 text-center text-xs text-muted">No tokens yet. Create one to call this endpoint.</TD></TR>
            ) : (
              tokens.map((t) => (
                <TR key={t.id}>
                  <TD className="text-ink">{t.name}</TD>
                  <TD className="font-mono text-xs text-muted">{t.prefix}</TD>
                  <TD className="text-xs text-muted">{dateShort(t.created)}</TD>
                  <TD className="text-xs text-muted">{t.expires ? dateShort(t.expires) : "-"}</TD>
                  <TD className="text-xs text-muted">{!t.lastUsed || t.lastUsed === "-" ? "Never" : relative(t.lastUsed)}</TD>
                  <TD className="text-right"><GateButton permission="agent.publish" variant="ghost" size="sm" className="text-critical" onClick={() => setRevokeId(t.id)}>Revoke</GateButton></TD>
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
        body={live ? "Revoking cuts open streams within about 2 seconds and every later call gets 401. This cannot be undone." : "Revoking this token immediately breaks any integration using it. This cannot be undone."}
      />
    </div>
  );
}

/* ================================================================= Dialogs */
/** Live vs mock run surface. Live (026 US4) sends ONLY {version_id, suite_id}: the server picks the baseline (no
 *  picker) and applies the workspace policy, then returns the persisted run, which is shown inline. */
function RunEvalDialog(props: { agent: Agent; open: boolean; onClose: () => void }) {
  const live = useIsLiveWorkspace();
  return live ? <LiveRunEvalDialog {...props} /> : <MockRunEvalDialog {...props} />;
}

function MockRunEvalDialog({ agent, open, onClose }: { agent: Agent; open: boolean; onClose: () => void }) {
  const versions = versionsOf(agent);
  const policy = useEvalPolicyFor(agent.workspaceId);
  const suites = useSuitesForAgent(agent.id);
  const [baseline, setBaseline] = React.useState(versions[0]?.label ?? agent.activeVersion);
  const [suiteId, setSuiteId] = React.useState("");
  const selectedSuite = suites.find((s) => s.id === suiteId) ?? suites[0];
  const thinSuite = !!selectedSuite && selectedSuite.cases.length < policy.minCases;

  function submit() {
    addEvalRun(simulateEvalRun({ agent, version: agent.activeVersion, baseline, suite: selectedSuite, qualityThreshold: policy.qualityThreshold, policy }));
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
        <Field label="Eval suite" hint={thinSuite ? `Below the ${policy.minCases}-case floor — this run will read as low-confidence.` : undefined}>
          {suites.length === 0 ? (
            <p className="rounded-md border border-border bg-elevated/40 px-3 py-2 text-xs text-muted">
              No suites authored yet. Add one under <span className="text-ink-2">Eval suites</span> below; this run uses an ad-hoc sample.
            </p>
          ) : (
            <Select value={selectedSuite?.id ?? ""} onChange={(e) => setSuiteId(e.target.value)}>
              {suites.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.cases.length} cases)</option>)}
            </Select>
          )}
        </Field>
        <Field label="Judge model" hint="Fixed & versioned so the judge can't drift.">
          <div className="flex h-9 items-center gap-2 rounded-md border border-border bg-elevated px-3 text-sm">
            <span className="font-mono text-ink">claude-haiku-4-5</span>
            <Badge tone="neutral">judge-v1</Badge>
          </div>
        </Field>
      </div>
    </Dialog>
  );
}

function SaveVersionDialog({ agent, open, onClose }: { agent: Agent; open: boolean; onClose: () => void }) {
  const live = useIsLiveWorkspace();
  const [label, setLabel] = React.useState(`v${agent.versions + 1}`);
  const [note, setNote] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  async function submit() {
    if (!live) {
      toast("Draft version saved. Run an eval before publishing.");
      onClose();
      return;
    }
    setBusy(true);
    try {
      // A refusal (e.g. 404 detail.binding — a bound KB was deleted) is toasted with its distinct message by the store.
      if (await createVersion(agent.id, label)) { setNote(""); onClose(); }
    } finally { setBusy(false); }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Save as new version"
      description={live
        ? "Freezes the saved draft (config + bindings) into an immutable version. It is not made active until you re-point to it."
        : "Creates an immutable draft snapshot. Publish only after a passing eval."}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button variant="primary" onClick={() => void submit()} disabled={busy}>{busy ? "Saving…" : "Save version"}</Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Version label"><Input value={label} onChange={(e) => setLabel(e.target.value)} /></Field>
        <Field label="Change note" hint={live ? "Not available yet for live agents (no server field)" : undefined}>
          <Textarea rows={3} value={note} disabled={live} onChange={(e) => setNote(e.target.value)} placeholder="What changed in this version?" />
        </Field>
      </div>
    </Dialog>
  );
}

function ToolsDialog({ agent, open, onClose }: { agent: Agent; open: boolean; onClose: () => void }) {
  const principal = usePrincipal();
  const tools = useTools().filter((t) => canUse(t.accessPolicy, principal));
  // Connected, usable servers; only their enabled (allow-listed) tools can be bound.
  const mcpServers = useMcpServers().filter((s) => s.status === "connected" && canUse(s.accessPolicy, principal));
  // Keyed by open-state in ToolsTab so it remounts fresh per open (initial = current bindings).
  const [selected, setSelected] = React.useState<string[]>(agent.tools);

  function toggle(ref: string) {
    setSelected((s) => (s.includes(ref) ? s.filter((x) => x !== ref) : [...s, ref]));
  }
  function submit() {
    updateAgent(agent.id, { tools: selected }, false);
    toast(`Updated tools for ${agent.name}`);
    onClose();
  }

  const mcpWithEnabled = mcpServers
    .map((s) => ({ server: s, enabled: s.tools.filter((t) => t.enabled) }))
    .filter((x) => x.enabled.length > 0);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Bind tools"
      description="Select repository tools and MCP tools this agent can call. Schemas are snapshotted into the next version."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={submit}>Update tools</Button>
        </>
      }
    >
      <div className="space-y-4">
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-muted">Repository</p>
          <div className="divide-y divide-border">
            {tools.length === 0 && <p className="py-3 text-xs text-muted">No repository tools you can use in this workspace.</p>}
            {tools.map((t) => (
              <button key={t.name} type="button" onClick={() => toggle(t.name)} className="flex w-full items-center justify-between gap-3 py-3 text-left">
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
        </div>

        {mcpWithEnabled.map(({ server, enabled }) => (
          <div key={server.id}>
            <p className="mb-1 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-muted"><Plug className="size-3.5" /> {server.name}{server.authMode === "per_user" && <Badge tone="accent">Per-user</Badge>}</p>
            <div className="divide-y divide-border">
              {enabled.map((t) => {
                const ref = mcpRef(server.id, t.name);
                return (
                  <button key={ref} type="button" onClick={() => toggle(ref)} className="flex w-full items-center justify-between gap-3 py-3 text-left">
                    <div className="flex items-center gap-3">
                      <Plug className="size-4 text-muted" />
                      <div>
                        <p className="font-mono text-sm text-ink">{t.name}</p>
                        <p className="text-xs text-muted">{t.description}</p>
                      </div>
                    </div>
                    <Toggle on={selected.includes(ref)} />
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </Dialog>
  );
}

function CreateTokenDialog({ agent, open, onClose }: { agent: Agent; open: boolean; onClose: () => void }) {
  const [name, setName] = React.useState("");
  const [expiry, setExpiry] = React.useState("none");
  // The one-time secret lives ONLY in this component's state (cleared on close) - never in the store/localStorage.
  const [full, setFull] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  // review #3: no close while a live mint is in flight — the server would create the token and its one-time secret
  // would land in a dialog that's gone (or show up when the dialog is next opened for a new token). A ref, not `busy`:
  // an Escape/backdrop in the same tick as the click would still see the pre-render `busy=false` closure.
  const minting = React.useRef(false);
  const live = useIsLiveWorkspace();

  function close() {
    if (minting.current) return;
    setName("");
    setExpiry("none");
    setFull(null);
    onClose();
  }
  async function submit() {
    const tokenName = name.trim() || `${agent.endpoint ?? agent.id}-token`;
    if (live) {
      if (minting.current) return;
      minting.current = true;
      setBusy(true);
      const days = expiry === "none" ? null : Number(expiry);
      const out = await mintTokenLive(agent.id, tokenName, days ? new Date(Date.now() + days * 86_400_000).toISOString() : undefined)
        .finally(() => { minting.current = false; });
      setBusy(false);
      if (out) setFull(out.secret);
      return;
    }
    const f = `ac_live_${genId("")}${genId("")}`.replace(/-/g, "");
    addToken({
      id: genId("tok"),
      name: tokenName,
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
            <Button variant="ghost" disabled={busy} onClick={close}>Cancel</Button>
            <Button variant="primary" disabled={busy} onClick={() => void submit()}>{busy ? "Creating..." : "Create token"}</Button>
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
              {/* review #2: live max IS 90 days (server cap) — a separate "90 days" option would be a duplicate of the default. */}
              <option value="none">{live ? "Maximum (90 days)" : "No expiry"}</option><option value="30">30 days</option>{!live && <option value="90">90 days</option>}
            </Select>
          </Field>
        </div>
      )}
    </Dialog>
  );
}
