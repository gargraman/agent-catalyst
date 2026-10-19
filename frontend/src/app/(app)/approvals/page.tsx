"use client";

import * as React from "react";
import Link from "next/link";
import { Check, ShieldCheck, X } from "lucide-react";
import {
  Card, CardHeader, CardTitle, CardContent, CardDescription, Badge, Table, THead, TH, TR, TD, EmptyState,
} from "@/components/ui";
import { Segmented } from "@/components/tabs";
import { StatTile } from "@/components/stat-tile";
import { GateButton } from "@/components/gate";
import { useApprovals, decideApproval } from "@/lib/store";
import { riskTone } from "@/lib/tools";
import { relative } from "@/lib/format";
import type { ToolApproval } from "@/lib/data";

const statusTone = (s: ToolApproval["status"]) =>
  s === "approved" ? "good" : s === "denied" ? "critical" : s === "expired" ? "warning" : "neutral";

/* @backend integration points (this page):
 *  - @backend GET /v1/approvals?workspace=:id - pending/decided tool-call approvals [via useApprovals]
 *  - @backend POST /v1/approvals/:id/decide - approve/deny; resumes or terminates the paused run
 *    server-side [via decideApproval]. Publisher+ (tool.approve). [ref: backend/08, backend/03 §2]
 */
export default function ApprovalsPage() {
  const approvals = useApprovals();
  const [filter, setFilter] = React.useState("pending");

  const pending = approvals.filter((a) => a.status === "pending");
  const rows = filter === "pending" ? pending : approvals;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">Approvals</h1>
        <p className="mt-1 text-sm text-muted">High-risk tool calls paused at the runtime approval gate. A publisher decides; the run resumes or stops.</p>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <StatTile label="Pending" value={String(pending.length)} hint="awaiting a decision" icon={<ShieldCheck className="size-4" />} />
        <StatTile label="Destructive pending" value={String(pending.filter((a) => a.riskTier === "destructive").length)} hint="highest risk" icon={<ShieldCheck className="size-4" />} />
        <StatTile label="Decided" value={String(approvals.filter((a) => a.status !== "pending").length)} hint="approved / denied" icon={<Check className="size-4" />} />
      </div>

      <Card>
        <CardHeader>
          <div><CardTitle>Tool-call approvals</CardTitle><CardDescription>Destructive and write-tier calls require a human decision before they run.</CardDescription></div>
          <Segmented options={[{ key: "pending", label: "Pending" }, { key: "all", label: "All" }]} value={filter} onChange={setFilter} />
        </CardHeader>
        <CardContent className="pt-0">
          {rows.length === 0 ? (
            <EmptyState icon={<ShieldCheck className="size-6" />} title={filter === "pending" ? "No pending approvals" : "No approvals yet"} hint="High-risk tool calls will appear here for a decision." />
          ) : (
            <Table>
              <THead><TR><TH>Agent</TH><TH>Tool</TH><TH>Risk</TH><TH>Arguments</TH><TH>Requested</TH><TH>Status</TH><TH></TH></TR></THead>
              <tbody>
                {rows.map((a) => (
                  <TR key={a.id}>
                    <TD><Link href={`/agents/${a.agentId}`} className="text-ink hover:underline">{a.agent}</Link></TD>
                    <TD><span className="font-mono text-xs text-ink-2">{a.toolLabel}</span></TD>
                    <TD><Badge tone={riskTone(a.riskTier)}>{a.riskTier}</Badge></TD>
                    <TD><span className="font-mono text-xs text-muted">{a.argsPreview}</span></TD>
                    <TD className="text-xs text-muted">{relative(a.requestedAt)}{a.requestedBy ? ` · ${a.requestedBy}` : ""}</TD>
                    <TD>
                      <Badge tone={statusTone(a.status)}>{a.status}</Badge>
                      {a.status !== "pending" && a.decidedBy && <span className="mt-0.5 block text-[11px] text-muted">by {a.decidedBy}</span>}
                    </TD>
                    <TD className="text-right">
                      {a.status === "pending" && (
                        <div className="flex items-center justify-end gap-1">
                          <GateButton permission="tool.approve" variant="outline" size="sm" onClick={() => decideApproval(a.id, "approve")}><Check className="size-3.5" /> Approve</GateButton>
                          <GateButton permission="tool.approve" variant="ghost" size="sm" onClick={() => decideApproval(a.id, "deny")} aria-label="Deny"><X className="size-4" /></GateButton>
                        </div>
                      )}
                    </TD>
                  </TR>
                ))}
              </tbody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
