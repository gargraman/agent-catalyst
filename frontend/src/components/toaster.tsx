"use client";

import { Check, Info, X, AlertTriangle } from "lucide-react";
import { useToasts, dismissToast } from "@/lib/store";
import { cn } from "@/lib/utils";

export function Toaster() {
  const toasts = useToasts();
  if (toasts.length === 0) return null;
  return (
    <div className="fixed bottom-4 right-4 z-[60] flex w-80 flex-col gap-2">
      {toasts.map((t) => {
        const Icon = t.tone === "good" ? Check : t.tone === "critical" ? AlertTriangle : Info;
        const tone = t.tone === "good" ? "text-good" : t.tone === "critical" ? "text-critical" : "text-accent";
        return (
          <div key={t.id} className="reveal card-elev flex items-start gap-3 rounded-lg border border-border bg-elevated px-4 py-3">
            <Icon className={cn("mt-0.5 size-4 shrink-0", tone)} />
            <p className="flex-1 text-sm text-ink-2">{t.message}</p>
            <button onClick={() => dismissToast(t.id)} className="text-muted hover:text-ink"><X className="size-4" /></button>
          </div>
        );
      })}
    </div>
  );
}
