"use client";

/* The "why is publish blocked / why did it fail" surface.
   - EvalExplainer: for DRAFT agents with no eval yet. Explains what an eval is and
     offers to generate a starter set so a first-time builder is not stuck.
   - EvalFailureSummary / EvalFailingBanner: for EVAL_FAILING agents. Explains the
     specific regression (trigger, deltas, scored dimensions, example failing cases)
     and offers the correct remedies: roll back to the last passing version, or re-run
     the eval. Deliberately does NOT offer the starter-set path, which would mask the
     regression. */

import * as React from "react";
import { FlaskConical, RotateCcw, ShieldCheck, Sparkles, TriangleAlert } from "lucide-react";
import { Button, Progress } from "@/components/ui";
import { GateButton } from "@/components/gate";
import { Dialog } from "@/components/dialog";
import { Term } from "@/components/term";
import { addEvalRun, updateAgent, useEvalRunsForAgent, genId, nowIso, toast } from "@/lib/store";
import { usePrincipal } from "@/lib/auth";
import { canManage } from "@/lib/access-policy";
import type { Agent, EvalDimension, EvalRun } from "@/lib/data";

/* ------------------------------------------------------------------ helpers */

/** The run that explains the current failing state: the one for the active version,
    else the most recent failing run. */
export function pickFailingRun(evals: EvalRun[], agent: Agent): EvalRun | undefined {
  return evals.find((e) => e.version === agent.activeVersion) ?? evals.find((e) => e.verdict === "fail");
}

/** Accept the rollback: serve the last passing version and clear the failing state.
    evalPass is only overwritten when we actually have the baseline run's score. */
export function rollbackToBaseline(agent: Agent, baseline: string, evals: EvalRun[]) {
  const baselineQuality = evals.find((e) => e.version === baseline && e.verdict === "pass")?.quality;
  updateAgent(
    agent.id,
    {
      activeVersion: baseline,
      status: "published",
      drift: "none",
      ...(baselineQuality !== undefined ? { evalPass: baselineQuality } : {}),
    },
    false,
  );
  toast(`Rolled back to ${baseline}. The endpoint now serves the last passing version.`);
}

const fmtDelta = (n: number, unit: "pts" | "%" | "ms") =>
  `${n > 0 ? "+" : ""}${n}${unit === "pts" ? " pts" : unit}`;

/* -------------------------------------------------------------- starter set */

/** Mock: generate a small passing starter eval, record it, and unblock publishing.
    For DRAFT agents only (no eval yet). */
export function generateStarterEval(agent: Agent) {
  const quality = 92.5;
  addEvalRun({
    id: genId("ev"),
    agentId: agent.id,
    agent: agent.name,
    version: agent.activeVersion,
    baseline: "none",
    quality,
    qualityDelta: 0,
    costDelta: 0,
    latencyDelta: 0,
    cases: 12,
    verdict: "pass",
    date: nowIso(),
    trigger: "Starter set",
    reason: "Starter suite of 12 sensible cases. Establishes your first baseline.",
  });
  updateAgent(agent.id, { evalPass: quality, drift: "none" }, false);
  toast("Starter eval set generated and passed. You can publish now.");
}

export function EvalExplainer({ agent, onDone }: { agent: Agent; onDone?: () => void }) {
  return (
    <div className="space-y-4">
      <div className="flex gap-2.5 rounded-lg border border-border bg-elevated/60 px-3 py-2.5 text-xs leading-relaxed text-ink-2">
        <FlaskConical className="mt-0.5 size-3.5 shrink-0 text-accent" aria-hidden />
        <p className="max-w-[72ch]">
          An <Term id="eval">eval</Term> is an automated test suite: a set of example inputs with
          expected results that scores your agent&apos;s answers. It runs against a frozen{" "}
          <Term id="baseline">baseline</Term> so you can see whether a change helped or hurt before
          it reaches real users. Publishing needs a passing eval so a broken version never goes live.
        </p>
      </div>

      <div className="rounded-lg border border-border bg-surface p-3">
        <p className="flex items-center gap-1.5 text-sm font-medium text-ink">
          <Sparkles className="size-3.5 text-accent" /> Generate a starter eval set
        </p>
        <p className="mt-1 text-xs leading-relaxed text-ink-2">
          Creates a small suite of sensible test cases for this agent, runs it, and freezes the
          result as your baseline. You can edit or expand it afterward on the Evaluations tab.
        </p>
        <div className="mt-3 flex items-center gap-2">
          <GateButton
            permission="eval.run"
            variant="primary"
            size="sm"
            onClick={() => { generateStarterEval(agent); onDone?.(); }}
          >
            <FlaskConical className="size-4" /> Generate starter eval set
          </GateButton>
          <span className="inline-flex items-center gap-1 text-[11px] text-muted">
            <ShieldCheck className="size-3" /> 12 cases, runs in the background
          </span>
        </div>
      </div>
    </div>
  );
}

/* -------------------------------------------------------- failure surfaces */

function Stat({ label, value, tone }: { label: string; value: React.ReactNode; tone?: "good" | "bad" }) {
  return (
    <div className="rounded-lg border border-border bg-elevated px-2.5 py-1.5">
      <p className="text-[10px] uppercase tracking-wide text-muted">{label}</p>
      <p className={`truncate text-xs font-medium ${tone === "bad" ? "text-critical" : tone === "good" ? "text-good" : "text-ink"}`}>{value}</p>
    </div>
  );
}

function DimensionRow({ d }: { d: EvalDimension }) {
  const delta = Math.round((d.score - d.baseline) * 10) / 10;
  const regressed = delta < 0;
  return (
    <div className="flex items-center gap-3 text-xs">
      <span className="w-28 shrink-0 truncate text-ink-2" title={d.name}>{d.name}</span>
      <Progress value={d.score} tone={regressed ? "critical" : "good"} className="flex-1" />
      <span className="w-20 shrink-0 text-right tnum text-muted">{d.score}<span className="text-muted/70"> / {d.baseline}</span></span>
      <span className={`w-12 shrink-0 text-right tnum ${regressed ? "text-critical" : "text-good"}`}>{delta > 0 ? "+" : ""}{delta}</span>
    </div>
  );
}

/** Detailed failure card: trigger, regressions, scored dimensions, failing cases, remedies. */
export function EvalFailureSummary({ agent, onRunEval }: { agent: Agent; onRunEval?: () => void }) {
  const principal = usePrincipal();
  const manage = canManage(agent.accessPolicy, principal);
  const manageTitle = manage ? undefined : "Restricted by this agent's access policy";
  const evals = useEvalRunsForAgent(agent.id);
  const run = pickFailingRun(evals, agent);
  const baseline = run?.baseline && run.baseline !== "none" ? run.baseline : undefined;

  if (!run) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-ink-2">
          This version failed its latest eval, so the endpoint keeps serving the previous version.
          Fix the regression and re-run the eval.
        </p>
        {onRunEval && (
          <GateButton permission="eval.run" variant="primary" size="sm" onClick={onRunEval}>
            <FlaskConical className="size-4" /> Run eval
          </GateButton>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex gap-2.5 rounded-lg border border-critical/40 bg-critical/10 px-3 py-2.5 text-xs leading-relaxed text-ink-2">
        <TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-critical" aria-hidden />
        <div className="space-y-1">
          <p className="font-medium text-ink">{agent.activeVersion} failed its latest eval against {run.baseline}.</p>
          {run.reason && <p className="max-w-[72ch]">{run.reason}</p>}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="Trigger" value={run.trigger} />
        <Stat label="Quality" value={`${run.quality.toFixed(1)} (${fmtDelta(run.qualityDelta, "pts")})`} tone={run.qualityDelta < 0 ? "bad" : "good"} />
        <Stat label="Cost" value={fmtDelta(run.costDelta, "%")} tone={run.costDelta > 0 ? "bad" : "good"} />
        <Stat label="Latency" value={fmtDelta(run.latencyDelta, "ms")} tone={run.latencyDelta > 0 ? "bad" : "good"} />
      </div>

      {run.dimensions && run.dimensions.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-medium text-ink-2">Scored dimensions vs baseline</p>
          <div className="space-y-1.5">
            {run.dimensions.map((d) => <DimensionRow key={d.name} d={d} />)}
          </div>
        </div>
      )}

      {run.failingCases && run.failingCases.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-medium text-ink-2">Example failing cases</p>
          <ul className="space-y-1.5">
            {run.failingCases.map((c, i) => (
              <li key={i} className="rounded-lg border border-border bg-elevated/60 px-3 py-2 text-xs">
                <p className="font-medium text-ink-2">{c.input}</p>
                <p className="mt-0.5 text-muted">{c.issue}</p>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 pt-1">
        {baseline && (
          <GateButton permission="agent.publish" variant="primary" size="sm" disabled={!manage} title={manageTitle} onClick={() => rollbackToBaseline(agent, baseline, evals)}>
            <RotateCcw className="size-4" /> Roll back to {baseline}
          </GateButton>
        )}
        {onRunEval && (
          <GateButton permission="eval.run" variant="outline" size="sm" onClick={onRunEval}>
            <FlaskConical className="size-4" /> Run eval
          </GateButton>
        )}
        {baseline && <span className="text-[11px] text-muted">Roll back serves {baseline} while you fix {agent.activeVersion}.</span>}
      </div>
    </div>
  );
}

/** Compact above-the-fold banner for the failing state (detail page header area). */
export function EvalFailingBanner({ agent, onRunEval }: { agent: Agent; onRunEval?: () => void }) {
  const principal = usePrincipal();
  const manage = canManage(agent.accessPolicy, principal);
  const manageTitle = manage ? undefined : "Restricted by this agent's access policy";
  const evals = useEvalRunsForAgent(agent.id);
  const run = pickFailingRun(evals, agent);
  const baseline = run?.baseline && run.baseline !== "none" ? run.baseline : undefined;
  return (
    <div className="flex flex-wrap items-start gap-3 rounded-xl border border-critical/40 bg-critical/10 px-4 py-3">
      <TriangleAlert className="mt-0.5 size-4 shrink-0 text-critical" aria-hidden />
      <div className="min-w-0 flex-1 space-y-0.5">
        <p className="text-sm font-medium text-ink">
          {agent.activeVersion} failed its latest eval{baseline ? `, so the endpoint is serving ${baseline}` : ""}.
        </p>
        {run?.reason && <p className="text-xs leading-relaxed text-ink-2">{run.reason}</p>}
        <p className="text-[11px] text-muted">See the Evaluations tab for failing cases, or Publish &amp; Export for the full breakdown.</p>
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        {baseline && (
          <GateButton permission="agent.publish" variant="primary" size="sm" disabled={!manage} title={manageTitle} onClick={() => rollbackToBaseline(agent, baseline, evals)}>
            <RotateCcw className="size-4" /> Roll back to {baseline}
          </GateButton>
        )}
        {onRunEval && (
          <GateButton permission="eval.run" variant="outline" size="sm" onClick={onRunEval}>
            <FlaskConical className="size-4" /> Run eval
          </GateButton>
        )}
      </div>
    </div>
  );
}

export function PublishBlockedDialog({
  agent,
  open,
  onClose,
  onRunEval,
}: {
  agent: Agent;
  open: boolean;
  onClose: () => void;
  onRunEval?: () => void;
}) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Publishing needs a passing eval"
      description="Here is why this version is blocked and how to get unblocked."
      footer={<Button variant="ghost" onClick={onClose}>Close</Button>}
    >
      <EvalFailureSummary agent={agent} onRunEval={onRunEval} />
    </Dialog>
  );
}
