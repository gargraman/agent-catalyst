"use client";

import * as React from "react";
import Link from "next/link";
import { FlaskConical, Gauge, Play, ShieldCheck, TriangleAlert } from "lucide-react";
import {
  Card, CardHeader, CardTitle, CardContent, CardDescription, Badge, Button, Field, Input, Select,
  Table, THead, TH, TR, TD, EmptyState,
} from "@/components/ui";
import { Dialog } from "@/components/dialog";
import { GateButton } from "@/components/gate";
import { Segmented } from "@/components/tabs";
import { StatTile } from "@/components/stat-tile";
import { BarChart, Donut } from "@/components/charts";
import { getVersions, evalStats, verdictTone, verdictLabel, type Agent, type Verdict } from "@/lib/data";
import { useEvalRuns, useAgents, addEvalRun, genId, nowIso } from "@/lib/store";
import { useWorkspace } from "@/lib/auth";
import { pct, relative } from "@/lib/format";

const FILTERS = [
  { key: "all", label: "All" },
  { key: "pass", label: "Pass" },
  { key: "fail", label: "Fail" },
  { key: "low_confidence", label: "Low confidence" },
];

const SUITES = ["Golden set", "Regression set", "Smoke set"];
const JUDGES = ["claude-opus-5-5", "claude-sonnet-5-5"];

/** Version labels for an agent, falling back to the active version when none are recorded. */
function versionLabelsFor(a: Agent): string[] {
  const labels = getVersions(a.id).map((v) => v.label);
  return labels.length > 0 ? labels : [a.activeVersion];
}

function round1(n: number): number {
  return Math.round(n * 10) / 10;
}

/* @backend integration points (this page):
 *  - @backend GET /v1/evals - eval run history [via useEvalRuns]
 *  - @backend POST /v1/agents/:id/evals - run eval, async job [via addEvalRun]
 *  - @backend NOTE runEval() result values (quality/deltas/cases) are Math.random mock -> become the async job result
 * See src/lib/store.ts markers + docs/backend-integration-map.md for the authoritative contracts.
 */
export default function EvaluationsPage() {
  const evalRuns = useEvalRuns();
  const agents = useAgents();
  const { workspace } = useWorkspace();
  const stats = evalStats(workspace?.id);
  const activeAgents = agents.filter((a) => a.status !== "archived");

  const [filter, setFilter] = React.useState("all");
  const [open, setOpen] = React.useState(false);

  const firstAgent = activeAgents[0];
  const firstLabels = firstAgent ? versionLabelsFor(firstAgent) : [];
  const [agentId, setAgentId] = React.useState(firstAgent?.id ?? "");
  const [version, setVersion] = React.useState(firstLabels[0] ?? "");
  const [baseline, setBaseline] = React.useState(firstLabels[1] ?? firstLabels[0] ?? "");
  const [suite, setSuite] = React.useState(SUITES[0]);
  const [judge, setJudge] = React.useState(JUDGES[0]);
  const [threshold, setThreshold] = React.useState("90");
  const [budget, setBudget] = React.useState("within 10%");
  const [mode, setMode] = React.useState("interactive");

  const selectedAgent = agents.find((a) => a.id === agentId);
  const versionLabels = selectedAgent ? versionLabelsFor(selectedAgent) : [];

  function onAgentChange(id: string) {
    const next = agents.find((a) => a.id === id);
    const labels = next ? versionLabelsFor(next) : [];
    setAgentId(id);
    setVersion(labels[0] ?? "");
    setBaseline(labels[1] ?? labels[0] ?? "");
  }

  function runEval() {
    if (!selectedAgent) return;
    const thr = Number(threshold) || 90;
    const quality = round1(90 + (Math.random() * 12 - 6));
    const qualityDelta = round1(Math.random() * 8 - 4);
    const costDelta = round1(Math.random() * 10 - 5);
    const latencyDelta = Math.round(Math.random() * 160 - 80);
    const cases = 24 + Math.floor(Math.random() * 57); // 24..80
    let verdict: Verdict = quality >= thr ? "pass" : "fail";
    if (cases < 10) verdict = "low_confidence";

    addEvalRun({
      id: genId("ev"),
      agentId: selectedAgent.id,
      agent: selectedAgent.name,
      version,
      baseline,
      quality,
      qualityDelta,
      costDelta,
      latencyDelta,
      cases,
      verdict,
      date: nowIso(),
      trigger: "Manual run",
    });
    setOpen(false);
  }

  const total = evalRuns.length;
  const passes = evalRuns.filter((e) => e.verdict === "pass").length;
  const drift = evalRuns.filter((e) => e.verdict !== "pass").length;
  const avgQuality = evalRuns.reduce((s, e) => s + e.quality, 0) / (total || 1);

  const rows = evalRuns.filter((e) => filter === "all" || e.verdict === filter);

  const qualityByAgent = agents.filter((a) => a.status !== "archived" && a.evalPass > 0)
    .map((a) => ({ label: a.name.split(" ")[0], value: a.evalPass }));

  const verdictDist = [
    { label: "Pass", value: passes, color: "var(--color-good)" },
    { label: "Fail", value: evalRuns.filter((e) => e.verdict === "fail").length, color: "var(--color-critical)" },
    { label: "Low confidence", value: evalRuns.filter((e) => e.verdict === "low_confidence").length, color: "var(--color-warning)" },
  ];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-ink">Evaluations</h1>
          <p className="mt-1 text-sm text-muted">Track prompt stability and quality or efficiency drift when models or knowledge change.</p>
        </div>
        <GateButton permission="eval.run" variant="primary" onClick={() => setOpen(true)}><Play className="size-4" /> New eval run</GateButton>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile label="Eval runs (30d)" value={stats.runs30d} delta={stats.runs30dDelta} deltaGood hint="across agents" icon={<FlaskConical className="size-4" />} spark={stats.runs30dSpark} />
        <StatTile label="Overall pass rate" value={stats.passRate} delta={stats.passRateDelta} deltaGood={false} hint="last 7d" icon={<Gauge className="size-4" />} spark={stats.passRateSpark} sparkTone="var(--color-s3)" />
        <StatTile label="Drift incidents caught" value={String(drift)} delta={stats.driftDelta} deltaGood={false} hint="fail or low-confidence" icon={<TriangleAlert className="size-4" />} spark={stats.driftSpark} sparkTone="var(--color-s2)" />
        <StatTile label="Avg quality score" value={pct(avgQuality)} delta={stats.qualityDelta} deltaGood hint="blended rubric" icon={<ShieldCheck className="size-4" />} spark={stats.qualitySpark} />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader><CardTitle>Pass rate by agent</CardTitle><CardDescription>Latest eval pass rate per active agent</CardDescription></CardHeader>
          <CardContent><BarChart data={qualityByAgent} height={230} format={(n) => `${n.toFixed(0)}%`} /></CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Verdict distribution</CardTitle></CardHeader>
          <CardContent><Donut data={verdictDist} /></CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <div><CardTitle>Recent eval runs</CardTitle><CardDescription>Candidate scored against a frozen baseline on quality and efficiency.</CardDescription></div>
          <Segmented options={FILTERS} value={filter} onChange={setFilter} />
        </CardHeader>
        <CardContent className="pt-0">
          {rows.length === 0 ? (
            <EmptyState icon={<FlaskConical className="size-6" />} title="No runs match this filter" />
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH>Agent</TH><TH>Trigger</TH><TH className="text-right">Quality</TH>
                  <TH className="text-right">Cost Δ</TH><TH className="text-right">Latency Δ</TH>
                  <TH>Cases</TH><TH>Verdict</TH><TH>When</TH>
                </TR>
              </THead>
              <tbody>
                {rows.map((e) => (
                  <TR key={e.id}>
                    <TD>
                      <Link href={`/agents/${e.agentId}`} className="font-medium text-ink hover:text-accent">{e.agent}</Link>
                      <div className="font-mono text-xs text-muted">{e.version} vs {e.baseline}</div>
                    </TD>
                    <TD className="text-ink-2">{e.trigger}</TD>
                    <TD className="text-right">
                      <span className="tnum font-medium text-ink">{pct(e.quality)}</span>
                      {e.qualityDelta !== 0 && (
                        <span className={`ml-1.5 text-xs tnum ${e.qualityDelta > 0 ? "text-good" : "text-critical"}`}>
                          {e.qualityDelta > 0 ? "+" : ""}{e.qualityDelta}
                        </span>
                      )}
                    </TD>
                    <TD className={`text-right text-xs tnum ${e.costDelta <= 0 ? "text-good" : "text-critical"}`}>{e.costDelta > 0 ? "+" : ""}{e.costDelta}%</TD>
                    <TD className={`text-right text-xs tnum ${e.latencyDelta <= 0 ? "text-good" : "text-critical"}`}>{e.latencyDelta > 0 ? "+" : ""}{e.latencyDelta}ms</TD>
                    <TD>{e.cases < 10 ? <Badge tone="warning">{e.cases} · low set</Badge> : <span className="tnum text-ink-2">{e.cases}</span>}</TD>
                    <TD><Badge tone={verdictTone[e.verdict]}>{verdictLabel[e.verdict]}</Badge></TD>
                    <TD className="whitespace-nowrap text-xs text-muted">{relative(e.date)}</TD>
                  </TR>
                ))}
              </tbody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>How drift gating works</CardTitle></CardHeader>
        <CardContent className="grid gap-4 pt-0 sm:grid-cols-3">
          <Explain icon={<ShieldCheck className="size-4" />} title="Publish is gated">
            A version cannot be published or re-pointed without a passing eval newer than its last model, prompt or knowledge change.
          </Explain>
          <Explain icon={<Gauge className="size-4" />} title="Quality and efficiency">
            Each run scores quality (deterministic checks plus a fixed LLM judge) and efficiency (cost, tokens, latency) against a frozen baseline.
          </Explain>
          <Explain icon={<TriangleAlert className="size-4" />} title="Small sets flagged">
            Suites with fewer than 10 cases are marked low-confidence and widen the drift threshold to limit false positives.
          </Explain>
        </CardContent>
      </Card>

      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title="New eval run"
        description="Score a candidate version against a frozen baseline."
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
            <GateButton permission="eval.run" variant="primary" onClick={runEval} disabled={!selectedAgent}>
              <Play className="size-4" /> Run eval
            </GateButton>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Candidate agent">
            <Select value={agentId} onChange={(e) => onAgentChange(e.target.value)}>
              {activeAgents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </Select>
          </Field>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Candidate version">
              <Select value={version} onChange={(e) => setVersion(e.target.value)}>
                {versionLabels.map((v) => <option key={v} value={v}>{v}</option>)}
              </Select>
            </Field>
            <Field label="Baseline version">
              <Select value={baseline} onChange={(e) => setBaseline(e.target.value)}>
                {versionLabels.map((v) => <option key={v} value={v}>{v}</option>)}
              </Select>
            </Field>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Eval suite">
              <Select value={suite} onChange={(e) => setSuite(e.target.value)}>
                {SUITES.map((s) => <option key={s} value={s}>{s}</option>)}
              </Select>
            </Field>
            <Field label="Judge model">
              <Select value={judge} onChange={(e) => setJudge(e.target.value)}>
                {JUDGES.map((j) => <option key={j} value={j}>{j}</option>)}
              </Select>
            </Field>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Quality threshold" hint="Pass when quality score meets this value.">
              <Input value={threshold} onChange={(e) => setThreshold(e.target.value)} inputMode="decimal" />
            </Field>
            <Field label="Efficiency budget" hint="Cost and latency tolerance vs baseline.">
              <Input value={budget} onChange={(e) => setBudget(e.target.value)} />
            </Field>
          </div>

          <Field label="Mode">
            <Select value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="interactive">Interactive</option>
              <option value="batch">Batch</option>
            </Select>
          </Field>
        </div>
      </Dialog>
    </div>
  );
}

function Explain({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-border bg-elevated p-4">
      <div className="flex items-center gap-2 text-accent">{icon}<span className="text-sm font-medium text-ink">{title}</span></div>
      <p className="mt-2 text-xs leading-relaxed text-muted">{children}</p>
    </div>
  );
}
