"use client";

import * as React from "react";
import Link from "next/link";
import { Activity, Download } from "lucide-react";
import {
  Card, CardHeader, CardTitle, CardContent, CardDescription, Badge, StatusDot, Button, Table, THead, TH, TR, TD, EmptyState,
} from "@/components/ui";
import { Segmented } from "@/components/tabs";
import { StatTile } from "@/components/stat-tile";
import { AreaChart } from "@/components/charts";
import { RUNS_LABELS, LATENCY_LABELS, runsSeries, latencySeries, runsStats } from "@/lib/data";
import { useRuns } from "@/lib/store";
import { useWorkspace } from "@/lib/auth";
import { WORKSPACES } from "@/lib/iam";
import { usd, num, ms, relative } from "@/lib/format";

const STATUS = [
  { key: "all", label: "All" },
  { key: "success", label: "Success" },
  { key: "blocked", label: "Blocked" },
  { key: "error", label: "Error" },
];
const SOURCE = [
  { key: "all", label: "All sources" },
  { key: "API", label: "API" },
  { key: "Playground", label: "Playground" },
  { key: "Eval", label: "Eval" },
];

const wsName = (id?: string) => (id ? WORKSPACES.find((w) => w.id === id)?.name ?? id : "-");

/* @backend integration points (this page):
 *  - @backend GET /v1/runs?workspace=:id - runs list [via useRuns]
 *  - @backend metrics API GET (ws-scoped) - timeseries charts (RUNS_SERIES, LATENCY_SERIES) move server-side
 *  - @backend metrics API GET (ws-scoped) - stat tiles ("Runs today" 182940, deltas, spend today) are hardcoded today, become metrics
 * See src/lib/store.ts markers + docs/backend-integration-map.md for the authoritative contracts.
 */
export default function RunsPage() {
  const runs = useRuns();
  const { workspace } = useWorkspace();
  const ws = workspace?.id;
  const runs_series = runsSeries(ws);
  const latency_series = latencySeries(ws);
  const stats = runsStats(ws);
  const [status, setStatus] = React.useState("all");
  const [source, setSource] = React.useState("all");

  const rows = runs.filter((r) => (status === "all" || r.status === status) && (source === "all" || r.source === source));
  const successRate = runs.length ? (runs.filter((r) => r.status === "success").length / runs.length) * 100 : 0;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-ink">Runs</h1>
          <p className="mt-1 text-sm text-muted">Every model call logged with latency, tokens, cost and guardrail outcome.</p>
        </div>
        <Button variant="outline"><Download className="size-4" /> Export</Button>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile label="Runs today" value={num(stats.runsToday, { compact: true })} delta={stats.runsTodayDelta} deltaGood hint="vs yesterday" icon={<Activity className="size-4" />} spark={runs_series[0].values} />
        <StatTile label="Success rate" value={`${successRate.toFixed(1)}%`} delta={stats.successDelta} deltaGood={false} hint="last 24h" spark={stats.successSpark} sparkTone="var(--color-s3)" />
        <StatTile label="p95 latency" value={ms(stats.p95ms)} delta={stats.p95Delta} deltaGood hint="last 24h" spark={latency_series[0].values} sparkTone="var(--color-s4)" />
        <StatTile label="Spend today" value={usd(stats.spendToday)} delta={stats.spendTodayDelta} deltaGood={false} hint="all agents" spark={stats.spendTodaySpark} sparkTone="var(--color-s2)" />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Request volume</CardTitle><CardDescription>Last 7 days</CardDescription></CardHeader>
          <CardContent><AreaChart series={runs_series} labels={RUNS_LABELS} height={200} format={(n) => num(n, { compact: true })} /></CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>p95 latency</CardTitle><CardDescription>Milliseconds</CardDescription></CardHeader>
          <CardContent><AreaChart series={latency_series} labels={LATENCY_LABELS} height={200} format={(n) => ms(n)} /></CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <div><CardTitle>Live runs</CardTitle><CardDescription>Most recent requests across all agents.</CardDescription></div>
          <div className="flex flex-wrap items-center gap-2">
            <Segmented options={STATUS} value={status} onChange={setStatus} />
            <Segmented options={SOURCE} value={source} onChange={setSource} />
          </div>
        </CardHeader>
        <CardContent className="pt-0">
          {rows.length === 0 ? (
            <EmptyState icon={<Activity className="size-6" />} title="No runs match these filters" hint="Try widening the status or source filter." />
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH>Run</TH><TH>Agent</TH><TH>Caller</TH><TH>Tenant</TH><TH>Source</TH><TH>Status</TH>
                  <TH className="text-right">Latency</TH><TH className="text-right">Tokens</TH><TH className="text-right">Cost</TH>
                  <TH>Guardrail</TH><TH>Time</TH>
                </TR>
              </THead>
              <tbody>
                {rows.map((r) => {
                  const isService = r.subjectType === "service" || (!r.subjectType && r.source === "API");
                  return (
                  <TR key={r.id}>
                    <TD><span className="font-mono text-xs text-muted">{r.id}</span></TD>
                    <TD>
                      <Link href={`/agents/${r.agentId}`} className="font-medium text-ink hover:text-accent">{r.agent}</Link>
                      <span className="ml-1.5 font-mono text-xs text-muted">{r.version}</span>
                    </TD>
                    <TD>
                      <span className="inline-flex items-center gap-1.5">
                        <Badge tone={isService ? "neutral" : "accent"}>{isService ? "Service" : "User"}</Badge>
                        <span className="text-xs text-ink-2">{r.caller ?? (r.source === "API" ? "svc: catalyst-gateway" : "you")}</span>
                      </span>
                    </TD>
                    <TD className="whitespace-nowrap text-xs text-ink-2">{wsName(r.workspaceId)}</TD>
                    <TD><Badge tone={r.source === "API" ? "accent" : r.source === "Playground" ? "violet" : "neutral"}>{r.source}</Badge></TD>
                    <TD>
                      <span className="inline-flex items-center gap-1.5 text-xs capitalize text-ink-2">
                        <StatusDot tone={r.status === "success" ? "good" : r.status === "blocked" ? "warning" : "critical"} />{r.status}
                      </span>
                    </TD>
                    <TD className="text-right tnum">{ms(r.latencyMs)}</TD>
                    <TD className="text-right tnum">{num(r.tokens, { compact: true })}</TD>
                    <TD className="text-right tnum">{usd(r.cost)}</TD>
                    <TD className={r.status === "success" ? "text-xs text-muted" : r.status === "blocked" ? "text-xs text-warning" : "text-xs text-critical"}>{r.guardrail}</TD>
                    <TD className="whitespace-nowrap text-xs text-muted">{relative(r.time)}</TD>
                  </TR>
                  );
                })}
              </tbody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
