"use client";

import * as React from "react";
import { useParams } from "next/navigation";
import {
  ArrowLeft, Boxes, FileText, Globe, HardDrive, Link2, Plug, Plus, RefreshCw, TriangleAlert,
} from "lucide-react";
import {
  Badge, Button, Card, CardContent, CardDescription, CardHeader, CardTitle,
  EmptyState, Field, PageHeader, Select, StatusDot, Table, TD, TH, THead, TR,
} from "@/components/ui";
import { Dialog, ConfirmDialog } from "@/components/dialog";
import {
  SourcePicker, buildDataSource, describeDataSourceConfig, finalizeCredential, isSourceComplete,
  initialSourceState, type SourceState,
} from "@/components/source-picker";
import { StatTile } from "@/components/stat-tile";
import { GateButton } from "@/components/gate";
import { AccessNote } from "@/components/access-note";
import { AccessPolicyEditor } from "@/components/access-policy-editor";
import { useAuth, usePrincipal } from "@/lib/auth";
import { canManage } from "@/lib/access-policy";
import { type DataSource, type IngestionJob, type KnowledgeBase } from "@/lib/data";
import { addDataSource, reingest, toast, updateAgent, useAgents, useIngestionJobs, useKnowledgeBase } from "@/lib/store";
import { num, relative } from "@/lib/format";

const kbTone = (s: KnowledgeBase["status"]) => (s === "ready" ? "good" : s === "indexing" ? "warning" : "critical");
const kbLabel = (s: KnowledgeBase["status"]) => (s === "ready" ? "Ready" : s === "indexing" ? "Indexing" : "Error");
const srcTone = (s: DataSource["status"]) => (s === "synced" ? "good" : s === "syncing" ? "warning" : "critical");
const jobTone = (s: IngestionJob["status"]) =>
  s === "completed" ? "good" : s === "running" ? "accent" : s === "failed" ? "critical" : "neutral";

function SourceIcon({ type }: { type: DataSource["type"] }) {
  const Icon = type === "Web" ? Globe : type === "Connector" ? Plug : FileText;
  return <Icon className="size-4 text-muted" />;
}

function ConfigRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-border py-2.5 last:border-0">
      <span className="text-sm text-muted">{label}</span>
      <span className={`text-sm text-ink ${mono ? "font-mono text-xs" : ""}`}>{value}</span>
    </div>
  );
}

/* @backend integration points (this page):
 *  - @backend GET /v1/knowledge-bases/:id - knowledge base detail [via useKnowledgeBase]
 *  - @backend POST /v1/knowledge-bases/:id/sources - add source, async ingest [via addDataSource]
 *  - @backend POST /v1/knowledge-bases/:id/reingest - re-embed all docs, async job [via reingest]
 *  - @backend PATCH /v1/agents/:id - bind this KB to an agent version as a retrieval source
 *  - @backend PUT /v1/knowledge/:id/access-policy - build-time access policy [via setAccessPolicy]
 * See src/lib/store.ts markers + docs/backend-integration-map.md for the authoritative contracts.
 */
export default function KnowledgeDetailPage() {
  const params = useParams();
  const id = String(params.id);
  const kb = useKnowledgeBase(id);
  const agents = useAgents();
  const ingestionJobs = useIngestionJobs();
  const { can } = useAuth();
  const principal = usePrincipal();

  const [addOpen, setAddOpen] = React.useState(false);
  const [reingestOpen, setReingestOpen] = React.useState(false);
  const [bindOpen, setBindOpen] = React.useState(false);

  const [source, setSource] = React.useState<SourceState>(initialSourceState);

  const [bindAgent, setBindAgent] = React.useState("");
  // Binding mutates the target agent, so restrict the picker to agents the caller can manage (mirrors the
  // agent's own Knowledge tab, which gates on agent.edit + canManage). The Bind button also requires agent.edit.
  const bindableAgents = agents.filter((a) => canManage(a.accessPolicy, principal));
  const effectiveBindAgent = bindAgent || bindableAgents[0]?.id || "";

  if (!kb) {
    return (
      <div className="space-y-6">
        <Button variant="ghost" size="sm" href="/knowledge"><ArrowLeft className="size-4" /> Back</Button>
        <EmptyState icon={<Boxes className="size-6" />} title="Knowledge base not found" hint="It may have been deleted or the link is incorrect." />
      </div>
    );
  }

  const jobs = ingestionJobs.filter((j) => j.kb === kb.name);

  function submitAddSource() {
    const src = buildDataSource(source);
    if (src) {
      const credentialRef = finalizeCredential(source);
      addDataSource(id, credentialRef ? { ...src, config: { ...src.config, credentialRef } } : src);
    }
    setSource(initialSourceState);
    setAddOpen(false);
  }

  // @backend PATCH /v1/agents/:id - bind this KB to the selected agent (same action as the agent's
  //   Knowledge tab). boundAgents is recomputed in the store. [via updateAgent]
  function submitBind() {
    const agent = agents.find((a) => a.id === effectiveBindAgent);
    // Defense-in-depth: only bind to an agent the caller is allowed to manage.
    if (!agent || !kb || !canManage(agent.accessPolicy, principal)) return;
    if (agent.knowledgeBases.includes(kb.id)) {
      toast(`${agent.name} already has "${kb.name}"`, "neutral");
      setBindOpen(false);
      return;
    }
    updateAgent(agent.id, { knowledgeBases: [...agent.knowledgeBases, kb.id] }, false);
    toast(`Bound "${kb.name}" to ${agent.name}`);
    setBindOpen(false);
  }

  return (
    <div className="space-y-6">
      <div>
        <Button variant="ghost" size="sm" href="/knowledge" className="-ml-2 mb-3"><ArrowLeft className="size-4" /> Knowledge</Button>
        <PageHeader title={kb.name} description={kb.description || `Embedding model ${kb.embeddingModel}`}>
          <GateButton permission="knowledge.manage" variant="outline" onClick={() => setReingestOpen(true)}><RefreshCw className="size-4" /> Re-ingest</GateButton>
          <GateButton permission="knowledge.manage" variant="primary" onClick={() => setBindOpen(true)}><Link2 className="size-4" /> Bind to agent</GateButton>
        </PageHeader>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={kbTone(kb.status)}>
          {kb.status === "indexing" && <StatusDot tone="warning" className="animate-pulse" />}
          {kbLabel(kb.status)}
        </Badge>
        <Badge tone="neutral">{kb.boundAgents} agent{kb.boundAgents === 1 ? "" : "s"} bound</Badge>
        <span className="text-xs text-muted">synced {relative(kb.lastSync)}</span>
      </div>

      {kb.status === "error" && (
        <div className="flex items-start gap-3 rounded-xl border border-critical/30 bg-critical/10 p-4">
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-critical" />
          <div className="text-sm">
            <p className="font-medium text-ink">Last ingestion failed</p>
            <p className="text-ink-2">One source could not be parsed. Fix the source and re-ingest to restore retrieval for bound agents.</p>
          </div>
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile label="Documents" value={num(kb.docs)} icon={<FileText className="size-4" />} />
        <StatTile label="Chunks indexed" value={num(kb.chunks)} icon={<Boxes className="size-4" />} />
        <StatTile label="Index size" value={`${kb.sizeMB} MB`} icon={<HardDrive className="size-4" />} />
        <StatTile label="Bound agents" value={String(kb.boundAgents)} icon={<Link2 className="size-4" />} />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Data sources</CardTitle>
            <GateButton permission="knowledge.manage" variant="outline" size="sm" onClick={() => setAddOpen(true)}><Plus className="size-4" /> Add data source</GateButton>
          </CardHeader>
          <CardContent className="pt-2">
            <Table>
              <THead>
                <TR>
                  <TH>Source</TH>
                  <TH>Type</TH>
                  <TH className="text-right">Docs</TH>
                  <TH>Updated</TH>
                  <TH>Status</TH>
                </TR>
              </THead>
              <tbody>
                {kb.sources.map((s, i) => {
                  const cfg = describeDataSourceConfig(s.config);
                  return (
                  <TR key={`${s.name}-${i}`}>
                    <TD>
                      <span className="flex items-center gap-2 font-medium text-ink"><SourceIcon type={s.type} />{s.name}</span>
                      {cfg && <span className="mt-0.5 block pl-6 font-mono text-xs text-muted">{cfg}</span>}
                    </TD>
                    <TD>{s.type}</TD>
                    <TD className="text-right tnum">{num(s.docs)}</TD>
                    <TD>{relative(s.updatedAt)}</TD>
                    <TD>
                      <Badge tone={srcTone(s.status)}>
                        {s.status === "syncing" && <StatusDot tone="warning" className="animate-pulse" />}
                        {s.status[0].toUpperCase() + s.status.slice(1)}
                      </Badge>
                    </TD>
                  </TR>
                  );
                })}
              </tbody>
            </Table>
          </CardContent>
        </Card>

        {/* KB-owned configuration only. Retrieval settings (top-k/threshold/reranker/injection) belong to
            the agent that binds this KB and are edited on the agent's Knowledge tab, then frozen per version. */}
        <Card>
          <CardHeader><CardTitle>Configuration</CardTitle></CardHeader>
          <CardContent className="pt-1">
            <ConfigRow label="Embedding model" value={kb.embeddingModel} mono />
            <ConfigRow label="Index type" value={kb.chunkConfig?.indexType ?? "HNSW"} />
            <ConfigRow label="Chunk size" value={kb.chunkConfig ? `${num(kb.chunkConfig.size)} tokens` : "—"} />
            <ConfigRow label="Chunk overlap" value={kb.chunkConfig ? `${num(kb.chunkConfig.overlap)} tokens` : "—"} />
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Ingestion activity</CardTitle>
          <GateButton permission="knowledge.manage" variant="outline" size="sm" onClick={() => setReingestOpen(true)}><RefreshCw className="size-3.5" /> Re-ingest</GateButton>
        </CardHeader>
        <CardContent className="pt-2">
          {jobs.length === 0 ? (
            <EmptyState title="No ingestion jobs yet" hint="Add a data source to start indexing." />
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH>Job</TH>
                  <TH>Source</TH>
                  <TH className="text-right">Docs</TH>
                  <TH className="text-right">Chunks</TH>
                  <TH>Duration</TH>
                  <TH>Started</TH>
                  <TH>Status</TH>
                </TR>
              </THead>
              <tbody>
                {jobs.map((j) => (
                  <TR key={j.id}>
                    <TD className="font-mono text-xs text-muted">{j.id}</TD>
                    <TD className="font-medium text-ink">{j.source}</TD>
                    <TD className="text-right tnum">{num(j.docs)}</TD>
                    <TD className="text-right tnum">{num(j.chunks)}</TD>
                    <TD className="tnum">{j.duration}</TD>
                    <TD>{relative(j.startedAt)}</TD>
                    <TD>
                      <Badge tone={jobTone(j.status)}>
                        {j.status === "running" && <StatusDot tone="accent" className="animate-pulse" />}
                        {j.status[0].toUpperCase() + j.status.slice(1)}
                      </Badge>
                    </TD>
                  </TR>
                ))}
              </tbody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Access</CardTitle>
            <CardDescription>
              Who can see and manage this knowledge base here (build time) is separate from who can retrieve it at run time, which is governed by the calling user&apos;s token and tenant.
            </CardDescription>
          </div>
        </CardHeader>
        <CardContent className="space-y-4 pt-0">
          <AccessNote variant="run" />
          <AccessPolicyEditor kind="knowledge" id={kb.id} policy={kb.accessPolicy} readOnly={!can("access.manage")} />
        </CardContent>
      </Card>

      <Dialog
        open={addOpen}
        onClose={() => setAddOpen(false)}
        title="Add data source"
        description={`New source for ${kb.name}`}
        footer={
          <>
            <Button variant="ghost" onClick={() => setAddOpen(false)}>Cancel</Button>
            <GateButton permission="knowledge.manage" variant="primary" onClick={submitAddSource} disabled={!isSourceComplete(source)}>Add source</GateButton>
          </>
        }
      >
        <SourcePicker value={source} onChange={setSource} />
      </Dialog>

      <ConfirmDialog
        open={reingestOpen}
        onClose={() => setReingestOpen(false)}
        onConfirm={() => reingest(id)}
        title="Re-ingest knowledge base?"
        confirmLabel="Re-ingest"
        tone="primary"
        body={
          <p>
            This re-embeds all documents in <span className="font-medium text-ink">{kb.name}</span> and incurs embedding cost.
            Bound agents keep serving the current index until the new one is ready.
          </p>
        }
      />

      <Dialog
        open={bindOpen}
        onClose={() => setBindOpen(false)}
        title="Bind to agent"
        description={`Attach ${kb.name} as a retrieval source`}
        footer={
          <>
            <Button variant="ghost" onClick={() => setBindOpen(false)}>Cancel</Button>
            <GateButton permission="agent.edit" variant="primary" onClick={submitBind} disabled={!effectiveBindAgent}>Bind</GateButton>
          </>
        }
      >
        <div className="space-y-4">
          {bindableAgents.length === 0 ? (
            <p className="py-4 text-center text-xs text-muted">No agents you can manage in this workspace.</p>
          ) : (
            <Field label="Agent">
              <Select value={effectiveBindAgent} onChange={(e) => setBindAgent(e.target.value)}>
                {bindableAgents.map((a) => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </Select>
            </Field>
          )}
          <p className="text-xs text-muted">
            Retrieval settings (top-k, threshold, reranker, citations) are configured per agent on the
            agent&apos;s Knowledge tab and frozen into its version snapshot on publish.
          </p>
        </div>
      </Dialog>
    </div>
  );
}
