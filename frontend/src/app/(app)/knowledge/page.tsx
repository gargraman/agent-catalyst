"use client";

import Link from "next/link";
import {
  Database, Plus, FileText, Globe, Plug, Pencil, RefreshCw, AlertTriangle, CheckCircle2, Boxes,
} from "lucide-react";
import {
  Card, CardContent, CardHeader, CardTitle, Badge, StatusDot, Table, THead, TH, TR, TD, PageHeader, Button, Progress,
} from "@/components/ui";
import { StatTile } from "@/components/stat-tile";
import { GateButton } from "@/components/gate";
import { useIngestionJobs, useKnowledgeBases } from "@/lib/store";
import { usePrincipal } from "@/lib/auth";
import { canUse } from "@/lib/access-policy";
import { num, relative } from "@/lib/format";

const kbTone = { ready: "good", indexing: "warning", error: "critical" } as const;
const srcIcon = { File: FileText, Web: Globe, Connector: Plug, Manual: Pencil };
const jobTone = { completed: "good", running: "accent", failed: "critical", queued: "neutral" } as const;

/* @backend integration points (this page):
 *  - @backend GET /v1/knowledge-bases?workspace=:id - list knowledge bases [via useKnowledgeBases]
 *  - @backend GET /v1/ingestion-jobs - ingestion job history, poll/stream [via useIngestionJobs]
 * See src/lib/store.ts markers + docs/backend-integration-map.md for the authoritative contracts.
 */
export default function KnowledgePage() {
  const principal = usePrincipal();
  const knowledgeBases = useKnowledgeBases().filter((k) => canUse(k.accessPolicy, principal));
  const ingestionJobs = useIngestionJobs();

  const totalDocs = knowledgeBases.reduce((s, k) => s + k.docs, 0);
  const totalChunks = knowledgeBases.reduce((s, k) => s + k.chunks, 0);
  const attention = knowledgeBases.filter((k) => k.status !== "ready").length;

  return (
    <div className="space-y-6">
      <PageHeader title="Knowledge" description="Ground agents in your own documents. Sources are chunked, embedded and retrieved with citations.">
        <GateButton permission="knowledge.manage" variant="primary" href="/knowledge/new"><Plus className="size-4" /> New knowledge base</GateButton>
      </PageHeader>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatTile label="Knowledge bases" value={String(knowledgeBases.length)} icon={<Database className="size-4" />} />
        <StatTile label="Documents" value={num(totalDocs, { compact: true })} icon={<FileText className="size-4" />} />
        <StatTile label="Chunks indexed" value={num(totalChunks, { compact: true })} icon={<Boxes className="size-4" />} />
        <StatTile label="Needs attention" value={String(attention)} hint="indexing or error" icon={<AlertTriangle className="size-4" />} />
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {knowledgeBases.map((kb) => (
          <Link key={kb.id} href={`/knowledge/${kb.id}`} className="card-elev lift block rounded-xl border border-border bg-surface p-5">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <h3 className="truncate font-semibold text-ink">{kb.name}</h3>
                <p className="mt-0.5 font-mono text-xs text-muted">{kb.embeddingModel}</p>
              </div>
              <Badge tone={kbTone[kb.status]}><StatusDot tone={kbTone[kb.status]} />{kb.status}</Badge>
            </div>

            <div className="mt-4 grid grid-cols-3 gap-3 border-y border-border py-3 text-xs">
              <div><div className="text-muted">Docs</div><div className="tnum font-medium text-ink-2">{num(kb.docs, { compact: true })}</div></div>
              <div><div className="text-muted">Chunks</div><div className="tnum font-medium text-ink-2">{num(kb.chunks, { compact: true })}</div></div>
              <div><div className="text-muted">Size</div><div className="tnum font-medium text-ink-2">{kb.sizeMB} MB</div></div>
            </div>

            {kb.status === "indexing" && (
              <div className="mt-3"><div className="mb-1 flex justify-between text-xs text-muted"><span>Indexing</span><span className="tnum">72%</span></div><Progress value={72} tone="warning" /></div>
            )}

            <ul className="mt-3 space-y-2">
              {kb.sources.map((s) => {
                const Icon = srcIcon[s.type];
                return (
                  <li key={s.name} className="flex items-center gap-2 text-xs">
                    <Icon className="size-3.5 shrink-0 text-muted" />
                    <span className="min-w-0 flex-1 truncate text-ink-2">{s.name}</span>
                    <StatusDot tone={s.status === "synced" ? "good" : s.status === "syncing" ? "warning" : "critical"} />
                  </li>
                );
              })}
            </ul>

            <div className="mt-4 flex items-center justify-between text-xs text-muted">
              <span>{kb.boundAgents} agent{kb.boundAgents === 1 ? "" : "s"} bound</span>
              <span>synced {relative(kb.lastSync)}</span>
            </div>
          </Link>
        ))}
      </div>

      <Card>
        <CardHeader>
          <div><CardTitle>Ingestion jobs</CardTitle><p className="mt-0.5 text-xs text-muted">Extract, chunk and embed runs across all sources.</p></div>
          <Button variant="secondary" size="sm"><RefreshCw className="size-4" /> Refresh</Button>
        </CardHeader>
        <CardContent className="pt-0">
          <Table>
            <THead><tr><TH>Job</TH><TH>Source</TH><TH>Knowledge base</TH><TH>Status</TH><TH className="text-right">Docs</TH><TH>Duration</TH><TH>Started</TH></tr></THead>
            <tbody>
              {ingestionJobs.map((j) => (
                <TR key={j.id}>
                  <TD className="font-mono text-xs text-ink-2">{j.id}</TD>
                  <TD className="text-ink-2">{j.source}</TD>
                  <TD className="text-xs text-muted">{j.kb}</TD>
                  <TD>
                    <Badge tone={jobTone[j.status]}>
                      {j.status === "completed" ? <CheckCircle2 className="size-3" /> : j.status === "failed" ? <AlertTriangle className="size-3" /> : <StatusDot tone={jobTone[j.status]} />}
                      {j.status}
                    </Badge>
                  </TD>
                  <TD className="text-right tnum">{num(j.docs)}</TD>
                  <TD className="tnum text-xs text-muted">{j.duration}</TD>
                  <TD className="text-xs text-muted">{relative(j.startedAt)}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
