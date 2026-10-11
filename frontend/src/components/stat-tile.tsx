import * as React from "react";
import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import { Sparkline } from "./charts";
import { cn } from "@/lib/utils";

export function StatTile({
  label, value, delta, deltaGood, hint, spark, sparkTone, icon,
}: {
  label: string;
  value: string;
  delta?: string;
  deltaGood?: boolean;
  hint?: string;
  spark?: number[];
  sparkTone?: string;
  icon?: React.ReactNode;
}) {
  return (
    <div className="card-elev rounded-xl border border-border bg-surface p-5">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-muted">{label}</span>
        {icon && <span className="inline-flex size-8 items-center justify-center rounded-lg bg-elevated text-ink-2">{icon}</span>}
      </div>
      <div className="mt-3 flex items-end justify-between gap-3">
        <div>
          <div className="text-[26px] font-semibold leading-none tracking-tight text-ink tnum">{value}</div>
          <div className="mt-2 flex items-center gap-2">
            {delta && (
              <span className={cn("inline-flex items-center gap-0.5 rounded-md px-1.5 py-0.5 text-xs font-medium", deltaGood ? "bg-good/10 text-good" : "bg-critical/10 text-critical")}>
                {delta.trim().startsWith("-") ? <ArrowDownRight className="size-3" /> : <ArrowUpRight className="size-3" />}
                <span className="tnum">{delta}</span>
              </span>
            )}
            {hint && <span className="text-xs text-muted">{hint}</span>}
          </div>
        </div>
        {spark && <Sparkline values={spark} tone={sparkTone} />}
      </div>
    </div>
  );
}
