"use client";

/* Live-API status strip — the shared loading/error/source UI for every wired family (one component, reused as
   each family is wired). Renders nothing when the console is in pure mock mode (NEXT_PUBLIC_API_MODE unset). */

import * as React from "react";
import { Badge, Button } from "./ui";
import { LIVE_API } from "@/lib/api/client";
import { loadWorkspaces, useRemoteStatus, type RemoteStatus } from "@/lib/store";

function ErrorStrip({ status, what, onRetry }: { status: RemoteStatus; what: string; onRetry: () => void }) {
  const unreachable = status.httpStatus === 0 || (status.httpStatus ?? 0) >= 500;
  return (
    <div role="alert" className="flex flex-wrap items-center gap-2 rounded-md border border-critical/30 bg-critical/5 px-3 py-2 text-sm">
      <Badge tone="critical">{status.httpStatus ? `HTTP ${status.httpStatus}` : "Offline"}</Badge>
      <span className="text-ink-2">
        {unreachable ? `Couldn't reach the API to load ${what}.` : `Couldn't load ${what}: ${status.error}`}
        {status.code && !unreachable ? <span className="ml-1 text-muted">({status.code})</span> : null}
      </span>
      <Button size="sm" variant="ghost" onClick={onRetry}>Retry</Button>
    </div>
  );
}

export function LiveStatus({ live, status, onRetry, label, note }: {
  /** Is the active workspace sourced from the API for this family? */
  live: boolean;
  status: RemoteStatus;
  onRetry: () => void;
  /** Plural resource noun for messages, e.g. "agents". */
  label: string;
  /** Extra caveat shown next to the Live badge (e.g. which actions are still local-only). */
  note?: string;
}) {
  // Discovery decides whether a workspace is live at all — so its state must be read BEFORE claiming "mock":
  // a failed or in-flight discovery is NOT the same as "this workspace isn't linked".
  const discovery = useRemoteStatus("workspaces");
  if (!LIVE_API) return null;
  if (!live) {
    if (discovery.status === "error") {
      return <ErrorStrip status={discovery} what="your workspaces" onRetry={() => void loadWorkspaces({ force: true })} />;
    }
    if (discovery.status === "loading" || discovery.status === "idle") {
      return (
        <div className="flex items-center gap-2 text-xs text-muted" aria-live="polite">
          <Badge tone="neutral">Connecting</Badge> Discovering your workspaces…
        </div>
      );
    }
    return (
      <div className="flex items-center gap-2 text-xs text-muted">
        <Badge tone="warning">Mock data</Badge>
        This workspace isn&apos;t linked to a backend workspace — showing seed {label}.
      </div>
    );
  }
  if (status.status === "error") return <ErrorStrip status={status} what={label} onRetry={onRetry} />;
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-muted" aria-live="polite">
      <Badge tone="good">Live</Badge>
      {status.status === "loading" ? `Loading ${label}…` : `${label[0].toUpperCase()}${label.slice(1)} from the API`}
      {note ? <span className="text-warning">· {note}</span> : null}
    </div>
  );
}
