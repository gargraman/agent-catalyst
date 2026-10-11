import * as React from "react";
import { Check } from "lucide-react";
import { cn } from "@/lib/utils";

export function Stepper({ steps, current }: { steps: string[]; current: number }) {
  return (
    <ol className="flex items-center gap-2">
      {steps.map((label, i) => {
        const done = i < current;
        const active = i === current;
        return (
          <React.Fragment key={label}>
            <li className="flex items-center gap-2">
              <span
                className={cn(
                  "inline-flex size-6 items-center justify-center rounded-full border text-xs font-semibold transition-colors",
                  done && "border-accent bg-accent text-accent-ink",
                  active && "border-accent bg-accent-soft text-accent",
                  !done && !active && "border-border bg-elevated text-muted",
                )}
              >
                {done ? <Check className="size-3.5" /> : i + 1}
              </span>
              <span className={cn("text-sm font-medium", active || done ? "text-ink" : "text-muted")}>{label}</span>
            </li>
            {i < steps.length - 1 && <span className={cn("h-px w-6 sm:w-10", done ? "bg-accent" : "bg-border")} />}
          </React.Fragment>
        );
      })}
    </ol>
  );
}
