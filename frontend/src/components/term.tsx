"use client";

/* <Term id="eval"/> renders a glossary term with a dotted underline and a hover/focus
   tooltip carrying a plain-language definition. Keyboard accessible (tabbable, Escape-free
   since it's non-modal). Children override the displayed label; the definition comes from
   src/lib/glossary.ts. */

import * as React from "react";
import { GLOSSARY } from "@/lib/glossary";
import { cn } from "@/lib/utils";

export function Term({
  id,
  children,
  className,
}: {
  id: string;
  children?: React.ReactNode;
  className?: string;
}) {
  const entry = GLOSSARY[id];
  if (!entry) return <>{children ?? id}</>;
  return (
    <span className="group/term relative inline-flex">
      <span
        tabIndex={0}
        role="button"
        aria-describedby={`glossary-${id}`}
        className={cn(
          "cursor-help border-b border-dotted border-muted/70 outline-none focus-visible:border-accent",
          className,
        )}
      >
        {children ?? entry.term}
      </span>
      <span
        role="tooltip"
        id={`glossary-${id}`}
        className={cn(
          "pointer-events-none absolute left-0 top-full z-50 mt-1.5 w-64 rounded-lg border border-border bg-surface p-2.5 text-left text-xs leading-relaxed text-ink-2 opacity-0 shadow-lg transition-opacity duration-150",
          "group-hover/term:opacity-100 group-focus-within/term:opacity-100",
        )}
      >
        <span className="block font-medium text-ink">{entry.term}</span>
        <span className="mt-1 block font-normal normal-case tracking-normal">{entry.def}</span>
      </span>
    </span>
  );
}
