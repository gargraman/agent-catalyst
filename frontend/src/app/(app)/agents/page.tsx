"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Archive, ArchiveRestore, Bot, Copy, MessageSquare, MoreHorizontal, Pencil, Rocket, RotateCcw, Search, Trash2,
} from "lucide-react";
import {
  Badge, Button, Card, EmptyState, Input, PageHeader,
  Pagination, Progress, Select, StatusDot,
} from "@/components/ui";
import { Segmented } from "@/components/tabs";
import { ConfirmDialog } from "@/components/dialog";
import { GateButton } from "@/components/gate";
import { statusLabel, statusTone, type Agent, type AgentStatus } from "@/lib/data";
import { useAgents, updateAgent, deleteAgent, duplicateAgent, toast } from "@/lib/store";
import { useAuth, usePrincipal } from "@/lib/auth";
import { canUse } from "@/lib/access-policy";
import { num, relative } from "@/lib/format";

const FILTERS: { key: string; label: string }[] = [
  { key: "all", label: "All" },
  { key: "published", label: "Published" },
  { key: "draft", label: "Draft" },
  { key: "eval_failing", label: "Eval failing" },
  { key: "archived", label: "Archived" },
];

/* @backend integration points (this page):
 *  - @backend GET /v1/agents?workspace=:id - agent list [via useAgents]
 *  - @backend PATCH /v1/agents/:id - lifecycle: publish/unpublish/archive/restore [via updateAgent]
 *  - @backend POST /v1/agents/:id/duplicate - duplicate agent [via duplicateAgent]
 *  - @backend DELETE /v1/agents/:id - delete agent [via deleteAgent]
 *  - @backend GET /v1/search - search box Input (non-functional today)
 * See src/lib/store.ts markers + docs/backend-integration-map.md for the authoritative contracts.
 */
export default function AgentsPage() {
  const router = useRouter();
  const { can } = useAuth();
  const principal = usePrincipal();
  const [q, setQ] = React.useState("");
  const [status, setStatus] = React.useState("all");
  const [provider, setProvider] = React.useState("all");
  const [owner, setOwner] = React.useState("all");
  const [tag, setTag] = React.useState("all");
  const [page, setPage] = React.useState(1);
  const [pageSize, setPageSize] = React.useState(10);
  const [deleteTarget, setDeleteTarget] = React.useState<Agent | null>(null);

  // Build-time visibility: restricted agents the caller can't use are hidden from the list (Part B2).
  const agents = useAgents().filter((a) => canUse(a.accessPolicy, principal));

  // Filter options derived from what this caller can see (stable across selections).
  const providers = Array.from(new Set(agents.map((a) => a.provider))).sort();
  const owners = Array.from(new Set(agents.map((a) => a.owner))).sort();
  const tags = Array.from(new Set(agents.flatMap((a) => a.tags))).sort();

  const rows = agents.filter((a) => {
    const matchesStatus = status === "all" || a.status === (status as AgentStatus);
    const matchesProvider = provider === "all" || a.provider === provider;
    const matchesOwner = owner === "all" || a.owner === owner;
    const matchesTag = tag === "all" || a.tags.includes(tag);
    const needle = q.trim().toLowerCase();
    const matchesQ = !needle || a.name.toLowerCase().includes(needle) || a.description.toLowerCase().includes(needle) || a.tags.some((t) => t.toLowerCase().includes(needle));
    return matchesStatus && matchesProvider && matchesOwner && matchesTag && matchesQ;
  });

  // Pagination (clamp so a filter change that shrinks results never strands an out-of-range page).
  const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
  const safePage = Math.min(page, pageCount);
  const pageRows = rows.slice((safePage - 1) * pageSize, (safePage - 1) * pageSize + pageSize);

  // Any filter other than the status tabs is "active" (search + the three dropdowns).
  const hasActiveFilters = q.trim() !== "" || provider !== "all" || owner !== "all" || tag !== "all";
  function resetFilters() {
    setQ(""); setProvider("all"); setOwner("all"); setTag("all"); setPage(1);
  }

  function publish(a: Agent) {
    if (a.status === "eval_failing") { toast("Publishing is blocked: the active version failed its latest eval.", "critical"); return; }
    updateAgent(a.id, { status: "published" }, false);
    toast(`Published ${a.name}`);
  }

  return (
    <div className="space-y-6">
      <PageHeader title="Agents" description="Build, configure, test and publish LLM agents. Every version is an immutable, evaluated snapshot.">
        <GateButton permission="agent.create" variant="primary" href="/agents/new">New agent</GateButton>
      </PageHeader>

      <div className="space-y-3">
        <Segmented options={FILTERS} value={status} onChange={(v) => { setStatus(v); setPage(1); }} />
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-[220px] flex-1 sm:max-w-xs">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted" />
            <Input value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} placeholder="Search name, description, or tags" className="pl-9" />
          </div>
          <div className="w-40">
            <Select aria-label="Filter by provider" value={provider} onChange={(e) => { setProvider(e.target.value); setPage(1); }}>
              <option value="all">All providers</option>
              {providers.map((p) => <option key={p} value={p}>{p}</option>)}
            </Select>
          </div>
          <div className="w-44">
            <Select aria-label="Filter by owner" value={owner} onChange={(e) => { setOwner(e.target.value); setPage(1); }}>
              <option value="all">All owners</option>
              {owners.map((o) => <option key={o} value={o}>{o}</option>)}
            </Select>
          </div>
          <div className="w-40">
            <Select aria-label="Filter by tag" value={tag} onChange={(e) => { setTag(e.target.value); setPage(1); }}>
              <option value="all">All tags</option>
              {tags.map((t) => <option key={t} value={t}>{t}</option>)}
            </Select>
          </div>
          {hasActiveFilters && (
            <Button variant="ghost" size="sm" onClick={resetFilters}>Clear filters</Button>
          )}
        </div>
      </div>

      {rows.length === 0 ? (
        <EmptyState icon={<Bot className="size-6" />} title="No agents match your filters" hint="Try a different status, provider or owner, or clear the search." />
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {pageRows.map((a) => (
              <AgentCard
                key={a.id}
                agent={a}
                can={can}
                onPlayground={() => router.push(`/playground?agent=${a.id}`)}
                onEdit={() => router.push(`/agents/${a.id}`)}
                onDuplicate={() => { const id = duplicateAgent(a.id); if (id) router.push(`/agents/${id}`); }}
                onPublish={() => publish(a)}
                onUnpublish={() => updateAgent(a.id, { status: "draft" })}
                onArchive={() => updateAgent(a.id, { status: "archived" })}
                onRestore={() => updateAgent(a.id, { status: "draft" })}
                onDelete={() => setDeleteTarget(a)}
              />
            ))}
          </div>
          <Pagination
            page={safePage}
            pageSize={pageSize}
            total={rows.length}
            onPageChange={setPage}
            onPageSizeChange={(n) => { setPageSize(n); setPage(1); }}
          />
        </>
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => { if (deleteTarget) deleteAgent(deleteTarget.id); }}
        title="Delete agent"
        confirmLabel="Delete"
        body={<>Delete <span className="font-medium text-ink">{deleteTarget?.name}</span> and all its versions? This cannot be undone.</>}
      />
    </div>
  );
}

type RowActions = {
  agent: Agent;
  can: (p: import("@/lib/permissions").Permission) => boolean;
  onPlayground: () => void;
  onEdit: () => void; onDuplicate: () => void; onPublish: () => void; onUnpublish: () => void;
  onArchive: () => void; onRestore: () => void; onDelete: () => void;
};

function AgentCard(props: RowActions) {
  const a = props.agent;
  const archived = a.status === "archived";
  const extraTags = Math.max(0, a.tags.length - 2);
  return (
    <Card className="group relative flex flex-col gap-3 p-4 transition-colors hover:border-accent/40">
      {/* Whole tile is clickable; interactive controls sit above this via z-10. */}
      <Link href={`/agents/${a.id}`} aria-label={`Open ${a.name}`} className="absolute inset-0 rounded-xl" />

      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2.5">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-border bg-elevated text-ink-2">
            <Bot className="size-4" />
          </div>
          <div className="min-w-0">
            <p className="truncate font-medium text-ink group-hover:text-accent">{a.name}</p>
            <p className="truncate text-xs text-muted">{a.owner} · updated {relative(a.updatedAt)}</p>
          </div>
        </div>
        <div className="relative z-10 flex shrink-0 items-center gap-1">
          <Badge tone={statusTone[a.status]}><StatusDot tone={statusTone[a.status]} />{statusLabel[a.status]}</Badge>
          <RowMenu {...props} />
        </div>
      </div>

      <p className="line-clamp-2 min-h-[2.5rem] text-sm text-ink-2">{a.description}</p>

      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone="neutral">{a.provider}</Badge>
        <Badge tone="neutral">{a.activeVersion}</Badge>
        {a.tags.slice(0, 2).map((t) => <Badge key={t} tone="neutral">{t}</Badge>)}
        {extraTags > 0 && <span className="text-xs text-muted">+{extraTags}</span>}
      </div>

      <div className="mt-auto flex items-center justify-between gap-2 border-t border-border pt-3">
        {archived ? (
          <span className="text-xs text-muted">No recent activity</span>
        ) : (
          <>
            <div className="flex items-center gap-1.5" title="Latest eval pass rate">
              <Progress value={a.evalPass} tone={a.evalPass >= 90 ? "good" : a.evalPass >= 80 ? "warning" : "critical"} className="w-14" />
              <span className="tnum text-xs text-ink-2">{a.evalPass.toFixed(1)}%</span>
            </div>
            <span className="tnum text-xs text-muted">{num(a.calls30d, { compact: true })} calls (30d)</span>
          </>
        )}
      </div>
    </Card>
  );
}

function RowMenu({
  agent, can, onPlayground, onEdit, onDuplicate, onPublish, onUnpublish, onArchive, onRestore, onDelete,
}: RowActions) {
  const [open, setOpen] = React.useState(false);
  const item = "flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-ink-2 hover:bg-elevated hover:text-ink";
  function run(fn: () => void) { setOpen(false); fn(); }

  const canPlayground = can("playground.use");
  const canEdit = can("agent.edit");
  const canPublish = can("agent.publish");
  const canManageLifecycle = can("agent.delete"); // archive/restore/delete
  const hasManageActions = canEdit || canPublish || canManageLifecycle;
  // Archived agents can't be tested, so don't offer the Playground on them.
  const showPlayground = canPlayground && agent.status !== "archived";
  // Nothing actionable for this role → no menu at all.
  if (!showPlayground && !hasManageActions) return null;

  return (
    <div className="relative inline-block text-left">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-label="Agent actions"
        className="inline-flex size-8 items-center justify-center rounded-lg text-muted transition-colors hover:bg-elevated hover:text-ink"
      >
        <MoreHorizontal className="size-4" />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute right-0 z-20 mt-1 w-48 overflow-hidden rounded-lg border border-border bg-surface py-1 card-elev">
            {showPlayground && <button className={item} onClick={() => run(onPlayground)}><MessageSquare className="size-4" /> Open in Playground</button>}
            {showPlayground && hasManageActions && <div className="my-1 h-px bg-border" />}
            {canEdit && <button className={item} onClick={() => run(onEdit)}><Pencil className="size-4" /> Edit</button>}
            {canEdit && <button className={item} onClick={() => run(onDuplicate)}><Copy className="size-4" /> Duplicate</button>}
            {canPublish && (agent.status === "published"
              ? <button className={item} onClick={() => run(onUnpublish)}><RotateCcw className="size-4" /> Unpublish</button>
              : agent.status !== "archived" && <button className={item} onClick={() => run(onPublish)}><Rocket className="size-4" /> Publish</button>)}
            {canManageLifecycle && (agent.status === "archived"
              ? <button className={item} onClick={() => run(onRestore)}><ArchiveRestore className="size-4" /> Restore</button>
              : <button className={item} onClick={() => run(onArchive)}><Archive className="size-4" /> Archive</button>)}
            {canManageLifecycle && <>
              <div className="my-1 h-px bg-border" />
              <button className={`${item} text-critical hover:text-critical`} onClick={() => run(onDelete)}><Trash2 className="size-4" /> Delete</button>
            </>}
          </div>
        </>
      )}
    </div>
  );
}
