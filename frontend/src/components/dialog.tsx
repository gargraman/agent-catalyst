"use client";

import * as React from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { Button } from "./ui";
import { cn } from "@/lib/utils";

export function Dialog({
  open, onClose, title, description, children, footer, size = "md",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  size?: "sm" | "md" | "lg";
}) {
  // Portal target only exists on the client; gate render so SSR emits nothing and hydration matches.
  // useSyncExternalStore returns false on the server and true on the client without a setState effect.
  const mounted = React.useSyncExternalStore(() => () => {}, () => true, () => false);

  React.useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [open, onClose]);

  if (!open || !mounted) return null;
  const width = size === "sm" ? "max-w-md" : size === "lg" ? "max-w-3xl" : "max-w-xl";

  // Portal to <body> so the fixed overlay escapes any transformed/overflow-clipped ancestor
  // (e.g. the Tabs content wrapper's `animate-in`), which would otherwise trap and clip it.
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto p-4 sm:p-6">
      <div className="fixed inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div
        role="dialog"
        aria-modal="true"
        className={cn("reveal card-elev relative z-10 my-8 w-full rounded-xl border border-border bg-surface", width)}
      >
        <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
          <div>
            <h2 className="text-sm font-semibold text-ink">{title}</h2>
            {description && <p className="mt-0.5 text-xs text-muted">{description}</p>}
          </div>
          <button onClick={onClose} className="text-muted transition-colors hover:text-ink" aria-label="Close">
            <X className="size-5" />
          </button>
        </div>
        <div className="max-h-[70vh] overflow-y-auto px-5 py-5">{children}</div>
        {footer && <div className="flex items-center justify-end gap-2 border-t border-border px-5 py-4">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}

export function ConfirmDialog({
  open, onClose, onConfirm, title, body, confirmLabel = "Confirm", tone = "danger",
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  body: React.ReactNode;
  confirmLabel?: string;
  tone?: "danger" | "primary";
}) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant={tone === "danger" ? "danger" : "primary"} onClick={() => { onConfirm(); onClose(); }}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="text-sm text-ink-2">{body}</div>
    </Dialog>
  );
}
