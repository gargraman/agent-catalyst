"use client";

/* Reusable info callout stating the core access principle in one or two lines
   (Part D). Variants let each surface say the relevant half without re-writing it. */

import { Info } from "lucide-react";
import { cn } from "@/lib/utils";
import { ORG } from "@/lib/org";

const COPY = {
  build: "What you can see here is build-time visibility. Binding a tool or knowledge base lets the agent use it only if the caller is also granted it at run time.",
  run: "Run-time access to tools, knowledge and MCP is governed by the running user's token and tenant, not by whoever built or published the agent.",
  both: "Build-time visibility is not run-time access. A builder can bind any resource they can see, but at run time it is used only if the caller's token, tenant and the resource's access policy all allow it.",
  export: `Callers authenticate with their own ${ORG.idpName} token (usually a service account). Access to tools and knowledge is scoped to that token and its tenant; the LLM provider key stays the workspace's vaulted key.`,
} as const;

export function AccessNote({
  variant = "both",
  className,
}: {
  variant?: keyof typeof COPY;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex gap-2.5 rounded-lg border border-border bg-elevated/60 px-3 py-2.5 text-xs leading-relaxed text-ink-2",
        className,
      )}
    >
      <Info className="mt-0.5 size-3.5 shrink-0 text-accent" aria-hidden />
      <p className="max-w-[72ch]">{COPY[variant]}</p>
    </div>
  );
}
