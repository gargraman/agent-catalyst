"use client";

import Link from "next/link";
import { ArrowRight, Bot, CircleDollarSign, Gauge, Zap } from "lucide-react";
import {
  Badge, Button, Card, CardContent, CardHeader, CardTitle, PageHeader,
  Progress, StatusDot, Table, TD, TH, THead, TR,
} from "@/components/ui";
import { StatTile } from "@/components/stat-tile";
import { AreaChart, BarChart, Donut } from "@/components/charts";
import {
  RUNS_LABELS, SPEND_LABELS,
  activityFor, dashStats, runsSeries, spendSeries,
  driftTone, statusLabel, statusTone,
} from "@/lib/data";
import { useAgents } from "@/lib/store";
import { useWorkspace } from "@/lib/auth";
import { num, relative, usd } from "@/lib/format";

/* @backend integration points (this page):
 *  - @backend GET /v1/agents?workspace=:id - aggregates (published/spend/calls/eval avg) derived client-side [via useAgents]
 *  - @backend metrics API GET (ws-scoped) - timeseries + activity charts (RUNS_SERIES, SPEND_SERIES, ACTIVITY) move server-side
 *  - @backend metrics API GET (ws-scoped) - stat deltas ("12.4%", "1.2 pts", "18.0%") are hardcoded today, become metrics
 * See src/lib/store.ts markers + docs/backend-integration-map.md for the authoritative contracts.
 */
export default function DashboardPage() {
  const agents = useAgents();
  const { workspace } = useWorkspace();
  const ws = workspace?.id;
  const spend_series = spendSeries(ws);
  const runs_series = runsSeries(ws);
  const activity = activityFor(ws);
  const stats = dashStats(ws);
  const published = agents.filter((a) => a.status === "published").length;
  const spend = agents.reduce((s, a) => s + a.cost30d, 0);
  const calls = agents.reduce((s, a) => s + a.calls30d, 0);
  const active = agents.filter((a) => a.status !== "archived");
  const evalAvg = active.length ? active.reduce((s, a) => s + a.evalPass, 0) / active.length : 0;

  const statusCounts = [
    { label: "Published", value: agents.filter((a) => a.status === "published").length, color: "var(--color-s3)" },
    { label: "Draft", value: agents.filter((a) => a.status === "draft").length, color: "var(--color-s1)" },
    { label: "Eval failing", value: agents.filter((a) => a.status === "eval_failing").length, color: "var(--color-critical)" },
    { label: "Archived", value: agents.filter((a) => a.status === "archived").length, color: "var(--color-muted)" },
  ];
  const volume = RUNS_LABELS.map((l, i) => ({ label: l, value: runs_series[0].values[i] }));
  const topAgents = [...agents].filter((a) => a.status !== "archived").sort((a, b) => b.calls30d - a.calls30d).slice(0, 5);

  return (
    <div className="space-y-6">
      <PageHeader title="Dashboard" description="Your agents across build, test and publish, with spend and reliability at a glance.">
        <Button variant="outline">Last 30 days</Button>
        <Button variant="primary" href="/agents/new">New agent</Button>
      </PageHeader>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile label="Published agents" value={String(published)} hint={`${active.length} active of ${agents.length} total`} icon={<Bot className="size-4" />} />
        <StatTile label="Spend (30d)" value={usd(spend, { compact: true })} delta={stats.spendDelta} deltaGood={false} hint="vs prior 30d" spark={spend_series[0].values} sparkTone="var(--color-s2)" icon={<CircleDollarSign className="size-4" />} />
        <StatTile label="Eval pass rate" value={`${evalAvg.toFixed(1)}%`} delta={stats.evalDelta} deltaGood hint="across active agents" spark={stats.evalSpark} sparkTone="var(--color-s3)" icon={<Gauge className="size-4" />} />
        <StatTile label="Calls (30d)" value={num(calls, { compact: true })} delta={stats.callsDelta} deltaGood spark={runs_series[0].values} icon={<Zap className="size-4" />} />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <div>
              <CardTitle>Spend by provider</CardTitle>
              <p className="mt-0.5 text-xs text-muted">Daily model spend, last 30 days</p>
            </div>
            <Badge tone="neutral">{usd(spend, { compact: true })} total</Badge>
          </CardHeader>
          <CardContent className="pt-2">
            <AreaChart series={spend_series} labels={SPEND_LABELS} format={(n) => usd(n, { compact: true })} height={240} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Agent status</CardTitle></CardHeader>
          <CardContent className="flex items-center justify-center py-8">
            <Donut data={statusCounts} />
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <div>
              <CardTitle>Request volume</CardTitle>
              <p className="mt-0.5 text-xs text-muted">Runs served this week across all agents</p>
            </div>
          </CardHeader>
          <CardContent className="pt-2">
            <BarChart data={volume} format={(n) => num(n, { compact: true })} height={240} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Recent activity</CardTitle></CardHeader>
          <CardContent className="space-y-3.5 pt-1">
            {activity.map((a, i) => (
              <div key={i} className="flex gap-3">
                <StatusDot tone={a.tone} className="mt-1.5" />
                <div className="min-w-0 flex-1">
                  <p className="text-sm leading-snug text-ink-2">
                    <span className="font-medium text-ink">{a.who}</span> {a.action}{" "}
                    <span className="font-medium text-ink">{a.target}</span>
                  </p>
                  <p className="text-xs text-muted">{relative(a.time)}</p>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Agents</CardTitle>
          <Link href="/agents" className="inline-flex items-center gap-1 text-sm font-medium text-accent hover:underline">
            View all <ArrowRight className="size-4" />
          </Link>
        </CardHeader>
        <CardContent className="px-0 pb-0 pt-2">
          <Table>
            <THead>
              <TR>
                <TH>Agent</TH><TH>Status</TH><TH>Model</TH>
                <TH className="text-right">Calls (30d)</TH><TH className="text-right">Spend</TH>
                <TH>Eval pass</TH><TH>Drift</TH><TH>Updated</TH>
              </TR>
            </THead>
            <tbody>
              {topAgents.map((a) => (
                <TR key={a.id}>
                  <TD>
                    <Link href={`/agents/${a.id}`} className="font-medium text-ink hover:text-accent">{a.name}</Link>
                  </TD>
                  <TD><Badge tone={statusTone[a.status]}><StatusDot tone={statusTone[a.status]} />{statusLabel[a.status]}</Badge></TD>
                  <TD><span className="font-mono text-xs text-ink-2">{a.model}</span></TD>
                  <TD className="text-right tnum">{num(a.calls30d, { compact: true })}</TD>
                  <TD className="text-right tnum">{usd(a.cost30d, { compact: true })}</TD>
                  <TD>
                    <div className="flex items-center gap-2">
                      <Progress value={a.evalPass} tone={a.evalPass >= 90 ? "good" : a.evalPass >= 80 ? "warning" : "critical"} className="w-16" />
                      <span className="tnum text-xs text-ink-2">{a.evalPass.toFixed(1)}%</span>
                    </div>
                  </TD>
                  <TD><Badge tone={driftTone[a.drift]}>{a.drift}</Badge></TD>
                  <TD className="whitespace-nowrap text-xs text-muted">{relative(a.updatedAt)}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
