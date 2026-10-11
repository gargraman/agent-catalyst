"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

export type TabItem = { key: string; label: string; icon?: React.ReactNode; content: React.ReactNode };

export function Tabs({ tabs, initial, className }: { tabs: TabItem[]; initial?: string; className?: string }) {
  const [active, setActive] = React.useState(initial ?? tabs[0]?.key);
  const current = tabs.find((t) => t.key === active) ?? tabs[0];
  return (
    <div className={className}>
      <div className="flex gap-1 overflow-x-auto border-b border-border">
        {tabs.map((t) => {
          const on = t.key === active;
          return (
            <button
              key={t.key}
              onClick={() => setActive(t.key)}
              className={cn(
                "relative flex items-center gap-1.5 whitespace-nowrap px-3 py-2.5 text-sm font-medium transition-colors",
                on ? "text-ink" : "text-muted hover:text-ink-2",
              )}
            >
              {t.icon}
              {t.label}
              {on && <span className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-accent" />}
            </button>
          );
        })}
      </div>
      <div className="pt-6 animate-in">{current?.content}</div>
    </div>
  );
}

/** Lightweight segmented control for in-panel view switches. */
export function Segmented({
  options, value, onChange, className,
}: {
  options: { key: string; label: string }[]; value: string; onChange: (k: string) => void; className?: string;
}) {
  return (
    <div className={cn("inline-flex rounded-lg border border-border bg-elevated p-0.5", className)}>
      {options.map((o) => (
        <button
          key={o.key}
          onClick={() => onChange(o.key)}
          className={cn(
            "rounded-md px-3 py-1 text-xs font-medium transition-colors",
            value === o.key ? "bg-surface text-ink shadow-sm" : "text-muted hover:text-ink-2",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
