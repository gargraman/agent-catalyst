"use client";

/* Live eval surfaces (feature 026 US4), shared by the agent Evals tab and the Evaluations page:
   - EvalRunDetail: a run's drill-down (live runs add the evidence line: suite, compared X of Y, baseline provenance);
   - LiveRunEvalDialog: POST {version_id, suite_id} only — the server picks the baseline (D7) and the policy (O3) —
     then shows the persisted result inline. */

import * as React from "react";
import { FlaskConical } from "lucide-react";
import { Badge, Button, Field, Progress, Select } from "@/components/ui";
import { Dialog } from "@/components/dialog";
import { GateButton } from "@/components/gate";
import { verdictLabel, verdictTone, type Agent, type EvalRun } from "@/lib/data";
import { freezeBaseline, runEvalLive, useEnsureEvals, useEvalPolicyFor, useSuitesForAgent, versionsOf } from "@/lib/store";
import { baselineProvenance } from "@/lib/api/adapters";

/** Live-only evidence line: suite, "compared X of Y cases" (D8), and where the baseline came from (D7). */
function LiveRunMeta({ run }: { run: EvalRun }) {
  const suites = useSuitesForAgent(run.agentId);
  const suite = suites.find((x) => x.id === run.suiteId);
  const frozenBy = suite?.baseline?.origin === "frozen" && suite.baseline.evalRunId === run.baselineRef?.evalRunId ? suite.baseline.frozenBy : null;
  const extra = [run.newCases ? `${run.newCases} new` : "", run.droppedCases ? `${run.droppedCases} dropped` : ""].filter(Boolean).join(", ");
  const canFreeze = run.status === "completed" && !!run.suiteId && !(suite?.baseline?.origin === "frozen" && suite.baseline.evalRunId === run.id);
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted">
      {run.suiteName && <span>suite <span className="text-ink-2">{run.suiteName}</span></span>}
      {run.baselineRef
        ? <span>compared <span className="tnum text-ink-2">{run.comparedCases ?? 0}</span> of <span className="tnum text-ink-2">{run.cases}</span> cases{extra ? ` (${extra})` : ""}</span>
        : <span><span className="tnum text-ink-2">{run.cases}</span> cases</span>}
      <span className="font-mono">{baselineProvenance(run, frozenBy)}</span>
      {canFreeze && (
        <GateButton permission="eval.govern" variant="ghost" size="sm" onClick={(ev) => { ev.stopPropagation(); void freezeBaseline(run.suiteId!, run.id); }}>
          Freeze as baseline
        </GateButton>
      )}
    </div>
  );
}

export function EvalRunDetail({ run }: { run: EvalRun }) {
  const isLive = run.baselineRef !== undefined;
  const loadingDetail = isLive && run.status === "completed" && run.dimensions === undefined && run.failingCases === undefined;
  const hasDetail = !!run.reason || !!run.dimensions?.length || !!run.failingCases?.length;
  return (
    <div className="space-y-3">
      {isLive && <LiveRunMeta run={run} />}
      {run.reason && <p className="max-w-[80ch] text-xs leading-relaxed text-ink-2">{run.reason}</p>}
      {loadingDetail && <p className="text-xs text-muted">Loading dimensions and failing cases…</p>}
      {run.dimensions && run.dimensions.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-[11px] font-medium uppercase tracking-wide text-muted">Dimensions vs baseline</p>
          {run.dimensions.map((d) => {
            const delta = d.baseline === null ? null : Math.round((d.score - d.baseline) * 10) / 10;
            const regressed = delta !== null && delta < 0;
            return (
              <div key={d.name} className="flex items-center gap-3 text-xs">
                <span className="w-28 shrink-0 truncate text-ink-2" title={d.name}>{d.name}</span>
                <Progress value={d.score} tone={regressed ? "critical" : "good"} className="max-w-xs flex-1" />
                <span className="w-20 shrink-0 text-right tnum text-muted">{d.score} / {d.baseline ?? "—"}</span>
                <span className={`w-12 shrink-0 text-right tnum ${regressed ? "text-critical" : "text-good"}`}>{delta === null ? "—" : `${delta > 0 ? "+" : ""}${delta}`}</span>
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


/** Max cases a synchronous live run accepts (O4). */
const LIVE_MAX_CASES = 25;
/** `agents` (Evaluations page) adds an agent picker; the per-agent form remounts on switch so its picks reset. */
export function LiveRunEvalDialog({ agent, agents, open, onClose }: { agent?: Agent; agents?: Agent[]; open: boolean; onClose: () => void }) {
  const [pickedId, setPickedId] = React.useState("");
  const current = agents?.find((a) => a.id === pickedId) ?? agent ?? agents?.[0];
  if (!current) return null;
  const picker = agents && agents.length > 0 ? (
    <Field label="Agent">
      <Select value={current.id} onChange={(e) => setPickedId(e.target.value)}>
        {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
      </Select>
    </Field>
  ) : null;
  return <AgentRunEvalDialog key={current.id} agent={current} picker={picker} open={open} onClose={onClose} />;
}

function AgentRunEvalDialog({ agent, picker, open, onClose }: { agent: Agent; picker: React.ReactNode; open: boolean; onClose: () => void }) {
  useEnsureEvals(agent.id, open);
  const policy = useEvalPolicyFor(agent.workspaceId);
  const versions = versionsOf(agent).filter((v) => !!v.id);
  const suites = useSuitesForAgent(agent.id);
  const active = versions.find((v) => v.isActive) ?? versions[0];
  const [versionId, setVersionId] = React.useState("");
  const [suiteId, setSuiteId] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [result, setResult] = React.useState<EvalRun | null>(null);
  const vid = versionId || active?.id || "";
  const suite = suites.find((x) => x.id === suiteId) ?? suites[0];
  const n = suite?.cases.length ?? 0;
  const blockedWhy = !vid ? "Save a version first." : !suite ? "Create a suite first." : n === 0 ? "This suite has no cases."
    : n > LIVE_MAX_CASES ? `Live runs take at most ${LIVE_MAX_CASES} cases — this suite has ${n}.` : "";

  async function submit() {
    if (blockedWhy || !suite) return;
    setBusy(true);
    try { const r = await runEvalLive(agent.id, vid, suite.id); if (r) setResult(r); } finally { setBusy(false); }
  }
  function close() { if (busy) return; setResult(null); onClose(); }
  const b = suite?.baseline;

  return (
    <Dialog
      open={open} onClose={close} size={result ? "lg" : undefined}
      title={result ? "Eval result" : "Run evaluation"}
      description={result ? undefined : "Runs the suite against a version with the LLM judge. The server chooses the baseline and applies the workspace eval policy."}
      footer={result
        ? <Button variant="primary" onClick={close}>Close</Button>
        : <>
            <Button variant="ghost" onClick={close} disabled={busy}>Cancel</Button>
            <Button variant="primary" onClick={() => void submit()} disabled={busy || !!blockedWhy}><FlaskConical className="size-4" /> {busy ? `Running ${n} case${n === 1 ? "" : "s"}…` : "Run eval"}</Button>
          </>}
    >
      {result ? <LiveRunResult run={result} /> : (
        <div className="space-y-4">
          {picker}
          <Field label="Version">
            <Select value={vid} onChange={(e) => setVersionId(e.target.value)} disabled={busy}>
              {versions.map((v) => <option key={v.id} value={v.id}>{v.label}{v.isActive ? " (active)" : ""}</option>)}
            </Select>
          </Field>
          <Field label="Eval suite" hint={blockedWhy || (n < policy.minCases ? `Below the ${policy.minCases}-case floor — expect a low-confidence verdict.` : undefined)}>
            {suites.length === 0 ? (
              <p className="rounded-md border border-border bg-elevated/40 px-3 py-2 text-xs text-muted">No suites yet. Add one under <span className="text-ink-2">Eval suites</span>.</p>
            ) : (
              <Select value={suite?.id ?? ""} onChange={(e) => setSuiteId(e.target.value)} disabled={busy}>
                {suites.map((x) => <option key={x.id} value={x.id}>{x.name} ({x.cases.length} cases)</option>)}
              </Select>
            )}
          </Field>
          <Field label="Baseline" hint="Chosen by the server: a frozen run, else the active version's latest run of this suite.">
            <div className="flex h-9 items-center rounded-md border border-border bg-elevated px-3 text-xs text-ink-2">
              {b?.origin === "frozen" ? `frozen${b.frozenBy ? ` by ${b.frozenBy}` : ""} · run ${b.evalRunId?.slice(0, 8)}`
                : b?.origin === "automatic" ? `automatic · run ${b.evalRunId?.slice(0, 8)}` : "none yet — this run will be insufficient evidence"}
            </div>
          </Field>
        </div>
      )}
    </Dialog>
  );
}

function LiveRunResult({ run }: { run: EvalRun }) {
  const fmt = (v: number, unit: string) => `${v > 0 ? "+" : ""}${Math.round(v * 10) / 10}${unit}`;
  const compared = !!run.baselineRef && run.status === "completed";
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {run.status === "failed" ? <Badge tone="critical">Failed</Badge> : <Badge tone={verdictTone[run.verdict]}>{verdictLabel[run.verdict]}</Badge>}
        <span className="font-mono text-xs text-ink-2">{run.version} vs {run.baseline}</span>
      </div>
      {run.status === "completed" && (
        <div className="grid grid-cols-3 gap-3 text-xs">
          <div><p className="text-muted">Quality</p><p className="tnum text-ink">{run.scored === false ? "—" : run.quality.toFixed(1)}{compared && run.scored !== false ? <span className="text-muted"> ({fmt(run.qualityDelta, "")})</span> : null}</p></div>
          <div><p className="text-muted">Cost Δ</p><p className={`tnum ${run.costDelta > 0 ? "text-critical" : "text-ink"}`}>{compared ? fmt(run.costDelta, "%") : "—"}</p></div>
          <div><p className="text-muted">Latency Δ</p><p className={`tnum ${run.latencyDelta > 0 ? "text-critical" : "text-ink"}`}>{compared ? fmt(run.latencyDelta, "ms") : "—"}</p></div>
        </div>
      )}
      <EvalRunDetail run={run} />
    </div>
  );
}

